"""
Gemini Service - Handles Google Gemini API interactions via the Interactions API.

Uses the google-genai SDK's Interactions API (client.aio.interactions.create) for chat
replies (streamed, with Google's built-in tools and the bot's own client tools) and for
watching videos, and GenerateContent for the image models.
"""

import asyncio
import base64
import json
import logging
import os
import time
import warnings
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import discord
import httpx

from src.db.state_store import StateStore
from src.services.cost_service import GEMINI_DIRECT, note_stat, record_cost
from src.services.image_service import ImageModel, ImageOptions, ImageResult
from src.services.tool_runner import (
    CODE_STATUS, MAX_TOOL_ROUNDS, READING_LINKS_STATUS, SEARCH_RESULTS_STATUS, THINKING_STATUS, WATCHING_STATUS,
    WEB_SEARCH_STATUS, ChatTool, ToolResult, run_tool_call,
)
from src.telemetry import image_links_ready
from src.utils.message_utils import clean_reply, resolve_bot_id

logger = logging.getLogger(__name__)

# Suppress known google-genai SDK warnings that are expected/harmless
warnings.filterwarnings("ignore", message="Interactions usage is experimental")
warnings.filterwarnings("ignore", message="Async interactions client cannot use aiohttp")


@dataclass(frozen=True)
class GeminiModel:
    api_id: str
    # USD per 1M tokens (paid tier). Output includes thinking tokens.
    price_input: float
    price_output: float
    price_cached: float
    # The thinking level chat replies run at, and the levels the model takes.
    thinking_level: str
    thinking_levels: Tuple[str, ...]
    # Launch pricing: every rate is multiplied by ``intro_factor`` until ``intro_ends``.
    intro_ends: Optional[date] = None
    intro_factor: float = 1.0

    def rates(self, today: Optional[date] = None) -> Tuple[float, float, float]:
        """``(input, output, cached)`` per 1M tokens on ``today``."""
        factor = self.intro_factor if self.intro_ends and (today or date.today()) < self.intro_ends else 1.0
        return self.price_input * factor, self.price_output * factor, self.price_cached * factor


# Bot-facing model name -> Gemini API model. The thinking levels mirror the Claude models'
# efforts: the mid-size model at medium, the big one at low so it doesn't overthink chat.
GEMINI_MODELS: Dict[str, GeminiModel] = {
    "hydrogen": GeminiModel(
        "gemini-4-hydrogen", 1.50, 7.50, 0.075, "medium", ("low", "medium", "high", "xhigh")),
    # Half price until December 2026.
    "argon": GeminiModel(
        "gemini-4-argon", 4.00, 20.00, 0.20, "low", ("low", "medium", "high"),
        intro_ends=date(2026, 12, 1), intro_factor=0.5),
}
# Watches videos for the Claude models' ``summarize_youtube`` and ``watch_video`` tools.
VIDEO_MODEL = GEMINI_MODELS["hydrogen"]

# Google's own tools, run by the API. Search and URL reading go with every reply that may
# search; code execution only with the bot's client tools (mention replies).
SEARCH_TOOLS = [{"type": "google_search"}, {"type": "url_context"}]
CODE_EXECUTION_TOOL = {"type": "code_execution"}
# What the reply preview says when the model starts a step of this type.
STEP_STATUSES = {
    "thought": THINKING_STATUS,
    "google_search_call": WEB_SEARCH_STATUS,
    "google_search_result": SEARCH_RESULTS_STATUS,
    "url_context_call": READING_LINKS_STATUS,
    "code_execution_call": CODE_STATUS,
    # Agentic video: the model loading parts of a video a tool handed it.
    "processing_call": WATCHING_STATUS,
}
# Image types a function result can carry; others (GIFs) are described in text instead.
RESULT_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}

# A long YouTube video is ~100 tokens per second at low media resolution, so an hour is
# ~360k tokens; agentic processing loads far less of a long one.
VIDEO_TIMEOUT_SECONDS = 900
# Uploaded videos (watch_video) are processed by the Files API before use.
FILE_READY_TIMEOUT_SECONDS = 600
FILE_POLL_SECONDS = 3.0
INTERACTION_POLL_SECONDS = 5.0
VIDEO_SUMMARY_PROMPT = (
    "Watch this video and write a summary for people in a group chat who won't watch it. Cover: what "
    "it is (title and channel if shown); the main points or story in order, with rough [mm:ss] timestamps; "
    "notable quotes, word for word; anything shown on screen that matters, not just what's said; and how it "
    "ends or what it concludes. Scale the length to the video: a few sentences for a short clip, up to about "
    "800 words for a long one. Plain text, no preamble, and don't make up anything you didn't see or hear."
)

