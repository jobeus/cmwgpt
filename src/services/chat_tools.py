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
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

import discord

from src.services.discord_history import (
    HAS_FILTERS, MAX_READ_MESSAGES, SORTS, read_around, readable_channels, search_messages,
)
from src.services.image_service import edit_image, generate_image, resolve_draw_model, resolve_edit_model
from src.services.irc_log_service import MAX_CONTEXT_LINES, MAX_DAY_LINES, IrcLog
from src.utils.discord_helper import compress_image, url_to_base64_data_url
from src.utils.url_utils import wiki_title_candidates

logger = logging.getLogger(__name__)

# A reply may draw at most this many images.
MAX_DRAWS_PER_REPLY = 2
# Most user IDs one ``recall_users`` call looks up.
MAX_RECALL_USERS = 8
# Most images one ``edit_image`` call sends to the edit model.
MAX_EDIT_IMAGES = 4

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


EDIT_IMAGE_TOOL = {
    "name": "edit_image",
    "description": (
        "Edit an image someone posted (or one you posted earlier) with this channel's image edit model, "
        "and attach the result to your reply. The result is returned to you so you can comment on it. Use "
        "it when someone asks you to change, fix, restyle or add to a picture in the chat. Costs money and "
        "takes several seconds; it counts toward the same per-reply limit as draw."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "What to change, written as an instruction to the edit model, e.g. 'make the hat red'.",
            },
            "message_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "IDs of messages in this channel whose images to use (the [message ID] in a history "
                    f"header). The first message's first image is the one edited; up to {MAX_EDIT_IMAGES} "
                    "images in all, the rest as references."
                ),
            },
        },
        "required": ["prompt", "message_ids"],
        "additionalProperties": False,
    },
    "strict": True,
}

REMEMBER_TOOL = {
    "name": "remember",
    "description": (
        "Save, fix or forget something in a person's memory profile right away, instead of waiting for the "
        "nightly memory job. Use it only when someone explicitly asks you to remember, correct or forget "
        "something. People can change their own memory; only server admins can change someone else's."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "Discord user ID of the person the memory is about."},
            "note": {
                "type": "string",
                "description": (
                    "The change in plain words, as the person put it, e.g. 'I sold the Tacoma, I drive a "
                    "Ranger now' or 'forget that I'm job hunting'."
                ),
            },
        },
        "required": ["user_id", "note"],
        "additionalProperties": False,
    },
    "strict": True,
}

SEARCH_MESSAGES_TOOL = {
    "name": "search_messages",
    "description": (
        "Search this Discord server's message history (Discord's own search) beyond the recent messages you "
        "were sent. Use it when someone asks what was said earlier, when something came up, to find a link "
        "or picture someone posted, or what a person said about a topic. Each hit has its time, message ID, "
        "channel and author; use read_messages to see the conversation around one."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Words to find in the message text. Leave empty to filter only by the other fields.",
            },
            "author_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Only messages by these Discord user IDs.",
            },
            "all_channels": {
                "type": "boolean",
                "description": "Search every channel the asker can read instead of just this one. Default false.",
            },
            "has": {
                "type": "array",
                "items": {"type": "string", "enum": list(HAS_FILTERS)},
                "description": "Only messages containing all of these, e.g. ['link'] or ['image'].",
            },
            "after": {"type": "string", "description": "Only messages from this day on, as YYYY-MM-DD."},
            "before": {"type": "string", "description": "Only messages before this day, as YYYY-MM-DD."},
            "sort": {
                "type": "string",
                "enum": list(SORTS),
                "description": "Result order. Default newest.",
            },
            "skip": {"type": "integer", "description": "How many results to skip, for paging. Default 0."},
        },
        "required": [],
        "additionalProperties": False,
    },
    "strict": True,
}

READ_MESSAGES_TOOL = {
    "name": "read_messages",
    "description": (
        "Read the Discord messages around one message: the conversation before and after a search hit, "
        "or older chat in this channel than you were sent."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "The message to read around."},
            "channel_id": {
                "type": "string",
                "description": "The channel it's in, from its search hit. Default this channel.",
            },
            "count": {
                "type": "integer",
                "description": f"How many messages to read, 1-{MAX_READ_MESSAGES}. Default 20.",
            },
        },
        "required": ["message_id"],
        "additionalProperties": False,
    },
    "strict": True,
}

