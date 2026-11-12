"""
Mention Log - One JSON line per mention the bot answers, for looking at cost and quality later.

Each line in ``logs/mentions.jsonl`` holds the mention's whole text, who sent it and where,
whether it replied to the bot (a hint that it's a follow-up or re-ask), the model, what
answering cost, the usage stats the cost scope collected (tokens, tools called, web searches,
tool rounds, advisor calls), how long it took and any error. Re-asks can be worked out
afterwards: the same person mentioning the bot again in the same channel soon after a reply.

The file isn't rotated; a line is a few hundred bytes. It's only written once
:func:`setup_mention_log` has run (``main.py``), so tests never write it.
"""

import json
import logging
from datetime import timezone
from pathlib import Path
from typing import Any, Dict

from src.services.cost_service import CostLedger

logger = logging.getLogger(__name__)

# Its own logger, kept out of bot.log and the console.
_mention_logger = logging.getLogger("cmwgpt.mentions")
_mention_logger.propagate = False
_mention_logger.setLevel(logging.INFO)


def setup_mention_log(path: Path = Path("logs") / "mentions.jsonl") -> None:
    """Start appending mention records to ``path``."""
    path.parent.mkdir(exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    _mention_logger.addHandler(handler)


def mention_record(
    message: Any, bot_id: int, model: str, ledger: CostLedger, *,
    seconds: float, task: str = "",
) -> Dict[str, Any]:
    """The log record for one answered mention. ``reply_chars`` and ``error`` come from ``ledger.stats``."""
    reference = getattr(message, "reference", None)
    replied_to = getattr(reference, "resolved", None)
    guild = getattr(message, "guild", None)
    created = getattr(message, "created_at", None)
    record: Dict[str, Any] = {
        "ts": created.astimezone(timezone.utc).isoformat(timespec="seconds") if created else None,
        "message_id": getattr(message, "id", None),
        "guild_id": getattr(guild, "id", None),
        "channel_id": message.channel.id,
        "channel": getattr(message.channel, "name", None),
        "user_id": message.author.id,
        "user": getattr(message.author, "name", None),
        "text": getattr(message, "content", ""),
        "attachments": len(getattr(message, "attachments", None) or []),
        "reply_to": getattr(reference, "message_id", None),
        "reply_to_bot": getattr(getattr(replied_to, "author", None), "id", None) == bot_id,
        "reminder_task": task or None,
        "model": model,
        "cost": round(ledger.total, 6),
        "by_provider": {k: round(v, 6) for k, v in ledger.by_provider.items()},
        "seconds": round(seconds, 2),
        "reply_chars": None,
        "error": None,
    }
    record.update(ledger.stats)
    return record


def log_mention(record: Dict[str, Any]) -> None:
    """Append one record. Never raises."""
    try:
        _mention_logger.info(json.dumps(record, ensure_ascii=False, default=str))
    except Exception as e:
        logger.warning("Couldn't write the mention log: %s", e)
