"""What the Claude and Gemini services share for running the chat model's client tools.

The tools themselves are in ``chat_tools``; each service has its own loop, since the two
APIs ask for tool calls and take results differently, and runs each call through
:func:`run_tool_call` so logging, tracing and stats are the same on both.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Mapping

import discord
import logfire

from src.services.cost_service import note_stat
from src.telemetry import image_links_ready, without_blobs

logger = logging.getLogger(__name__)

# What the reply preview says while the model is working.
THINKING_STATUS = "thinking..."
WEB_SEARCH_STATUS = "searching the web..."
SEARCH_RESULTS_STATUS = "reading search results..."
CODE_STATUS = "running code..."
READING_LINKS_STATUS = "reading links..."
WATCHING_STATUS = "watching the video..."

# Rounds of client tool calls (draw, recall, ...) per reply; the last request after them can't call tools.
MAX_TOOL_ROUNDS = 6


@dataclass
class ToolResult:
    """What a tool call gives back: ``tool_result`` content for the model, plus extras for the reply.

    ``content`` is Messages API blocks (text and images). A tool built for Gemini may also
    return a ``{"type": "video", "uri": ...}`` block, which Gemini watches itself.
    """
    content: List[Dict[str, Any]]
    cost: float = 0.0
    files: List[discord.File] = field(default_factory=list)
    is_error: bool = False


@dataclass(frozen=True)
class ChatTool:
    """A tool definition for the Messages API and the coroutine that runs a call to it."""
    definition: Dict[str, Any]
    run: Callable[[Dict[str, Any]], Awaitable[ToolResult]]
    # What the reply preview says while the call runs, like "drawing...".
    status: str


def text_result(text: str, *, is_error: bool = False) -> ToolResult:
    return ToolResult(content=[{"type": "text", "text": text}], is_error=is_error)


async def run_tool_call(
    tools: Mapping[str, ChatTool], name: str, args: Dict[str, Any], on_status: Callable[[str], None],
) -> ToolResult:
    """Run one call the model made to the tool called ``name``. A failure comes back as an error result."""
    tool = tools.get(name)
    if tool is None:
        return text_result(f"There's no tool named {name}.", is_error=True)
    on_status(tool.status)
    note_stat("tools", name)
    logger.info("[tool] %s %s", name, str(args)[:300])
    with logfire.span("tool {tool_name}", tool_name=name, args=args) as span:
        try:
            result = await tool.run(args)
        except Exception as e:
            logger.warning("[tool] %s failed: %s", name, e)
            span.set_level("error")
            return text_result(f"{name} failed: {e}", is_error=True)
        await image_links_ready(result.content)
        span.set_attributes({
            "is_error": result.is_error,
            "cost": result.cost,
            "files": len(result.files),
            "result": without_blobs(result.content),
        })
        if result.is_error:
            span.set_level("warn")
        return result