WIKI_PAGEVIEWS_TOOL = {
    "name": "wiki_pageviews",
    "description": (
        "Look up a Wikipedia article's average monthly pageviews over the last few months, the same number "
        "deathwatch uses to decide who's famous enough. Use it when someone asks how famous or popular "
        "someone or something is, or about deathwatch's cutoff."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "The article's exact title or its Wikipedia URL."},
        },
        "required": ["title"],
        "additionalProperties": False,
    },
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


def _parse_day(args: Dict[str, Any], key: str) -> Optional[date]:
    """``args[key]`` as a date; None when it's missing. Raises ``ValueError`` when it's malformed."""
    value = str(args.get(key) or "").strip()
    return date.fromisoformat(value) if value else None


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
    message: Any = None,
    default_edit_model: Optional[str] = None,
    memory_corrector: Optional[Callable[[int, int, str, str], Awaitable[Tuple[str, float]]]] = None,
    wiki_views: Optional[Callable[[str], Awaitable[Optional[int]]]] = None,
) -> List[ChatTool]:
    """The tools for one mention, in a fixed order. Tools without their dependencies are left out.

    ``message`` is the mention; the tools that fetch or check Discord things need it.
    """
    tools: List[ChatTool] = []
    draws = 0
    guild = getattr(message, "guild", None)
    asker = getattr(message, "author", None)

    def image_result(text: str, img_bytes: bytes, preview: bytes, cost: Optional[float]) -> ToolResult:
        return ToolResult(
            content=[
                {"type": "text", "text": text},
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
        return image_result(
            f"Drew it with {model}. The image is attached to your reply, so don't link or "
            "describe it as missing; here it is so you can comment on how it came out.",
            img_bytes, preview, cost,
        )

    async def images_from(message_ids: List[int]) -> List[str]:
        """Base64 images from these messages in this channel: attachments, then embed images."""
        images: List[str] = []
        for message_id in message_ids:
            source = await message.channel.fetch_message(message_id)
            for attachment in source.attachments:
                if (attachment.content_type or "").startswith("image/"):
                    images.append(base64.b64encode(await attachment.read()).decode("ascii"))
            for embed in source.embeds:
                url = (embed.image and embed.image.url) or (embed.thumbnail and embed.thumbnail.url)
                if url:
                    images.append((await url_to_base64_data_url(url)).split(",", 1)[1])
        return images[:MAX_EDIT_IMAGES]

    async def edit(args: Dict[str, Any]) -> ToolResult:
        nonlocal draws
        prompt = str(args.get("prompt") or "").strip()
        message_ids = _parse_user_ids(args.get("message_ids"))
        if not prompt or not message_ids:
            return text_result("Give a prompt and at least one message ID.", is_error=True)
        if draws >= MAX_DRAWS_PER_REPLY:
            return text_result(f"You've already made {draws} images for this reply; that's the limit.",
                               is_error=True)
        try:
            images = await images_from(message_ids)
        except discord.NotFound:
            return text_result("One of those messages isn't in this channel.", is_error=True)
        if not images:
            return text_result("Those messages have no images.", is_error=True)
        draws += 1
        model = resolve_edit_model(state_service, channel_id, default_edit_model or "")
        logger.info(f"[edit tool] Channel {channel_id} Model: {model} Images: {len(images)} Prompt: {prompt[:200]}")
        img_bytes, cost = await edit_image(
            prompt=prompt,
            model=model,
            images_b64=images,
            gemini_service=gemini_service,
            runpod_service=runpod_service,
            discord_user_id=user_id,
            discord_channel_id=channel_id,
        )
        preview = await asyncio.to_thread(compress_image, img_bytes)
        return image_result(
            f"Edited it with {model}. The result is attached to your reply; here it is so you can comment on it.",
            img_bytes, preview, cost,
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

    async def remember(args: Dict[str, Any]) -> ToolResult:
        # Only offered when these are set.
        assert memory_corrector is not None and guild is not None and guild_id is not None
        ids = _parse_user_ids([args.get("user_id")])
        note = str(args.get("note") or "").strip()
        if not ids or not note:
            return text_result("Give a user ID and a note.", is_error=True)
        target_id = ids[0]
        is_admin = bool(getattr(getattr(asker, "guild_permissions", None), "administrator", False))
        if target_id != user_id and not is_admin:
            return text_result(
                "Only server admins can change someone else's memory. That person can ask you themselves, "
                "or use /memory correct.", is_error=True)
        member = guild.get_member(target_id)
        name = getattr(member, "display_name", None) or str(target_id)
        logger.info(f"[remember tool] {user_id} changed memory for {target_id}: {note[:200]}")
        try:
            profile, cost = await memory_corrector(guild_id, target_id, name, note)
        except ValueError:
            return text_result("Couldn't apply that, so nothing changed.", is_error=True)
        return ToolResult(content=[{"type": "text", "text": f"Saved. <@{target_id}>'s memory now reads:\n{profile}"}],
                          cost=cost)

    async def search(args: Dict[str, Any]) -> ToolResult:
        try:
            after, before = _parse_day(args, "after"), _parse_day(args, "before")
        except ValueError:
            return text_result("Give dates as YYYY-MM-DD.", is_error=True)
        channels = readable_channels(guild, asker)
        if not args.get("all_channels"):
            channels = [c for c in channels if c.id == channel_id]
        query = str(args.get("query") or "").strip()
        logger.info(f"[search tool] Channel {channel_id} Query: {query[:200]} All: {bool(args.get('all_channels'))}")
        text = await search_messages(
            guild,
            channels,
            query=query,
            author_ids=_parse_user_ids(args.get("author_ids")),
            has=[str(h) for h in args.get("has") or []],
            after=after,
            before=before,
            sort=str(args.get("sort") or "newest"),
            skip=_int_arg(args, "skip"),
        )
        return text_result(text)

    async def read_messages(args: Dict[str, Any]) -> ToolResult:
        message_ids = _parse_user_ids([args.get("message_id")])
        if not message_ids:
            return text_result("Give a message ID.", is_error=True)
        wanted = _parse_user_ids([args.get("channel_id")])
        target = wanted[0] if wanted else channel_id
        channel = next((c for c in readable_channels(guild, asker) if c.id == target), None)
        if channel is None:
            return text_result("That channel doesn't exist or isn't one you and the asker can both read.",
                               is_error=True)
        return text_result(await read_around(channel, message_ids[0], _int_arg(args, "count") or 20))

    async def wiki_pageviews(args: Dict[str, Any]) -> ToolResult:
        assert wiki_views is not None  # only offered when set
        candidates = wiki_title_candidates(str(args.get("title") or ""))
        if not candidates:
            return text_result("Give an article title or Wikipedia URL.", is_error=True)
        for title in candidates:
            views = await wiki_views(title)
            if views:
                return text_result(f"{title.replace('_', ' ')}: {views:,} average monthly pageviews.")
        return text_result(f"No pageviews found for {candidates[0]}; the title must be exact.", is_error=True)

    async def how_i_work(_args: Dict[str, Any]) -> ToolResult:
        return text_result(how_i_work_loader() or "The architecture description isn't available.")

    async def search_irc_log(args: Dict[str, Any]) -> ToolResult:
        assert irc_log is not None  # only offered when set
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
        assert irc_log is not None  # only offered when set
        try:
            day = date.fromisoformat(str(args.get("date") or "").strip())
        except ValueError:
            return text_result("Give the date as YYYY-MM-DD.", is_error=True)
        logger.info(f"[irc log tool] Channel {channel_id} Day: {day}")
        text = await asyncio.to_thread(irc_log.read_day, day, start_line=_int_arg(args, "start_line"))
        return text_result(text)

    if default_draw_model and state_service is not None and (gemini_service or runpod_service):
        tools.append(ChatTool(DRAW_TOOL, draw, "-# drawing..."))
    if default_edit_model and state_service is not None and (gemini_service or runpod_service) and message:
        tools.append(ChatTool(EDIT_IMAGE_TOOL, edit, "-# editing..."))
    if user_notes_provider and guild_id:
        tools.append(ChatTool(RECALL_USERS_TOOL, recall_users, "-# remembering..."))
    if channel_summary_provider and guild_id:
        tools.append(ChatTool(RECALL_CHANNEL_TOOL, recall_channel, "-# remembering..."))
    if memory_corrector and guild_id and guild is not None:
        tools.append(ChatTool(REMEMBER_TOOL, remember, "-# making a note..."))
    if guild is not None:
        tools.append(ChatTool(SEARCH_MESSAGES_TOOL, search, "-# searching the chat..."))
        tools.append(ChatTool(READ_MESSAGES_TOOL, read_messages, "-# reading back..."))
    if irc_log:
        tools.append(ChatTool(SEARCH_IRC_LOG_TOOL, search_irc_log, "-# digging through the irc logs..."))
        tools.append(ChatTool(READ_IRC_LOG_DAY_TOOL, read_irc_log_day, "-# reading the irc logs..."))
    if wiki_views:
        tools.append(ChatTool(WIKI_PAGEVIEWS_TOOL, wiki_pageviews, "-# checking wikipedia..."))
    tools.append(ChatTool(HOW_I_WORK_TOOL, how_i_work, "-# checking how I work..."))
    return tools
