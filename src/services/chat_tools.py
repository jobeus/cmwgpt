"""
Chat Tools - Client-side tools the Claude API models can call while replying to a mention.

Each mention builds its own ``ChatTool`` list (the handlers close over that mention's
guild and channel); ``AnthropicService`` offers their definitions and runs the calls.
The definitions are fixed so the cached tools prefix stays the same between requests.
"""

import asyncio
import base64
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

import discord

from src.services.image_service import generate_image, resolve_draw_model
from src.services.irc_log_service import MAX_CONTEXT_LINES, MAX_DAY_LINES, IrcLog
from src.utils.discord_helper import compress_image

logger = logging.getLogger(__name__)

# A reply may draw at most this many images.
MAX_DRAWS_PER_REPLY = 2
# Most user IDs one ``recall_users`` call looks up.
MAX_RECALL_USERS = 8

_USER_ID_RE = re.compile(r"\d{5,25}")


@dataclass
class ToolResult:
    """What a tool call gives back: ``tool_result`` content for the model, plus extras for the reply."""
    content: List[Dict[str, Any]]
    cost: float = 0.0
    files: List[discord.File] = field(default_factory=list)
    is_error: bool = False


@dataclass(frozen=True)
class ChatTool:
    """A tool definition for the Messages API and the coroutine that runs a call to it."""
    definition: Dict[str, Any]
    run: Callable[[Dict[str, Any]], Awaitable[ToolResult]]
    # Shown in the streamed preview while the call runs.
    preview: str


def text_result(text: str, *, is_error: bool = False) -> ToolResult:
    return ToolResult(content=[{"type": "text", "text": text}], is_error=is_error)


DRAW_TOOL = {
    "name": "draw",
    "description": (
        "Generate an image with this channel's image model and attach it to your reply. The image is "
        "returned to you so you can look at it and comment on it in your reply. Use it when someone asks "
        "you to draw, make, generate or show a picture of something, or when a picture is clearly the "
        "best answer. Each call costs money and takes several seconds, so draw once unless asked for more."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "A detailed image prompt: subject, setting, style, composition, lighting, and any "
                    "text that should appear. Write it fresh from the request; don't include Discord IDs."
                ),
            },
        },
        "required": ["prompt"],
        "additionalProperties": False,
    },
    "strict": True,
}

RECALL_USERS_TOOL = {
    "name": "recall_users",
    "description": (
        "Look up your memory notes about people in this server: facts, ongoing situations and chat style "
        "that the bot has learned from past chats and their corrections. Use it when a message mentions "
        "someone's life, plans, preferences, past events or an inside joke you don't recognize, when "
        "tailoring a reply to someone would help, or when someone asks what you remember about them or "
        "someone else. The notes aren't in your context unless you call this."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "user_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": f"Discord user IDs (the digits in <@id>), at most {MAX_RECALL_USERS}.",
            },
        },
        "required": ["user_ids"],
        "additionalProperties": False,
    },
    "strict": True,
}

RECALL_CHANNEL_TOOL = {
    "name": "recall_channel",
    "description": (
        "Look up your memory of this channel: a summary of what's been going on here over the last couple "
        "of weeks, updated nightly. Use it when a message refers to an ongoing topic, plan, running joke "
        "or earlier event in this channel that the recent messages and today's summary don't explain."
    ),
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    "strict": True,
}

HOW_I_WORK_TOOL = {
    "name": "how_i_work",
    "description": (
        "Read a description of your own architecture: how you're deployed, what context you get, how "
        "memory works, the models, tools, commands and features. Use it when someone asks how you work, "
        "what you can do, or about your commands, models, costs, memory or limits."
    ),
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    "strict": True,
}


SEARCH_IRC_LOG_TOOL = {
    "name": "search_irc_log",
    "description": (
        "Search this channel's old IRC log, from before it moved to Discord. Each matching line comes back "
        "under the date it was said. Use it when someone asks about the IRC days: what someone said, when "
        "something first came up, old jokes, links or arguments. Nicks there may differ from Discord names. "
        "Searches take a second or two, so make each one count; read_irc_log_day shows a whole day around "
        "a hit."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "A case-insensitive Python regex matched within one line, e.g. 'joe rogan' or "
                    "'physx|havok'. Use '.' with a nick to list everything that nick said."
                ),
            },
            "nick": {
                "type": "string",
                "description": "Only lines said by this IRC nick (exact, without the @ or + mode prefix).",
            },
            "newest_first": {
                "type": "boolean",
                "description": "Show the most recent matches first instead of the oldest. Default false.",
            },
            "context": {
                "type": "integer",
                "description": f"Lines of surrounding chat to show around each match, 0-{MAX_CONTEXT_LINES}. Default 0.",
            },
            "skip": {
                "type": "integer",
                "description": "How many matches to skip, for paging past the first results. Default 0.",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "strict": True,
}

READ_IRC_LOG_DAY_TOOL = {
    "name": "read_irc_log_day",
    "description": (
        "Read everything said on one day in this channel's old IRC log. Use it to see the whole "
        "conversation around a search hit, or when someone asks what happened on a particular day."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "The day, as YYYY-MM-DD."},
            "start_line": {
                "type": "integer",
                "description": f"Line to start from; a call returns at most {MAX_DAY_LINES} lines. Default 0.",
            },
        },
        "required": ["date"],
        "additionalProperties": False,
    },
    "strict": True,
}