# How long a channel keeps continuing from its last interaction (seconds). An hour, like the
# Claude models' prompt cache: within it a mention sends only what's new, and Gemini's
# implicit cache (the only kind Gemini 4 has) can discount the history it already read.
INTERACTION_CACHE_TTL = 3600

# Client-side request timeout (seconds); thinking and web search need far more than an
# HTTP library's default. Override with GEMINI_TIMEOUT_SECONDS.
try:
    REQUEST_TIMEOUT_SECONDS = float(os.environ.get("GEMINI_TIMEOUT_SECONDS", "120"))
except ValueError:
    REQUEST_TIMEOUT_SECONDS = 120.0
MAX_ATTEMPTS = 3
RETRY_BASE_SECONDS = 1.0


class GeminiServiceError(Exception):
    """Custom exception for Gemini service errors."""


class _StreamError(Exception):
    """An ``error`` event in a reply's stream; ``code`` is its HTTP-style status, when it has one."""

    def __init__(self, message: str, code: Optional[int] = None):
        super().__init__(message)
        self.code = code


def _interaction_text(interaction: Any) -> str:
    """The text an interaction ended with (from its last step, or its outputs)."""
    try:
        data = interaction.model_dump()
    except AttributeError:
        data = vars(interaction)
    if data.get("steps"):
        items = data["steps"][-1].get("content") or []
    else:
        items = data.get("outputs") or []
    return "\n".join(
        item.get("text", "") for item in items if isinstance(item, dict) and item.get("type") == "text"
    ).strip()


def _is_retryable(error: Exception) -> bool:
    """Transient failures only: rate limits, server errors, timeouts, dropped connections."""
    from google.genai import errors as genai_errors
    if isinstance(error, genai_errors.APIError):
        return error.code == 429 or (error.code or 0) >= 500
    if isinstance(error, _StreamError):
        # Mid-stream failures are mostly the server's ("Deadline expired"); only a plain 4xx is ours.
        return error.code is None or error.code == 429 or error.code >= 500
    return isinstance(error, (httpx.TransportError, asyncio.TimeoutError, ConnectionError))


def function_tool(definition: Dict[str, Any]) -> Dict[str, Any]:
    """A client tool's Messages API definition as an Interactions API function tool."""
    return {
        "type": "function",
        "name": definition["name"],
        "description": definition.get("description", ""),
        "parameters": definition.get("input_schema") or {"type": "object", "properties": {}},
    }


def function_result(call: Dict[str, Any], result: ToolResult) -> Dict[str, Any]:
    """``result`` as the Interactions API's answer to ``call``.

    Text and video blocks go as they are; a Messages API image block becomes an image the
    model can see, or a line of text when it's a type function results don't take.
    """
    content: List[Dict[str, Any]] = []
    for block in result.content:
        kind = block.get("type")
        if kind == "text":
            content.append({"type": "text", "text": block.get("text", "")})
        elif kind == "video":
            content.append(block)
        elif kind == "image":
            source = block.get("source") or {}
            mime_type = source.get("media_type", "")
            if source.get("type") == "url":
                content.append({"type": "image", "uri": source.get("url", "")})
            elif mime_type in RESULT_IMAGE_TYPES:
                content.append({"type": "image", "mime_type": mime_type, "data": source.get("data", "")})
            else:
                content.append({"type": "text", "text": f"[An image ({mime_type or 'unknown type'}) that can't be shown here.]"})
    entry: Dict[str, Any] = {
        "type": "function_result", "call_id": call["id"], "name": call["name"],
        "result": content or [{"type": "text", "text": "(no output)"}],
    }
    if result.is_error:
        entry["is_error"] = True
    return entry


def _usage_tokens(usage: Any, name: str) -> int:
    return (usage.get(name) if isinstance(usage, dict) else getattr(usage, name, 0)) or 0


# Each channel's latest interaction, per model, in Redis so a restart can continue the
# conversation. Per model so a channel that switches to another model and back within the
# hour continues (and reads the implicit cache of) the conversation that model already had.
CONTEXT_KEY = "context:gemini"
# (channel ID, bot-facing model name)
Slot = Tuple[int, str]
# How much of a reply is kept to recognise it in the history later.
REPLY_HEAD_CHARS = 200


