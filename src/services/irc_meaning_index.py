"""
IRC Meaning Index - Searches the old IRC log by what was talked about, not exact words.

The log is split into conversations (:meth:`IrcLog.conversations`) and each one is
turned into a vector with Gemini's embedding model. A search embeds the query the same
way and returns the conversations whose vectors are closest, so "the time someone's
truck got towed" finds a conversation that only says "they hauled my ranger".

Conversations are embedded with every speaker under their canonical name from
``IRC_NICKS`` (so a person's aliases all read as one name, and "what did Jobe say about X"
matches whichever nick they used), and lines from nicks it drops (bots) are left out.

Only the embedding happens at Google; the vectors come back and are kept next to the log
(``<IRC_LOG>.meaning.npz``, about 50MB for an 80MB log). It's built in the background
when the bot starts, in batches, saving as it goes, so a restart picks up where it left
off. It's rebuilt when the log or the nicks file changes. Building costs about $0.20 per million tokens
(~$4 for an 80MB log); a search costs a fraction of a cent.
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, List, Optional, Sequence, Tuple

import numpy as np

from src.services.cost_service import GEMINI_DIRECT, record_cost
from src.services.irc_log_service import IrcLog

logger = logging.getLogger(__name__)

EMBED_MODEL = "gemini-embedding-2"
EMBED_DIMS = 768
# USD per 1M input tokens; Gemini doesn't report embedding tokens, so ~4 characters each.
PRICE_EMBED_PER_M = 0.20
CHARS_PER_TOKEN = 4
BATCH_SIZE = 100
CONCURRENT_BATCHES = 4
# Save progress after this many batches, so a restart loses little.
SAVE_EVERY_BATCHES = 20
# How many of the closest conversations are looked at before filtering by nick or date.
CANDIDATES = 400
MAX_RESULTS = 10
# Retry a failed batch this many times (rate limits), then stop the build until next start.
BATCH_RETRIES = 4


def _document(label: str, lines: Sequence[str]) -> str:
    """How a conversation is embedded (gemini-embedding-2 takes the task as a text prefix)."""
    return f"title: IRC chat on {label} | text: " + "\n".join(lines)


def _query(text: str) -> str:
    return f"task: search result | query: {text}"


@dataclass
class _Spans:
    starts: np.ndarray
    ends: np.ndarray
    days: np.ndarray  # date ordinals, 0 when unknown


class IrcMeaningIndex:
    """Conversation vectors for an :class:`IrcLog`, built with Gemini and searched locally."""

    def __init__(self, log: IrcLog, gemini_service: Any, path: Optional[str] = None):
        self._log = log
        self._gemini = gemini_service
        self.path = path or f"{log.path}.meaning.npz"
        self._spans: Optional[_Spans] = None
        self._vectors: Optional[np.ndarray] = None
        self._built = 0
        self._task: Optional[asyncio.Task] = None

    # -- status -----------------------------------------------------------------

    @property
    def total(self) -> int:
        return 0 if self._spans is None else len(self._spans.starts)

    @property
    def ready(self) -> bool:
        return self._spans is not None and self.total > 0 and self._built >= self.total

    def progress(self) -> str:
        if self._spans is None:
            return "not started"
        return f"{self._built:,} of {self.total:,} conversations ({100 * self._built // max(self.total, 1)}%)"

    # -- building ---------------------------------------------------------------

    def start(self) -> None:
        """Load or build the index in the background, once."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._build())

    def _signature(self) -> dict:
        stat = os.stat(self._log.path)
        nicks = self._log.nicks.signature if self._log.nicks else ""
        return {"size": stat.st_size, "mtime": stat.st_mtime, "nicks": nicks, "model": EMBED_MODEL, "dims": EMBED_DIMS}

    def _load_saved(self, signature: dict) -> bool:
        """Load a saved index for this exact log; False if there's none or it's for another version."""
        if not os.path.exists(self.path):
            return False
        try:
            with np.load(self.path) as saved:
                if json.loads(str(saved["meta"])) != signature:
                    logger.info("[irc meaning] The log changed since the index was built; rebuilding it")
                    return False
                self._spans = _Spans(saved["starts"], saved["ends"], saved["days"])
                self._vectors = saved["vectors"].astype(np.float32)
                self._built = int(saved["built"])
        except Exception as e:
            logger.warning(f"[irc meaning] Couldn't read {self.path} ({e}); rebuilding it")
            return False
        return True

    def _save(self, signature: dict) -> None:
        assert self._spans is not None and self._vectors is not None
        tmp = f"{self.path}.tmp.npz"
        np.savez(
            tmp, meta=json.dumps(signature), starts=self._spans.starts, ends=self._spans.ends,
            days=self._spans.days, vectors=self._vectors.astype(np.float16), built=self._built,
        )
        os.replace(tmp, self.path)

    async def _build(self) -> None:
        started = time.monotonic()
        try:
            signature = await asyncio.to_thread(self._signature)
            if not await asyncio.to_thread(self._load_saved, signature):
                spans = await asyncio.to_thread(self._log.conversations)
                self._spans = _Spans(
                    np.array([s for s, _, _ in spans], dtype=np.int64),
                    np.array([e for _, e, _ in spans], dtype=np.int64),
                    np.array([d.toordinal() if d else 0 for _, _, d in spans], dtype=np.int32),
                )
                self._vectors = np.zeros((len(spans), EMBED_DIMS), dtype=np.float32)
                self._built = 0
            if self.ready:
                logger.info(f"[irc meaning] Loaded {self.total:,} conversations from {self.path}")
                return
            logger.info(f"[irc meaning] Embedding the log: {self.progress()} done so far")
            await self._embed_remaining(signature)
            logger.info(f"[irc meaning] Built {self.total:,} conversations in {time.monotonic() - started:.0f}s")
        except Exception:
            logger.exception(f"[irc meaning] Building the index stopped at {self.progress()}")

    async def _embed_remaining(self, signature: dict) -> None:
        assert self._spans is not None and self._vectors is not None
        cost = 0.0
        batches_since_save = 0
        while self._built < self.total:
            # A few batches at once; they all land before the next group starts, so
            # ``_built`` is always a clean prefix to save and resume from.
            group = [
                (start, min(start + BATCH_SIZE, self.total))
                for start in range(self._built, min(self._built + BATCH_SIZE * CONCURRENT_BATCHES, self.total),
                                   BATCH_SIZE)
            ]
            results = await asyncio.gather(*(self._embed_batch(a, b) for a, b in group))
            for (a, b), (vectors, batch_cost) in zip(group, results, strict=True):
                self._vectors[a:b] = vectors
                cost += batch_cost
            self._built = group[-1][1]
            batches_since_save += len(group)
            if batches_since_save >= SAVE_EVERY_BATCHES or self._built >= self.total:
                await asyncio.to_thread(self._save, signature)
                batches_since_save = 0
                logger.info(f"[irc meaning] {self.progress()} embedded, ${cost:.2f} so far this run")

    async def _embed_batch(self, a: int, b: int) -> Tuple[np.ndarray, float]:
        assert self._spans is not None
        spans = self._spans

        def documents() -> List[str]:
            return [_document(*self._log.excerpt(int(spans.starts[i]), int(spans.ends[i]))) for i in range(a, b)]

        texts = await asyncio.to_thread(documents)
        for attempt in range(BATCH_RETRIES + 1):
            try:
                vectors = await self._embed(texts)
                break
            except Exception as e:
                if attempt == BATCH_RETRIES:
                    raise
                delay = 2 ** attempt * 5
                logger.warning(f"[irc meaning] Embedding batch failed ({e}); retrying in {delay}s")
                await asyncio.sleep(delay)
        cost = sum(len(t) for t in texts) / CHARS_PER_TOKEN * PRICE_EMBED_PER_M / 1_000_000
        await record_cost(GEMINI_DIRECT, cost)
        return vectors, cost

    async def _embed(self, texts: List[str]) -> np.ndarray:
        from google.genai import types

        response = await self._gemini._get_client().aio.models.embed_content(
            model=EMBED_MODEL, contents=texts, config=types.EmbedContentConfig(output_dimensionality=EMBED_DIMS))
        vectors = np.array([e.values for e in response.embeddings], dtype=np.float32)
        # gemini-embedding-2 normalizes truncated vectors; this keeps it right if that changes.
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.where(norms == 0, 1, norms)

    # -- searching --------------------------------------------------------------

    async def search(
        self, query: str, *, nick: str = "", after: Optional[date] = None, before: Optional[date] = None,
        limit: int = 6,
    ) -> str:
        """The conversations closest in meaning to ``query``, formatted with their dates."""
        if not self.ready:
            return (f"The meaning search is still being built ({self.progress()}). Use search_irc_log "
                    "with keywords for now.")
        assert self._spans is not None and self._vectors is not None
        limit = max(1, min(limit, MAX_RESULTS))
        q = (await self._embed([_query(query)]))[0]
        await record_cost(GEMINI_DIRECT, len(query) / CHARS_PER_TOKEN * PRICE_EMBED_PER_M / 1_000_000)
        scores = self._vectors @ q
        mask: np.ndarray = np.ones(len(scores), dtype=bool)
        if after:
            mask &= self._spans.days >= after.toordinal()
        if before:
            mask &= (self._spans.days > 0) & (self._spans.days <= before.toordinal())
        scores = np.where(mask, scores, -np.inf)
        count = min(CANDIDATES, int(mask.sum()))
        if count == 0:
            return "No conversations in that date range."
        top = np.argpartition(-scores, count - 1)[:count]
        top = top[np.argsort(-scores[top])]
        blocks = []
        for i in top:
            start, end = int(self._spans.starts[i]), int(self._spans.ends[i])
            if nick and not await asyncio.to_thread(self._log.speaks_in, start, end, nick):
                continue
            label, lines = await asyncio.to_thread(self._log.excerpt, start, end)
            blocks.append(f"[{label}] (match {float(scores[i]):.2f})\n" + "\n".join(lines))
            if len(blocks) >= limit:
                break
        if not blocks:
            return f"No conversations like that{' with ' + nick if nick else ''}."
        return (f"The {len(blocks)} conversations closest in meaning to {query!r}, best first:\n\n"
                + "\n\n".join(blocks))