def _int_arg(args: Dict[str, Any], key: str) -> int:
    try:
        return int(args.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _parse_user_ids(values: Any) -> List[int]:
    ids: List[int] = []
    for value in values if isinstance(values, list) else []:
        match = _USER_ID_RE.search(str(value))
        if match:
            ids.append(int(match.group()))
    return list(dict.fromkeys(ids))[:MAX_RECALL_USERS]


def build_mention_tools(
    *,
    guild_id: Optional[int],
    channel_id: int,
    user_id: int,
    user_notes_provider: Optional[Callable[[int, Sequence[int]], Awaitable[str]]],
    channel_summary_provider: Optional[Callable[[int, int], Awaitable[str]]],
    how_i_work_loader: Callable[[], str],
    state_service: Any,
    gemini_service: Any,
    runpod_service: Any,
    default_draw_model: Optional[str],
    irc_log: Optional[IrcLog] = None,
) -> List[ChatTool]:
    """The tools for one mention, in a fixed order. Tools without their dependencies are left out."""
    tools: List[ChatTool] = []
    draws = 0

    async def draw(args: Dict[str, Any]) -> ToolResult:
        nonlocal draws
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return text_result("No prompt given.", is_error=True)
        if draws >= MAX_DRAWS_PER_REPLY:
            return text_result(f"You've already drawn {draws} images for this reply; that's the limit.",
                               is_error=True)
        draws += 1
        model = resolve_draw_model(state_service, channel_id, default_draw_model or "")
        logger.info(f"[draw tool] Channel {channel_id} Model: {model} Prompt: {prompt[:200]}")
        img_bytes, cost = await generate_image(
            prompt=prompt,
            model=model,
            gemini_service=gemini_service,
            runpod_service=runpod_service,
            discord_user_id=user_id,
            discord_channel_id=channel_id,
        )
        preview = await asyncio.to_thread(compress_image, img_bytes)
        return ToolResult(
            content=[
                {
                    "type": "text",
                    "text": (
                        f"Drew it with {model}. The image is attached to your reply, so don't link or "
                        "describe it as missing; here it is so you can comment on how it came out."
                    ),
                },
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.b64encode(preview).decode("ascii"),
                    },
                },
            ],
            cost=cost or 0.0,
            files=[discord.File(io.BytesIO(img_bytes), filename=f"image{draws}.png")],
        )

    async def recall_users(args: Dict[str, Any]) -> ToolResult:
        ids = _parse_user_ids(args.get("user_ids"))
        if not ids:
            return text_result("No valid Discord user IDs given.", is_error=True)
        notes = await user_notes_provider(guild_id, ids) if user_notes_provider and guild_id else ""
        return text_result(notes or "No memory notes for those users.")

    async def recall_channel(_args: Dict[str, Any]) -> ToolResult:
        summary = (
            await channel_summary_provider(guild_id, channel_id)
            if channel_summary_provider and guild_id else ""
        )
        return text_result(summary or "No memory of this channel yet.")

    async def how_i_work(_args: Dict[str, Any]) -> ToolResult:
        return text_result(how_i_work_loader() or "The architecture description isn't available.")

    async def search_irc_log(args: Dict[str, Any]) -> ToolResult:
        query = str(args.get("query") or "")
        if not query.strip():
            return text_result("No query given.", is_error=True)
        nick = str(args.get("nick") or "").strip().lstrip("@+%~&")
        logger.info(f"[irc log tool] Channel {channel_id} Query: {query[:200]} Nick: {nick}")
        text = await asyncio.to_thread(
            irc_log.search,
            query,
            nick=nick,
            context=_int_arg(args, "context"),
            newest_first=bool(args.get("newest_first")),
            skip=_int_arg(args, "skip"),
        )
        return text_result(text)

    async def read_irc_log_day(args: Dict[str, Any]) -> ToolResult:
        try:
            day = date.fromisoformat(str(args.get("date") or "").strip())
        except ValueError:
            return text_result("Give the date as YYYY-MM-DD.", is_error=True)
        logger.info(f"[irc log tool] Channel {channel_id} Day: {day}")
        text = await asyncio.to_thread(irc_log.read_day, day, start_line=_int_arg(args, "start_line"))
        return text_result(text)

    if default_draw_model and state_service is not None and (gemini_service or runpod_service):
        tools.append(ChatTool(DRAW_TOOL, draw, "-# drawing..."))
    if user_notes_provider and guild_id:
        tools.append(ChatTool(RECALL_USERS_TOOL, recall_users, "-# remembering..."))
    if channel_summary_provider and guild_id:
        tools.append(ChatTool(RECALL_CHANNEL_TOOL, recall_channel, "-# remembering..."))
    if irc_log:
        tools.append(ChatTool(SEARCH_IRC_LOG_TOOL, search_irc_log, "-# digging through the irc logs..."))
        tools.append(ChatTool(READ_IRC_LOG_DAY_TOOL, read_irc_log_day, "-# reading the irc logs..."))
    tools.append(ChatTool(HOW_I_WORK_TOOL, how_i_work, "-# checking how I work..."))
    return tools
