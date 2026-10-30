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
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

import discord

from src.services.image_service import generate_image, resolve_draw_model
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

    if default_draw_model and state_service is not None and (gemini_service or runpod_service):
        tools.append(ChatTool(DRAW_TOOL, draw, "-# drawing..."))
    if user_notes_provider and guild_id:
        tools.append(ChatTool(RECALL_USERS_TOOL, recall_users, "-# remembering..."))
    if channel_summary_provider and guild_id:
        tools.append(ChatTool(RECALL_CHANNEL_TOOL, recall_channel, "-# remembering..."))
    tools.append(ChatTool(HOW_I_WORK_TOOL, how_i_work, "-# checking how I work..."))
    return tools