def _reply_head(text: str) -> str:
    """The start of a reply with its whitespace evened out, for comparing."""
    return " ".join(text.split())[:REPLY_HEAD_CHARS]


def _entry_text(message: Dict[str, Any]) -> str:
    """A history entry's text without the ``[timestamp] [message ID] <@sender>:`` header line."""
    content = message.get("content", "")
    if isinstance(content, list):
        content = "\n".join(
            p if isinstance(p, str) else p.get("text", "") for p in content
            if isinstance(p, str) or (isinstance(p, dict) and p.get("type") == "text"))
    _header, _, body = str(content).partition("\n")
    return body


class GeminiService:
    """Service for handling Google Gemini Interactions API calls."""

    def __init__(self, api_key: Optional[str] = None, *, context_store: Optional[StateStore] = None):
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self._client = None
        self._bot_id_loader = None
        # slot -> {"interaction_id", "timestamp", "prompt_tokens", "seen_message_id", "reply_head"}
        self._interaction_cache: Dict[Slot, Dict[str, Any]] = {}
        self._context_store = context_store or StateStore(None)
        for field, saved in self._context_store.get_all(CONTEXT_KEY).items():
            channel, _, model = str(field).partition(":")
            # An entry without a model was saved before chains were kept per model; it expires on its own.
            if model and channel.isdigit() and isinstance(saved, dict) and saved.get("interaction_id"):
                self._interaction_cache[(int(channel), model)] = {
                    "interaction_id": str(saved["interaction_id"]),
                    "timestamp": float(saved.get("timestamp") or 0),
                    "prompt_tokens": int(saved.get("prompt_tokens") or 0),
                    "seen_message_id": int(saved.get("seen_message_id") or 0),
                    "reply_head": str(saved.get("reply_head") or ""),
                }

    def set_bot_id_loader(self, bot_id_loader):
        """Set a callable that returns the current bot user ID."""
        self._bot_id_loader = bot_id_loader

    def _chain(self, channel_id: Optional[int], model: str) -> Optional[Dict[str, Any]]:
        """What's remembered of ``model``'s conversation in the channel, if it can still be continued."""
        if not channel_id:
            return None
        slot = (channel_id, model)
        cached = self._interaction_cache.get(slot)
        if not cached:
            return None
        if time.time() - cached["timestamp"] > INTERACTION_CACHE_TTL:
            del self._interaction_cache[slot]
            logger.info("[gemini] %s's conversation in channel %s expired", model, channel_id)
            return None
        return cached

    def _get_previous_interaction_id(self, channel_id: Optional[int], model: str) -> Optional[str]:
        """The interaction ``model`` last finished in the channel, if still fresh."""
        chain = self._chain(channel_id, model)
        return chain["interaction_id"] if chain else None

    def history_tokens(self, channel_id: int, model: str) -> int:
        """How many prompt tokens ``model``'s last reply in the channel started with (0 when unknown or no longer continued)."""
        chain = self._chain(channel_id, model)
        return int(chain.get("prompt_tokens") or 0) if chain else 0

    def _update_interaction_cache(
        self, channel_id: Optional[int], model: str, interaction_id: str, *, prompt_tokens: int = 0,
        seen_message_id: int = 0, reply: str = "",
    ) -> None:
        """Remember ``model``'s latest interaction in the channel: its ID, how big its prompt was,
        the newest Discord message it was sent, and the start of the reply it gave."""
        if not channel_id:
            return
        entry = {
            "interaction_id": interaction_id,
            "timestamp": time.time(),
            "prompt_tokens": prompt_tokens,
            "seen_message_id": seen_message_id,
            "reply_head": _reply_head(reply),
        }
        self._interaction_cache[(channel_id, model)] = entry
        self._context_store.set_field(
            CONTEXT_KEY, f"{channel_id}:{model}", entry, ttl_seconds=INTERACTION_CACHE_TTL)

    def _drop_chain(self, channel_id: int, model: str) -> None:
        """Forget ``model``'s conversation in the channel; its next reply starts from the history."""
        if self._interaction_cache.pop((channel_id, model), None) is not None:
            logger.info("[gemini] Dropped %s's conversation in channel %s", model, channel_id)
        self._context_store.delete_field(CONTEXT_KEY, f"{channel_id}:{model}")

    def clear_channel_cache(self, channel_id: int) -> None:
        """Forget every model's conversation in a channel (its history window was rebuilt, or /reset)."""
        for slot in [slot for slot in self._interaction_cache if slot[0] == channel_id]:
            self._drop_chain(*slot)

    def _get_client(self):
        """Lazy-init the google-genai client."""
        if self._client is None:
            from google import genai
            from google.genai import types as genai_types
            self._client = genai.Client(
                api_key=self._api_key,
                http_options=genai_types.HttpOptions(timeout=int(REQUEST_TIMEOUT_SECONDS * 1000)),
            )
        return self._client

    def _build_input(self, messages: List[Dict[str, Any]], chain: Optional[Dict[str, Any]] = None) -> list:
        """Build the input for the Interactions API, which takes only the current user turn.

        Earlier messages go in front of it as a transcript. Without a conversation to continue
        (``chain``), all of them. With one, only what it hasn't seen: the messages newer than
        the last one it was sent, minus its own reply to that. That includes what another
        model said in the channel meanwhile, so switching models and back loses nothing.
        """
        if not messages:
            return [{"type": "text", "text": " "}]

        current_msg = messages[-1]
        earlier = messages[:-1]
        has_prev_interaction = chain is not None
        if chain is not None and chain.get("seen_message_id"):
            seen = chain["seen_message_id"]
            head = chain.get("reply_head") or ""
            own_reply_ids = {
                m.get("message_id") for m in earlier
                if head and m.get("role") in ("assistant", "model") and _reply_head(_entry_text(m)) == head
            }
            earlier = [
                m for m in earlier
                if (m.get("message_id") or 0) > seen and m.get("message_id") not in own_reply_ids
            ]
        elif chain is not None:
            # Nothing says what it has seen (untagged messages): go by the bot's last reply.
            last_bot_idx = next(
                (i for i in range(len(earlier) - 1, -1, -1) if earlier[i].get("role") in ("assistant", "model")),
                -1,
            )
            earlier = earlier[last_bot_idx + 1:]

        parts: List[Dict[str, Any]] = []

        def add_text(text_val: str):
            if not text_val:
                return
            if parts and parts[-1].get("type") == "text":
                parts[-1]["text"] += text_val
            else:
                parts.append({"type": "text", "text": text_val})

        def append_msg_parts(msg: Dict[str, Any]):
            role = "User" if msg.get("role") == "user" else "Assistant"
            content = msg.get("content", "")
            first_text = True

            def add_text_part(text_val: str):
                nonlocal first_text
                if not text_val:
                    return
                if first_text:
                    text_val = f"{role}: {text_val}"
                    first_text = False
                add_text(text_val)

            if not isinstance(content, list):
                add_text_part(str(content))
                return
            for part in content:
                if isinstance(part, str):
                    add_text_part(part)
                elif not isinstance(part, dict):
                    continue
                elif part.get("type") == "text":
                    add_text_part(part.get("text", ""))
                elif part.get("type") == "image_url":
                    image_url = part.get("image_url", {}).get("url", "")
                    if not image_url.startswith("data:"):
                        parts.append({"type": "image", "uri": image_url, "mime_type": "image/jpeg"})
                        continue
                    try:
                        header, b64data = image_url.split(",", 1)
                        mime_type = header.split(":")[1].split(";")[0]
                    except (ValueError, IndexError):
                        continue
                    parts.append({"type": "image", "data": b64data, "mime_type": mime_type})

        if earlier:
            if has_prev_interaction:
                add_text("The following messages occurred since your last response:\n\n")
            for msg in earlier:
                append_msg_parts(msg)
                add_text("\n\n")
            if has_prev_interaction:
                add_text("--- End missed context ---\n\n")

        append_msg_parts(current_msg)
        return parts

    async def upload_video(self, path: str, mime_type: str = "video/mp4") -> Any:
        """Upload a video to the Files API and wait until it's ready. Returns the file (``.uri``, ``.name``)."""
        client = self._get_client()
        file = await client.aio.files.upload(file=path, config={"mime_type": mime_type})
        deadline = time.monotonic() + FILE_READY_TIMEOUT_SECONDS
        while getattr(getattr(file, "state", None), "name", "ACTIVE") == "PROCESSING":
            if time.monotonic() > deadline:
                raise GeminiServiceError("Gemini took too long to process the uploaded video.")
            await asyncio.sleep(FILE_POLL_SECONDS)
            file = await client.aio.files.get(name=file.name)
        if getattr(getattr(file, "state", None), "name", "ACTIVE") != "ACTIVE":
            raise GeminiServiceError(f"Gemini couldn't process the uploaded video ({file.state.name}).")
        return file

    async def watch_video(
        self, uri: str, prompt: str, *, agentic: bool, mime_type: Optional[str] = None,
    ) -> tuple[str, float]:
        """Ask about one video through the Interactions API. Returns ``(text, cost)``.

        ``uri`` is an uploaded file's URI or a public YouTube link. ``agentic`` lets the model
        move around the video timeline itself, changing frame rate and resolution as it goes;
        Google recommends it for videos over ~5 minutes and questions about particular
        moments. Otherwise the whole video is sampled at 1 fps. Both run at low resolution,
        in the background, polled until done.
        """
        client = self._get_client()
        video: Dict[str, Any] = {
            "type": "video", "uri": uri, "processing": "agentic" if agentic else "static", "resolution": "low",
        }
        if mime_type:
            video["mime_type"] = mime_type
        mode = "agentic" if agentic else "static"
        logger.info("[gemini/video] Watching %s (%s)", uri, mode)
        try:
            interaction = await client.aio.interactions.create(
                model=VIDEO_MODEL.api_id, input=[video, {"type": "text", "text": prompt}], background=True,
                store=True)
            deadline = time.monotonic() + VIDEO_TIMEOUT_SECONDS
            while getattr(interaction, "status", None) in ("in_progress", "queued"):
                if time.monotonic() > deadline:
                    raise GeminiServiceError("Gemini took too long watching the video.")
                await asyncio.sleep(INTERACTION_POLL_SECONDS)
                interaction = await client.aio.interactions.get(interaction.id)
        except GeminiServiceError:
            raise
        except Exception as e:
            raise GeminiServiceError(f"Gemini couldn't watch that video: {e}") from e
        usage = getattr(interaction, "usage", None)
        cost = self._estimate_interaction_cost(usage, VIDEO_MODEL)
        await record_cost(GEMINI_DIRECT, cost)
        status = getattr(interaction, "status", None)
        if status != "completed":
            raise GeminiServiceError(f"Gemini stopped watching the video ({status}).")
        text = _interaction_text(interaction)
        if not text:
            raise GeminiServiceError("Gemini watched the video but wrote nothing.")
        logger.info("[gemini/video] Watched %s (%s). Usage: %s. Estimated cost: $%.4f", uri, mode, usage, cost)
        return text, cost

    @staticmethod
    def _estimate_interaction_cost(usage: Any, model: GeminiModel) -> float:
        """Price Interactions API usage: input (and what agentic tools loaded) at the input rate.

        ``total_input_tokens`` includes the cached ones, so those come off before pricing it;
        they're charged once, at the cached rate.
        """
        if not usage:
            return 0.0
        try:
            price_input, price_output, price_cached = model.rates()
            cached = _usage_tokens(usage, "total_cached_tokens")
            uncached_input = (
                max(_usage_tokens(usage, "total_input_tokens") - cached, 0)
                + _usage_tokens(usage, "total_tool_use_tokens"))
            output = _usage_tokens(usage, "total_output_tokens") + _usage_tokens(usage, "total_thought_tokens")
            return (uncached_input * price_input + cached * price_cached + output * price_output) / 1_000_000
        except Exception as e:
            logger.warning("Failed to estimate Gemini cost: %s", e)
            return 0.0

    @staticmethod
    def _estimate_image_cost(usage_metadata: Any, model: ImageModel) -> float:
        """Price an image call: output image tokens at the image rate, text and thinking at the text rate."""
        if not usage_metadata:
            return 0.0
        input_tokens = getattr(usage_metadata, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage_metadata, "candidates_token_count", 0) or 0
        thought_tokens = getattr(usage_metadata, "thoughts_token_count", 0) or 0
        details = getattr(usage_metadata, "candidates_tokens_details", None) or []
        image_tokens = sum(
            d.token_count or 0 for d in details if str(getattr(d.modality, "value", d.modality)) == "IMAGE")
        if not details:
            # No breakdown: count the whole output as image.
            image_tokens = output_tokens
        text_tokens = max(output_tokens - image_tokens, 0) + thought_tokens
        cost = (
            input_tokens * model.price_input
            + image_tokens * model.price_image_output
            + text_tokens * model.price_text_output
        ) / 1_000_000
        logger.info("[gemini/image] Usage: %s input, %s image, %s text/thinking. Estimated cost: $%.4f", input_tokens, image_tokens, text_tokens, cost)
        return cost

    async def _stream(
        self, kwargs: Dict[str, Any], show: Optional[Callable[[str], None]], status: Callable[[str], None],
        tool_statuses: Dict[str, str],
    ) -> SimpleNamespace:
        """Send one request and read its stream to the end.

        Returns ``id``, ``status``, ``usage``, ``text`` and ``calls`` (the function calls the
        model made, each ``{"id", "name", "arguments"}``). ``show`` gets the text so far, and
        ``status`` what the model started on: thinking, searching, code, or a tool call.
        """
        result = SimpleNamespace(id=None, status=None, usage=None, text="", calls=[])
        # Function calls by step index; their arguments arrive as pieces of JSON.
        calls: Dict[int, Dict[str, Any]] = {}
        stream = await self._get_client().aio.interactions.create(stream=True, **kwargs)
        try:
            async for event in stream:
                kind = getattr(event, "event_type", None)
                if kind == "interaction.created":
                    result.id = getattr(event.interaction, "id", None)
                elif kind == "step.start":
                    step = event.step
                    step_type = getattr(step, "type", None)
                    if step_type == "function_call":
                        name = getattr(step, "name", "")
                        calls[event.index] = {
                            "id": getattr(step, "id", ""), "name": name,
                            "arguments": getattr(step, "arguments", None) or {}, "json": "",
                        }
                        if name in tool_statuses:
                            status(tool_statuses[name])
                    elif step_type in STEP_STATUSES:
                        status(STEP_STATUSES[step_type])
                elif kind == "step.delta":
                    delta = event.delta
                    delta_type = getattr(delta, "type", None)
                    if delta_type == "text":
                        result.text += getattr(delta, "text", "") or ""
                        if show:
                            show(result.text)
                    elif delta_type == "arguments_delta" and event.index in calls:
                        calls[event.index]["json"] += getattr(delta, "arguments", "") or ""
                elif kind == "interaction.completed":
                    interaction = event.interaction
                    result.id = getattr(interaction, "id", None) or result.id
                    result.status = getattr(interaction, "status", None)
                    result.usage = getattr(interaction, "usage", None)
                elif kind == "error":
                    error = getattr(event, "error", None)
                    code = getattr(error, "code", None)
                    raise _StreamError(
                        str(getattr(error, "message", None) or error), code if isinstance(code, int) else None)
        finally:
            close = getattr(stream, "close", None) or getattr(stream, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception as e:
                    logger.debug("[gemini] Closing the reply stream failed: %s", e)
        for call in calls.values():
            pieces = call.pop("json")
            if pieces:
                try:
                    call["arguments"] = json.loads(pieces)
                except ValueError:
                    logger.warning("[gemini] Unreadable arguments for %s: %s", call["name"], pieces[:200])
            result.calls.append(call)
        return result

    async def _send(
        self, kwargs: Dict[str, Any], show: Optional[Callable[[str], None]], status: Callable[[str], None],
        tool_statuses: Dict[str, str],
    ) -> SimpleNamespace:
        """:meth:`_stream`, tried again after a transient failure."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return await self._stream(kwargs, show, status, tool_statuses)
            except GeminiServiceError:
                raise
            except Exception as e:
                if not _is_retryable(e) or attempt == MAX_ATTEMPTS:
                    logger.error("Gemini API error on attempt %s/%s, giving up: %s", attempt, MAX_ATTEMPTS, e,
                                 exc_info=True)
                    raise GeminiServiceError(f"Gemini API error: {e}") from e
                delay = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
                logger.warning("Gemini API error on attempt %s/%s, retrying in %ss: %s", attempt, MAX_ATTEMPTS, delay, e)
                if show:
                    # Clear what streamed in before the failure.
                    show("")
                await asyncio.sleep(delay)
        raise GeminiServiceError("Failed to get Gemini completion after all retry attempts")

    async def get_chat_completion(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        channel_id: Optional[int] = None,
        discord_user_id: Optional[int] = None,  # noqa: ARG002 - identity kwarg shared by all services
        bot_id: Optional[int] = None,
        search: bool = True,
        on_text: Optional[Callable[[str], None]] = None,
        effort: Optional[str] = None,
        tools: Optional[Sequence[ChatTool]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        disable_cache: bool = False,
    ) -> Tuple[Union[str, Dict[str, Any]], float]:
        """Get a chat completion from the Gemini Interactions API. Returns ``(reply, cost)``.

        The same contract as the Claude service's: with ``on_text`` it hears the cleaned text
        so far, ``effort`` overrides the model's thinking level, ``tools`` are client tools
        the model may call (their costs are added in, and when they made files the reply is
        ``{"text": ..., "files": [...]}``), and ``on_status`` hears what the model is doing
        while there's no new text. With ``search`` the model has Google Search and URL
        reading; with ``tools`` it also gets code execution.

        A channel continues from its last interaction (``previous_interaction_id``) for up to
        ``INTERACTION_CACHE_TTL``, sending only the messages since.
        """
        gemini_model = GEMINI_MODELS.get(model) or GEMINI_MODELS["hydrogen"]
        resolved_bot_id = resolve_bot_id(bot_id, self._bot_id_loader)
        client_tools: Dict[str, ChatTool] = {t.definition["name"]: t for t in tools or ()}

        chain = None if disable_cache else self._chain(channel_id, model)
        previous_id = chain["interaction_id"] if chain else None
        if previous_id:
            logger.info("[gemini] Continuing %s's conversation with previous_interaction_id=%s...", model,
                        previous_id[:20])
        input_parts = self._build_input(messages, chain)
        seen_message_id = max((m.get("message_id") or 0 for m in messages), default=0)

        api_tools: List[Dict[str, Any]] = list(SEARCH_TOOLS) if search else []
        if client_tools:
            api_tools += [CODE_EXECUTION_TOOL] + [function_tool(t.definition) for t in client_tools.values()]
        level = effort if effort in gemini_model.thinking_levels else gemini_model.thinking_level
        generation_config: Dict[str, Any] = {"thinking_level": level}
        if client_tools:
            # What Google asks for when its own tools and functions are offered together.
            generation_config["tool_choice"] = "validated"
        kwargs: Dict[str, Any] = {
            "model": gemini_model.api_id,
            "input": input_parts,
            "store": True,
            "generation_config": generation_config,
        }
        if api_tools:
            kwargs["tools"] = api_tools
        if system_prompt:
            kwargs["system_instruction"] = system_prompt
        if previous_id:
            kwargs["previous_interaction_id"] = previous_id

        snippet = str(input_parts[-1].get("text", "") if input_parts else "").replace("\n", " ")
        logger.info("[%s] Prompt Snippet: %s%s", gemini_model.api_id, snippet[:150], '...' if len(snippet) > 150 else '')

        # Text the model wrote before each finished tool round, and the files its tools made.
        texts: List[str] = []
        files: List[discord.File] = []
        total_cost = 0.0
        tool_rounds = 0
        first_prompt_tokens: Optional[int] = None
        watched_video = False

        def show(text: str) -> None:
            if on_text:
                on_text("\n\n".join(texts + [clean_reply(text, bot_id=resolved_bot_id)]))

        def status(text: str) -> None:
            if on_status:
                on_status(text)

        tool_statuses = {name: tool.status for name, tool in client_tools.items()}
        try:
            while True:
                status(THINKING_STATUS)
                response = await self._send(kwargs, show if on_text else None, status, tool_statuses)
                cost = self._estimate_interaction_cost(response.usage, gemini_model)
                total_cost += cost
                await record_cost(GEMINI_DIRECT, cost)
                note_stat("requests", 1)
                for name in ("total_input_tokens", "total_cached_tokens", "total_output_tokens", "total_thought_tokens"):
                    note_stat(name, _usage_tokens(response.usage, name))
                logger.info("[%s] status=%s usage: %s cost=$%.4f", gemini_model.api_id, response.status,
                            response.usage, cost)
                if first_prompt_tokens is None:
                    # Before any tool results were added: the size the next mention starts from.
                    first_prompt_tokens = _usage_tokens(response.usage, "total_input_tokens")
                if response.status != "requires_action" or not response.calls or not client_tools:
                    break
                if response.text:
                    texts.append(clean_reply(response.text, bot_id=resolved_bot_id))
                # Concurrently; results, costs and files stay in the order the model made the calls.
                outcomes = await asyncio.gather(*(
                    run_tool_call(client_tools, call["name"], call["arguments"], status) for call in response.calls))
                results = []
                for call, outcome in zip(response.calls, outcomes, strict=True):
                    total_cost += outcome.cost
                    files += outcome.files
                    watched_video = watched_video or any(b.get("type") == "video" for b in outcome.content)
                    results.append(function_result(call, outcome))
                kwargs["input"] = results
                kwargs["previous_interaction_id"] = response.id
                tool_rounds += 1
                note_stat("tool_rounds", 1)
                if tool_rounds >= MAX_TOOL_ROUNDS:
                    generation_config["tool_choice"] = "none"
        except GeminiServiceError:
            # Start the next mention fresh rather than continuing from this interaction.
            if channel_id:
                self._drop_chain(channel_id, model)
            raise

        cleaned = "\n\n".join(texts + [clean_reply(response.text, bot_id=resolved_bot_id)]).strip()
        if channel_id and not disable_cache:
            if response.id and response.status == "completed" and not watched_video:
                self._update_interaction_cache(
                    channel_id, model, response.id, prompt_tokens=first_prompt_tokens or 0,
                    seen_message_id=seen_message_id, reply=cleaned)
            else:
                # A video the model watched would be billed again on every later mention, and an
                # interaction that didn't finish can't be continued: the next mention starts fresh.
                self._drop_chain(channel_id, model)

        logger.info("[%s] status=%s cost=$%.4f tool_rounds=%s response snippet: %s", gemini_model.api_id,
                    response.status, total_cost, tool_rounds, cleaned.replace("\n", " ")[:150])
        if files:
            return {"text": cleaned, "files": files}, total_cost
        if not cleaned:
            raise GeminiServiceError(f"Gemini returned an empty response (status: {response.status}).")
        return cleaned, total_cost

    async def close(self) -> None:
        """Close the client's connection pools."""
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.aio.aclose()
            except Exception as e:
                logger.error("Error closing Gemini client: %s", e, exc_info=True)

    async def generate_image(
        self,
        prompt: str,
        model: ImageModel,
        images_b64: Optional[List[str]] = None,
        options: Optional[ImageOptions] = None,
    ) -> ImageResult:
        """Make an image with one of Gemini's image models; ``images_b64`` are reference images."""
        from google.genai import types

        options = options or ImageOptions()
        client = self._get_client()
        parts = [
            types.Part.from_bytes(data=data, mime_type=_image_mime_type(data))
            for data in (base64.b64decode(b64) for b64 in images_b64 or [])
        ]
        await image_links_ready(images=[
            (p.inline_data.data, p.inline_data.mime_type or "") for p in parts if p.inline_data])
        parts.append(types.Part.from_text(text=prompt))

        tools = None
        if options.search:
            search_types = None
            if options.search == "web+image":
                search_types = types.SearchTypes(web_search=types.WebSearch(), image_search=types.ImageSearch())
            # Grounding is free for the first 5,000 searches a month, so it isn't priced.
            tools = [types.Tool(google_search=types.GoogleSearch(search_types=search_types))]
        config = types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=options.aspect_ratio, image_size=options.size),
            thinking_config=(
                types.ThinkingConfig(thinking_level=types.ThinkingLevel(options.thinking.upper()))
                if options.thinking else None
            ),
            tools=tools,
        )

        logger.info("[gemini/image] %s (%s), %s images, Prompt: %s", model.id, options.describe() or 'defaults', len(parts) - 1, prompt[:50])
        response = await client.aio.models.generate_content(model=model.id, contents=parts, config=config)

        candidate_parts = (response.candidates[0].content.parts if response.candidates
                           and response.candidates[0].content else None) or []
        # Thinking can produce draft images; those are marked as thoughts, and the last real one is the result.
        output = [p for p in candidate_parts if not p.thought]
        images = [p.inline_data.data for p in output if p.inline_data and p.inline_data.data]
        if not images:
            raise GeminiServiceError("No image returned from Gemini.")
        text = "\n".join(p.text.strip() for p in output if p.text and p.text.strip())
        cost = self._estimate_image_cost(getattr(response, "usage_metadata", None), model)
        await record_cost(GEMINI_DIRECT, cost)
        return ImageResult(image=images[-1], text=text, cost=cost)


def _image_mime_type(data: bytes) -> str:
    """Tell PNG, GIF and WebP from JPEG by their first bytes."""
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def is_gemini_model(model: str) -> bool:
    """Check if the model string should be handled by the Gemini service."""
    return model in GEMINI_MODELS
