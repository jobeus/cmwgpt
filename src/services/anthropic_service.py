"""
Anthropic Service - Talks to the Claude API directly.

Accepts the same OpenAI-style message array the rest of the bot builds
(``text`` / ``image_url`` parts) and converts it to Messages API content blocks.
"""

import asyncio
import io
import logging
import os
import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import anthropic
import discord
import logfire

from src.config import get_config
from src.db.state_store import CONTEXT_TTL_SECONDS, StateStore
from src.services.chat_tools import ChatTool, ToolResult, text_result
from src.services.cost_service import CLAUDE_DIRECT, note_stat, record_cost
from src.telemetry import image_links_ready, without_blobs
from src.utils.message_utils import clean_reply, resolve_bot_id

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class DirectModel:
    """A Claude API model the bot can call directly, with its pricing (USD per 1M tokens)."""
    api_id: str
    price_input: float
    price_output: float
    price_cache_write: float
    price_cache_read: float
    effort: str

    @property
    def price_cache_write_1h(self) -> float:
        """1-hour cache writes cost twice the input price (5-minute ones 1.25x)."""
        return self.price_input * 2


# Bot-facing model alias -> Claude API model.
DIRECT_MODELS: Dict[str, DirectModel] = {
    # ``medium``: at ``low`` Sonnet 5.5 skipped web searches it should have made. Its thinking
    # stays out of the reply; only text blocks are streamed and posted.
    "sonnet-5.5-direct": DirectModel("claude-sonnet-5-5", 2.00, 10.00, 2.50, 0.10, effort="medium"),
    # Opus 5.5 can't turn thinking off; its default ``medium`` overthinks chat, so run it at ``low``.
    "opus-5.5-direct": DirectModel("claude-opus-5-5", 4.00, 20.00, 5.00, 0.20, effort="low"),
}
_MODELS_BY_API_ID: Dict[str, DirectModel] = {m.api_id: m for m in DIRECT_MODELS.values()}

PRICE_WEB_SEARCH = 0.01

MAX_TOKENS = 16000
# Output cap per request for a chat reply in Discord, thinking included. Replies aim at Discord's
# 2000 characters and rarely pass 1,100 tokens, so this leaves room for a long one while a
# runaway reply stops after a few seconds. The advisor's output has its own cap.
REPLY_MAX_TOKENS = 2000
# Added to a reply that stopped at the cap, so it doesn't read as finished.
CUT_OFF_NOTE = "\n-# (cut off: that hit my length limit)"
# Text that looks like a tool call the model typed out instead of making. It never runs, and
# once started the model tends to repeat it until max_tokens.
TOOL_MARKUP = re.compile(r"<(?:invoke|parameter) name=|</?function_calls>|</?antml:")
# Requests re-sent after the model typed out tool calls, before giving up.
MAX_TOOL_MARKUP_RETRIES = 1
WEB_SEARCH_MAX_USES = 10
# Server-side Python sandbox, offered with client tools (mention replies). It's free alongside
# web search. Files it saves come back as file IDs and are attached to the reply.
CODE_EXECUTION_TOOL = {"type": "code_execution_20260521", "name": "code_execution"}
# Most code execution files attached to one reply, and the largest one attached.
MAX_CODE_FILES = 4
MAX_CODE_FILE_BYTES = 8 * 1024 * 1024
# A server-tool turn can come back as ``pause_turn``; resume it this many times.
MAX_CONTINUATIONS = 5
# Rounds of client tool calls (draw, recall, ...) per reply; the last request after them can't call tools.
MAX_TOOL_ROUNDS = 6

# The API's advisor tool: mid-reply, the model can ask a stronger one for guidance. The API runs
# that model itself on the whole transcript and hands the advice back within the same request.
# Bot model alias -> the advisor's API model; only the models listed here get the tool.
ADVISOR_MODELS = {"sonnet-5.5-direct": "claude-opus-5-5"}
ADVISOR_BETA = "advisor-tool-2026-03-01"
# Advisor calls per reply, across its tool rounds, and the output cap (thinking included) of each.
ADVISOR_MAX_USES = 2
ADVISOR_MAX_TOKENS = 2048


def advisor_tool(advisor_api_model: str) -> Dict[str, Any]:
    """The advisor tool's definition, advised by ``advisor_api_model``."""
    return {
        "type": "advisor_20260301",
        "name": "advisor",
        "model": advisor_api_model,
        "max_uses": ADVISOR_MAX_USES,
        "max_tokens": ADVISOR_MAX_TOKENS,
    }


ADVISOR_STATUS = "asking opus for advice..."
# What the reply preview says while the model is working, by the content block it's on.
THINKING_STATUS = "thinking..."
WEB_SEARCH_STATUS = "searching the web..."
SEARCH_RESULTS_STATUS = "reading search results..."
CODE_STATUS = "running code..."
LOGS_STATUS = "checking my logs..."

# The bot's own traces and logs in Logfire, through the API's MCP connector (Anthropic calls the
# server). Read-only tools only; the key's own scope limits it further.
MCP_BETA = "mcp-client-2025-11-20"
LOGFIRE_MCP_NAME = "logfire"
LOGFIRE_MCP_TOOLS = ("query_run", "query_schema_reference", "query_find_exceptions_in_file", "issue_list")


def logfire_mcp_params(url: str, token: str) -> Dict[str, Any]:
    """The request fields that give the model the Logfire MCP tools."""
    return {
        "mcp_servers": [{"type": "url", "url": url, "name": LOGFIRE_MCP_NAME, "authorization_token": token}],
        "toolset": {
            "type": "mcp_toolset",
            "mcp_server_name": LOGFIRE_MCP_NAME,
            "default_config": {"enabled": False},
            "configs": {name: {"enabled": True} for name in LOGFIRE_MCP_TOOLS},
        },
    }


def _without_logfire_mcp(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """``kwargs`` with the Logfire MCP server, its toolset and its beta taken out."""
    trimmed = {k: v for k, v in kwargs.items() if k != "mcp_servers"}
    trimmed["tools"] = [t for t in kwargs.get("tools", []) if t.get("type") != "mcp_toolset"]
    trimmed["betas"] = [b for b in kwargs.get("betas", []) if b != MCP_BETA]
    return trimmed


def _is_mcp_error(error: anthropic.BadRequestError) -> bool:
    return "mcp_server" in str(getattr(error, "message", "") or error)

# Cache diagnostics (beta): each request names the previous response in its
# channel, and the API reports where the prompt-cache prefix diverged from it.
# Fingerprints are only kept for requests that carry this beta, so it's sent
# on every request.
CACHE_DIAGNOSIS_BETA = "cache-diagnosis-2026-04-07"
# A reported miss is only worth a warning when less than this share of the prompt was read from the cache.
CACHE_READ_SHARE_TO_WARN = 0.8

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[\w/+.-]+);base64,(?P<data>.+)$", re.DOTALL)
_SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def _aborted_stream_cost(stream: Any, streamed: str, model: DirectModel) -> float:
    """Rough cost of a stream stopped early, which never reports its final usage.

    The input side comes from the message's start event; the output is guessed from the text
    so far (about 4 characters a token), which leaves out any thinking before it.
    """
    snapshot = getattr(stream, "current_message_snapshot", None)
    usage = getattr(snapshot, "usage", None)
    if usage is None:
        return 0.0
    estimate = SimpleNamespace(
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        output_tokens=len(streamed) // 4,
        server_tool_use=None,
    )
    return _usage_cost(estimate, model)


class _ToolMarkupError(Exception):
    """The model wrote tool-call markup as reply text; carries what the request cost so far."""

    def __init__(self, cost: float):
        super().__init__("tool-call markup in the reply text")
        self.cost = cost


class AnthropicServiceError(Exception):
    """Custom exception for Anthropic service errors."""
    pass


def is_direct_anthropic_model(model: str) -> bool:
    """Check if the model string should be handled by the Anthropic service."""
    return model in DIRECT_MODELS


def _convert_part(part: Any, role: str) -> Optional[Dict[str, Any]]:
    """Convert one OpenAI-style content part to an Anthropic content block."""
    if isinstance(part, str):
        return {"type": "text", "text": part} if part.strip() else None
    if not isinstance(part, dict):
        return None

    if part.get("type") == "text":
        text = part.get("text", "")
        return {"type": "text", "text": text} if text and text.strip() else None

    # Images are only allowed in user turns.
    if part.get("type") == "image_url" and role == "user":
        url = (part.get("image_url") or {}).get("url", "")
        match = _DATA_URL_RE.match(url)
        if match:
            mime = match.group("mime").lower()
            if mime not in _SUPPORTED_IMAGE_TYPES:
                logger.debug("[anthropic] Skipping unsupported image type %s", mime)
                return None
            return {
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": match.group("data")},
            }
        if url.startswith(("http://", "https://")):
            return {"type": "image", "source": {"type": "url", "url": url}}
    return None


def build_anthropic_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert the bot's OpenAI-style message array to Messages API format."""
    converted: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue

        content = msg.get("content", "")
        parts = content if isinstance(content, list) else [content]
        blocks = [b for b in (_convert_part(p, role) for p in parts) if b is not None]
        if blocks:
            entry: Dict[str, Any] = {"role": role, "content": blocks}
            if msg.get("message_id") is not None:
                # Read (and removed) by ``apply_history_breakpoints``; the API doesn't take it.
                entry["message_id"] = msg["message_id"]
            converted.append(entry)

    # The conversation must open with a user turn.
    if converted and converted[0]["role"] != "user":
        converted.insert(0, {"role": "user", "content": [{"type": "text", "text": "(earlier conversation)"}]})
    return converted


_EPHEMERAL = {"type": "ephemeral"}
# Mention replies keep their channel's history window for an hour (HISTORY_WINDOW_SECONDS in
# the mention handler), so their system prompt and history are cached that long too.
_EPHEMERAL_1H = {"type": "ephemeral", "ttl": "1h"}


def apply_history_breakpoints(
    api_messages: List[Dict[str, Any]], previous_index: Optional[int],
    cache_control: Dict[str, str] = _EPHEMERAL,
) -> Optional[int]:
    """Mark cache breakpoints on the history and return this request's history index.

    The last message is the mention. Everything before it is history that the next
    mention in the channel re-sends unchanged, so it gets a breakpoint (written now,
    read next time). The mention itself is left unmarked because its rendering can
    still change (Discord unfurls link embeds a moment after posting). A second
    breakpoint goes where the previous request's history ended, so the read hits even
    when more than 20 blocks (the API's lookback limit) were added since.

    Messages posted after the mention (it waited in the queue while the bot replied to
    another one) sit just before it now but after it in the next request's history, so
    the history breakpoint goes before them. Entries' ``message_id`` tags (Discord
    snowflakes, so ordered by time) are how they're spotted; they're removed here.
    """
    message_ids = [message.pop("message_id", None) for message in api_messages]
    mention_id = message_ids[-1] if message_ids else None
    current_index = len(api_messages) - 2
    if mention_id is not None:
        while current_index >= 0 and (message_ids[current_index] or 0) > mention_id:
            current_index -= 1
    if current_index < 0:
        return None
    marked = {current_index}
    if previous_index is not None and 0 <= previous_index < current_index:
        marked.add(previous_index)
    for index in marked:
        blocks = api_messages[index]["content"]
        blocks[-1] = {**blocks[-1], "cache_control": cache_control}
    return current_index


def _is_grammar_error(error: Exception) -> bool:
    """Whether the API refused the request because the strict tools' combined grammar is too big."""
    return "grammar is too large" in str(getattr(error, "message", "") or error)


def _without_strict(tool: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in tool.items() if k != "strict"}


def _with_assistant_content(api_messages: List[Dict[str, Any]], content: List[Any]) -> List[Dict[str, Any]]:
    """Copy ``api_messages`` with ``content`` added to the assistant turn it ends on, or as a new one."""
    if api_messages and api_messages[-1]["role"] == "assistant":
        last = api_messages[-1]
        return api_messages[:-1] + [{**last, "content": list(last["content"]) + list(content)}]
    return api_messages + [{"role": "assistant", "content": list(content)}]


def _with_tool_round_breakpoint(api_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Copy ``api_messages`` with the one tool-round cache breakpoint on its last block.

    Without it, every further round re-sends the mention and all earlier tool results at the
    full input price. Earlier rounds' breakpoints are dropped, so a request keeps at most 4
    (system, up to 2 in the history, this one); the next round still reads the previous
    round's entry through the lookback window.
    """
    copied = []
    for message in api_messages:
        content = message["content"]
        if isinstance(content, list) and any(_is_marked_tool_result(b) for b in content):
            content = [
                {k: v for k, v in b.items() if k != "cache_control"} if _is_marked_tool_result(b) else b
                for b in content
            ]
            message = {**message, "content": content}
        copied.append(message)
    last = copied[-1]
    copied[-1] = {**last, "content": list(last["content"][:-1]) + [{**last["content"][-1], "cache_control": _EPHEMERAL}]}
    return copied


def _is_marked_tool_result(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") == "tool_result" and "cache_control" in block


def _response_text(response: Any) -> str:
    return "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()


def _block_status(block: Any, tool_statuses: Dict[str, str]) -> Optional[str]:
    """What the preview should say when the model starts this content block, if anything."""
    kind = getattr(block, "type", None)
    if kind in ("thinking", "redacted_thinking"):
        return THINKING_STATUS
    if kind == "mcp_tool_use":
        return LOGS_STATUS
    if kind == "server_tool_use":
        name = getattr(block, "name", "")
        return {"web_search": WEB_SEARCH_STATUS, "advisor": ADVISOR_STATUS}.get(name, CODE_STATUS)
    if kind == "web_search_tool_result":
        return SEARCH_RESULTS_STATUS
    if kind == "tool_use":
        return tool_statuses.get(getattr(block, "name", ""))
    return None


def _code_file_ids(response: Any) -> List[str]:
    """IDs of the files code execution saved in ``response``."""
    ids = []
    for block in getattr(response, "content", None) or ():
        if getattr(block, "type", None) != "bash_code_execution_tool_result":
            continue
        result = getattr(block, "content", None)
        if getattr(result, "type", None) != "bash_code_execution_result":
            continue
        for output in getattr(result, "content", None) or ():
            if getattr(output, "type", None) == "bash_code_execution_output":
                ids.append(output.file_id)
    return ids


def _token_cost(usage: Any, model: DirectModel) -> float:
    """Price the token counts on ``usage`` (a usage block or one of its iterations) at ``model``'s rates."""
    written = getattr(usage, "cache_creation_input_tokens", 0) or 0
    written_1h = getattr(getattr(usage, "cache_creation", None), "ephemeral_1h_input_tokens", 0) or 0
    return (
        (getattr(usage, "input_tokens", 0) or 0) * model.price_input
        + (getattr(usage, "output_tokens", 0) or 0) * model.price_output
        + (written - written_1h) * model.price_cache_write
        + written_1h * model.price_cache_write_1h
        + (getattr(usage, "cache_read_input_tokens", 0) or 0) * model.price_cache_read
    ) / 1_000_000


def _advisor_iterations(usage: Any) -> List[Any]:
    """The advisor's own model runs in ``usage``; the top-level counts leave them out."""
    return [i for i in getattr(usage, "iterations", None) or () if getattr(i, "type", None) == "advisor_message"]


def _usage_cost(usage: Any, model: DirectModel) -> float:
    """Price a response's ``usage`` block in USD: its tokens at ``model``'s rates, advisor runs at theirs."""
    if usage is None:
        return 0.0
    cost = _token_cost(usage, model)
    for iteration in _advisor_iterations(usage):
        advisor = _MODELS_BY_API_ID.get(getattr(iteration, "model", None) or "")
        if advisor is None:
            logger.warning("[advisor] No prices for %s; billing it as opus", getattr(iteration, "model", None))
            advisor = DIRECT_MODELS["opus-5.5-direct"]
        cost += _token_cost(iteration, advisor)
    server_tool_use = getattr(usage, "server_tool_use", None)
    searches = (getattr(server_tool_use, "web_search_requests", 0) or 0) if server_tool_use else 0
    return cost + searches * PRICE_WEB_SEARCH


def _note_usage(response: Any) -> None:
    """Add one response's tokens and web searches to the open cost scope's stats."""
    usage = getattr(response, "usage", None)
    note_stat("requests", 1)
    if getattr(response, "model", None):
        note_stat("served_by", response.model)
    if usage is None:
        return
    for name in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        note_stat(name, getattr(usage, name, 0) or 0)
    server_tool_use = getattr(usage, "server_tool_use", None)
    note_stat("web_searches", (getattr(server_tool_use, "web_search_requests", 0) or 0) if server_tool_use else 0)
    advisor_runs = _advisor_iterations(usage)
    if advisor_runs:
        note_stat("advisor_calls", len(advisor_runs))
        note_stat("advisor_output_tokens", sum(getattr(i, "output_tokens", 0) or 0 for i in advisor_runs))


def _cache_read_share(usage: Any) -> float:
    """How much of a request's input came from the prompt cache, 0-1."""
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    total = read + (getattr(usage, "cache_creation_input_tokens", 0) or 0) + (getattr(usage, "input_tokens", 0) or 0)
    return read / total if total else 0.0


def _log_cache_diagnostics(api_model: str, channel_id: Optional[int], response: Any) -> None:
    """Log why the prompt cache missed, from the response's cache diagnostics."""
    diagnostics = getattr(response, "diagnostics", None)
    if diagnostics is None:
        return
    reason = getattr(diagnostics, "cache_miss_reason", None)
    where = f"[{api_model}] channel={channel_id}"
    if reason is None:
        # Either nothing to report or the comparison hadn't finished yet.
        logger.debug("%s cache diagnostics: no miss reported", where)
        return
    missed = getattr(reason, "cache_missed_input_tokens", None)
    if missed:
        # The missed tokens are measured against the whole previous request, including its tool rounds and
        # the blocks added to its mention, which aren't sent again. So what matters is how much of this
        # prompt came from the cache.
        share = _cache_read_share(getattr(response, "usage", None))
        log = logger.warning if share < CACHE_READ_SHARE_TO_WARN else logger.info
        log("%s cache miss: %s, ~%s input tokens not read from cache, %.0f%% of this prompt read from it",
            where, reason.type, missed, share * 100)
    else:
        logger.info("%s cache diagnostics: %s", where, reason.type)


# Each channel's breakpoint and previous response id, in Redis so a restart within the
# hour still reads the prompt cache (a moved breakpoint would miss) and keeps diagnosing it.
CONTEXT_KEY = "context:anthropic"


class AnthropicService:
    """Service for handling direct Claude API interactions."""

    def __init__(self, api_key: Optional[str] = None, *, logfire_read_token: str = "",
                 logfire_mcp_url: str = "https://logfire-us.pydantic.dev/mcp",
                 logs_channel_id: Optional[int] = None, context_store: Optional[StateStore] = None):
        self._api_key = api_key
        self._logfire_read_token = logfire_read_token
        self._logs_channel_id = logs_channel_id
        self._logfire_mcp_url = logfire_mcp_url
        self._client: Optional[anthropic.AsyncAnthropic] = None
        self._bot_id_loader: Optional[Callable[[], Optional[int]]] = None
        # channel_id -> index of the last history message marked in the previous request
        self._history_breakpoints: Dict[int, int] = {}
        # channel_id -> id of the previous response, for cache diagnostics
        self._last_message_ids: Dict[int, str] = {}
        self._context_store = context_store or StateStore(None)
        self._restore_context()

    def _restore_context(self) -> None:
        """Pick up each channel's cache state saved by the previous process."""
        for channel, saved in self._context_store.get_all(CONTEXT_KEY).items():
            try:
                channel_id = int(channel)
                if isinstance(saved.get("breakpoint"), int):
                    self._history_breakpoints[channel_id] = saved["breakpoint"]
                if saved.get("message_id"):
                    self._last_message_ids[channel_id] = str(saved["message_id"])
            except (AttributeError, ValueError):
                logger.warning("Ignoring unreadable cache state for channel %s: %s", channel, saved)
        if self._history_breakpoints or self._last_message_ids:
            logger.info("Restored Claude cache state for %s channels", len(self._history_breakpoints))

    def logs_available(self, channel_id: Optional[int]) -> bool:
        """Whether mention replies in ``channel_id`` can query the bot's own Logfire traces.

        Only in the one configured channel: the traces hold every channel's conversations.
        """
        return bool(self._logfire_read_token) and channel_id is not None and channel_id == self._logs_channel_id

    def set_client(self, client) -> None:
        """Set a custom client (useful for testing)."""
        self._client = client

    def set_bot_id_loader(self, bot_id_loader: Optional[Callable[[], Optional[int]]]) -> None:
        """Set a callable that returns the current bot user ID."""
        self._bot_id_loader = bot_id_loader

    def get_client(self) -> anthropic.AsyncAnthropic:
        """Get the Anthropic client with lazy initialization."""
        if self._client is None:
            api_key = self._api_key or get_config().anthropic_api_key
            if not api_key:
                raise AnthropicServiceError("ANTHROPIC_API_KEY is not configured.")
            # The SDK retries connection errors, 408/409/429 and 5xx with backoff.
            self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=3, timeout=600.0)
        return self._client

    def clear_channel_cache(self, channel_id: int) -> None:
        """Forget a channel's cache state (its history window was rebuilt).

        The next request starts a fresh diagnostics chain, so an expected
        divergence isn't reported as a cache miss.
        """
        self._history_breakpoints.pop(channel_id, None)
        self._last_message_ids.pop(channel_id, None)
        self._context_store.delete_field(CONTEXT_KEY, channel_id)

    async def close(self) -> None:
        """Close the Anthropic client and clean up resources."""
        if self._client is not None and hasattr(self._client, "close"):
            try:
                await self._client.close()
                logger.debug("Anthropic client closed")
            except Exception as e:
                logger.error("Error closing Anthropic client: %s", e, exc_info=True)
            finally:
                self._client = None

    async def _send(
        self,
        client: Any,
        api_messages: List[Dict[str, Any]],
        kwargs: Dict[str, Any],
        direct_model: DirectModel,
        channel_id: Optional[int],
        previous_message_id: Optional[str],
        resolved_bot_id: Optional[int],
        on_text: Optional[Callable[[str], None]],
        on_status: Optional[Callable[[str], None]] = None,
        tool_statuses: Optional[Dict[str, str]] = None,
        code_file_ids: Optional[List[str]] = None,
    ) -> Tuple[Any, float, Optional[str], List[Dict[str, Any]]]:
        """Run one request, resuming ``pause_turn``s.

        Returns ``(response, cost, last message id, messages)``; ``messages`` ends with the
        paused assistant content when the turn was resumed. While streaming, ``on_status``
        hears what the model started on: thinking, a web search, code, or a tool call (its
        status from ``tool_statuses``). IDs of files code execution saved are added to
        ``code_file_ids``, and its container is reused by the requests after this one.
        """
        api_model = direct_model.api_id
        cost = 0.0
        streamed = ""
        response = None
        # So the request's span can link to its images instead of carrying them.
        await image_links_ready(api_messages)
        for _ in range(MAX_CONTINUATIONS + 1):
            request = {
                "messages": api_messages,
                "diagnostics": {"previous_message_id": previous_message_id},
                **kwargs,
            }
            if on_text:
                async with client.beta.messages.stream(**request) as stream:
                    async for event in stream:
                        if event.type == "text":
                            streamed += event.text
                            if TOOL_MARKUP.search(streamed):
                                # Leaving the block closes the stream, so it stops generating.
                                aborted_cost = _aborted_stream_cost(stream, streamed, direct_model)
                                await record_cost(CLAUDE_DIRECT, aborted_cost)
                                raise _ToolMarkupError(cost + aborted_cost)
                            on_text(clean_reply(streamed, bot_id=resolved_bot_id))
                        elif event.type == "content_block_start" and on_status:
                            status = _block_status(getattr(event, "content_block", None), tool_statuses or {})
                            if status:
                                on_status(status)
                    response = await stream.get_final_message()
            else:
                response = await client.beta.messages.create(**request)
            # A safety fallback can serve the turn on another model; price it at that model's rates.
            served_by = _MODELS_BY_API_ID.get(getattr(response, "model", None) or "", direct_model)
            response_cost = _usage_cost(getattr(response, "usage", None), served_by)
            cost += response_cost
            await record_cost(CLAUDE_DIRECT, response_cost)
            if TOOL_MARKUP.search(_response_text(response)):
                raise _ToolMarkupError(cost)
            _note_usage(response)
            _log_cache_diagnostics(api_model, channel_id, response)
            previous_message_id = getattr(response, "id", None) or previous_message_id
            if code_file_ids is not None:
                code_file_ids += _code_file_ids(response)
            container_id = getattr(getattr(response, "container", None), "id", None)
            if container_id:
                kwargs["container"] = container_id
            if response.stop_reason != "pause_turn":
                break
            # Resume a paused server-tool turn by sending its content back.
            api_messages = _with_assistant_content(api_messages, response.content)
        return response, cost, previous_message_id, api_messages

    async def _download_code_files(self, client: Any, file_ids: List[str]) -> List[discord.File]:
        """Download the files code execution saved, as Discord attachments. Failures are skipped."""
        files: List[discord.File] = []
        for file_id in list(dict.fromkeys(file_ids))[:MAX_CODE_FILES]:
            try:
                metadata = await client.files.retrieve_metadata(file_id)
                if (getattr(metadata, "size_bytes", 0) or 0) > MAX_CODE_FILE_BYTES:
                    logger.info("[code_execution] Skipping %s: too big to attach", file_id)
                    continue
                data = await (await client.files.download(file_id)).read()
            except Exception as e:
                logger.warning("[code_execution] Couldn't download %s: %s", file_id, e)
                continue
            name = os.path.basename(getattr(metadata, "filename", "") or "") or f"{file_id}.bin"
            files.append(discord.File(io.BytesIO(data), filename=name))
        return files

    async def _run_tool_calls(
        self, response: Any, client_tools: Dict[str, ChatTool], on_status: Callable[[str], None],
    ) -> Tuple[List[Dict[str, Any]], float, List[discord.File]]:
        """Run ``response``'s client tool calls concurrently. Returns ``(tool_result blocks, cost, files)``.

        Results, costs and files come back in the order the model made the calls.
        """
        async def run_one(block: Any) -> ToolResult:
            name = getattr(block, "name", "")
            args = getattr(block, "input", None) or {}
            tool = client_tools.get(name)
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

        blocks = [block for block in response.content if getattr(block, "type", None) == "tool_use"]
        outcomes = await asyncio.gather(*(run_one(block) for block in blocks))
        results: List[Dict[str, Any]] = []
        cost = 0.0
        files: List[discord.File] = []
        for block, result in zip(blocks, outcomes, strict=True):
            cost += result.cost
            files += result.files
            entry: Dict[str, Any] = {"type": "tool_result", "tool_use_id": block.id, "content": result.content}
            if result.is_error:
                entry["is_error"] = True
            results.append(entry)
        return results, cost, files

    async def get_chat_completion(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        channel_id: Optional[int] = None,
        state_service: Any = None,
        bot_id: Optional[int] = None,
        discord_user_id: Optional[int] = None,  # noqa: ARG002 - identity kwarg shared by all services
        search: bool = True,
        on_text: Optional[Callable[[str], None]] = None,
        effort: Optional[str] = None,
        tools: Optional[Sequence[ChatTool]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        max_tokens: int = MAX_TOKENS,
    ) -> Tuple[Union[str, Dict[str, Any]], float]:
        """Get a chat completion from the Claude API. Returns ``(reply, cost)``.

        With ``on_text``, the reply is streamed and ``on_text`` gets the cleaned text so far.
        ``effort`` overrides the model's default effort level. ``tools`` are client tools the
        model may call; their costs are added in, and when they made files the reply is
        ``{"text": ..., "files": [...]}`` instead of a string. With ``tools``, the model also
        gets code execution, and the files its code saves are attached the same way.
        ``on_status`` hears what the model is doing while there's no new text: thinking,
        searching, running code, or running a tool. ``max_tokens`` caps each request's output,
        thinking included.
        """
        client = self.get_client()
        resolved_bot_id = resolve_bot_id(bot_id, state_service, self._bot_id_loader)
        direct_model = DIRECT_MODELS.get(model) or DIRECT_MODELS["sonnet-5.5-direct"]
        api_model = direct_model.api_id
        api_messages = build_anthropic_messages(messages)
        if not api_messages:
            raise AnthropicServiceError("No usable messages to send to Claude.")

        kwargs: Dict[str, Any] = {
            "model": api_model,
            "max_tokens": max_tokens,
            "output_config": {"effort": effort or direct_model.effort},
            # On a safety decline, let the API re-run on its recommended fallback model.
            "betas": ["server-side-fallback-2026-07-01", CACHE_DIAGNOSIS_BETA],
            "fallbacks": "default",
        }
        # Only mention replies pass a channel; background jobs run hours apart, so they keep
        # the cheaper 5-minute writes.
        cache_control = _EPHEMERAL_1H if channel_id else _EPHEMERAL
        if system_prompt:
            # Its own breakpoint, so the system prompt still reads from cache when the history doesn't.
            kwargs["system"] = [{"type": "text", "text": system_prompt, "cache_control": cache_control}]
        previous_index = self._history_breakpoints.get(channel_id) if channel_id else None
        history_index = apply_history_breakpoints(api_messages, previous_index, cache_control)
        if search:
            kwargs["tools"] = [{
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": WEB_SEARCH_MAX_USES,
            }]
        client_tools: Dict[str, ChatTool] = {t.definition["name"]: t for t in tools or ()}
        if client_tools:
            kwargs["tools"] = kwargs.get("tools", []) + [CODE_EXECUTION_TOOL] + [
                t.definition for t in client_tools.values()
            ]
        if client_tools and self.logs_available(channel_id):
            mcp = logfire_mcp_params(self._logfire_mcp_url, self._logfire_read_token)
            kwargs["mcp_servers"] = mcp["mcp_servers"]
            kwargs["tools"] = kwargs.get("tools", []) + [mcp["toolset"]]
            kwargs["betas"] = kwargs["betas"] + [MCP_BETA]
        # Mention replies only (they pass a channel): a background job is one fixed task.
        if model in ADVISOR_MODELS and channel_id:
            kwargs["tools"] = kwargs.get("tools", []) + [advisor_tool(ADVISOR_MODELS[model])]
            kwargs["betas"] = kwargs["betas"] + [ADVISOR_BETA]

        last_text = api_messages[-1]["content"][0].get("text", "") if api_messages else ""
        snippet = last_text.replace("\n", " ")
        logger.info("[%s] Prompt Snippet: %s%s", api_model, snippet[:150], '...' if len(snippet) > 150 else '')

        total_cost = 0.0
        previous_message_id = self._last_message_ids.get(channel_id) if channel_id else None
        # Text the model wrote before each finished tool round, and the files its tools made.
        texts: List[str] = []
        files: List[discord.File] = []
        code_file_ids: List[str] = []

        def show(text: str) -> None:
            if on_text:
                on_text("\n\n".join(texts + [text]))

        def status(text: str) -> None:
            if on_status:
                on_status(text)

        tool_statuses = {name: tool.status for name, tool in client_tools.items()}

        cap_raised = False
        strict_dropped = False
        markup_retries = 0
        tool_rounds = 0
        try:
            while True:
                status(THINKING_STATUS)
                try:
                    response, cost, previous_message_id, sent_messages = await self._send(
                        client, api_messages, kwargs, direct_model, channel_id, previous_message_id,
                        resolved_bot_id, show if on_text else None, status, tool_statuses, code_file_ids,
                    )
                except _ToolMarkupError as e:
                    total_cost += e.cost
                    note_stat("tool_markup", 1)
                    if markup_retries >= MAX_TOOL_MARKUP_RETRIES:
                        logger.error("[%s] Reply was tool-call markup again; giving up", api_model)
                        raise AnthropicServiceError(
                            "Claude wrote its tool calls out as text instead of running them. Try again.") from e
                    markup_retries += 1
                    logger.warning("[%s] Reply turned into tool-call markup; asking again", api_model)
                    # Clear the draft it streamed before the markup.
                    show("")
                    continue
                except anthropic.BadRequestError as e:
                    if kwargs.get("mcp_servers") and _is_mcp_error(e):
                        # Logfire is down or the key stopped working: answer without the logs.
                        logger.warning("[%s] Logfire MCP failed, retrying without it: %s", api_model, e.message)
                        kwargs = _without_logfire_mcp(kwargs)
                        continue
                    if strict_dropped or not _is_grammar_error(e) or not kwargs.get("tools"):
                        raise
                    # Too many or too big strict schemas: the arguments just aren't guaranteed
                    # to match then, and every handler checks its own input anyway.
                    logger.warning("[%s] Strict tool grammar too large; retrying without strict mode", api_model)
                    kwargs["tools"] = [_without_strict(t) for t in kwargs["tools"]]
                    strict_dropped = True
                    continue
                total_cost += cost
                if (response.stop_reason == "max_tokens" and not cap_raised and kwargs["max_tokens"] < MAX_TOKENS
                        and not _response_text(response).strip()):
                    # Thinking used the whole reply cap, so there's nothing to post. Ask once more
                    # with room to answer; max_tokens isn't part of the cache key.
                    cap_raised = True
                    note_stat("max_tokens_retry", 1)
                    logger.warning("[%s] Hit max_tokens (%s) before writing any text; asking again with %s",
                                   api_model, kwargs["max_tokens"], MAX_TOKENS)
                    kwargs["max_tokens"] = MAX_TOKENS
                    continue
                if response.stop_reason != "tool_use" or not client_tools:
                    break
                text = _response_text(response)
                if text:
                    texts.append(clean_reply(text, bot_id=resolved_bot_id))
                results, cost, new_files = await self._run_tool_calls(response, client_tools, status)
                total_cost += cost
                files += new_files
                api_messages = _with_tool_round_breakpoint(
                    _with_assistant_content(sent_messages, response.content) + [{"role": "user", "content": results}]
                )
                tool_rounds += 1
                note_stat("tool_rounds", 1)
                if tool_rounds >= MAX_TOOL_ROUNDS:
                    kwargs["tool_choice"] = {"type": "none"}
        except anthropic.AuthenticationError as e:
            logger.error("Anthropic authentication error: %s", e)
            raise AnthropicServiceError("Authentication failed. Please check ANTHROPIC_API_KEY.") from e
        except anthropic.BadRequestError as e:
            logger.error("Anthropic bad request: %s", e)
            raise AnthropicServiceError(f"Invalid request: {e.message}") from e
        except anthropic.RateLimitError as e:
            logger.error("Anthropic rate limit after retries: %s", e)
            raise AnthropicServiceError("Rate limit exceeded. Please try again later.") from e
        except anthropic.APIStatusError as e:
            logger.error("Anthropic API error (%s): %s", e.status_code, e)
            raise AnthropicServiceError(f"Claude API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            logger.error("Anthropic connection error: %s", e)
            raise AnthropicServiceError("Connection to the Claude API failed. Please try again later.") from e

        if response is None:
            raise AnthropicServiceError("The Claude API returned no response.")
        if channel_id and history_index is not None:
            self._history_breakpoints[channel_id] = history_index
        if channel_id and previous_message_id:
            self._last_message_ids[channel_id] = previous_message_id
        if channel_id and (history_index is not None or previous_message_id):
            self._context_store.set_field(CONTEXT_KEY, channel_id, {
                "breakpoint": self._history_breakpoints.get(channel_id),
                "message_id": self._last_message_ids.get(channel_id),
            }, ttl_seconds=CONTEXT_TTL_SECONDS)

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            logger.warning("[%s] Request declined (category=%s)", api_model, category)
            raise AnthropicServiceError(f"Claude declined this request (category: {category or 'unknown'}).")

        response_text = _response_text(response)
        usage = getattr(response, "usage", None)
        logger.info(
            "[%s] stop_reason=%s cost=$%.4f cache_read=%s cache_write=%s tool_rounds=%s response snippet: %s", response.model, response.stop_reason, total_cost, getattr(usage, 'cache_read_input_tokens', 0), getattr(usage, 'cache_creation_input_tokens', 0), tool_rounds, response_text.replace(chr(10), ' ')[:150]
        )
        cleaned = "\n\n".join(texts + [clean_reply(response_text, bot_id=resolved_bot_id)]).strip()
        if response.stop_reason == "max_tokens" and cleaned:
            logger.warning("[%s] Reply was cut off at max_tokens (%s)", api_model, kwargs["max_tokens"])
            cleaned += CUT_OFF_NOTE
        if code_file_ids:
            files += await self._download_code_files(client, code_file_ids)
        if files:
            return {"text": cleaned, "files": files}, total_cost
        if not cleaned:
            raise AnthropicServiceError("The Claude API returned an empty response.")
        return cleaned, total_cost


# -- Message Batches ------------------------------------------------------------------

# Requests made within this long of each other go in one batch.
BATCH_COLLECT_SECONDS = 2.0
BATCH_POLL_SECONDS = 60.0
# Batches can take up to 24 hours; give up (and cancel) a little before the API would.
BATCH_MAX_WAIT_SECONDS = 23 * 3600
# Batched requests cost half the normal rates, cache reads and writes included.
BATCH_DISCOUNT = 0.5


@dataclass
class _BatchItem:
    custom_id: str
    params: Dict[str, Any]
    model: DirectModel
    future: "asyncio.Future[Tuple[str, float]]"


class MessageBatcher:
    """Sends Claude API requests through the Message Batches API: half price, but slower.

    :meth:`complete` looks like one request to the caller. Requests made within
    ``BATCH_COLLECT_SECONDS`` of each other go in one batch, which is polled until it
    ends. A request that fails in the batch (errored, expired, or the batch couldn't be
    sent) raises ``AnthropicServiceError``, so the caller can retry it normally.
    """

    def __init__(
        self, service: "AnthropicService", *,
        collect_seconds: float = BATCH_COLLECT_SECONDS, poll_seconds: float = BATCH_POLL_SECONDS,
        max_wait_seconds: float = BATCH_MAX_WAIT_SECONDS,
    ):
        self._service = service
        self._collect_seconds = collect_seconds
        self._poll_seconds = poll_seconds
        self._max_wait_seconds = max_wait_seconds
        self._pending: List[_BatchItem] = []
        self._flush: Optional[asyncio.Task] = None
        self._tasks: set = set()
        self._next_id = 0

    async def complete(
        self, *, model: str, system_prompt: str, user_text: str, effort: Optional[str] = None,
    ) -> Tuple[str, float]:
        """Queue one single-turn request and wait for its ``(text, cost)``."""
        direct_model = DIRECT_MODELS.get(model) or DIRECT_MODELS["sonnet-5.5-direct"]
        params: Dict[str, Any] = {
            "model": direct_model.api_id,
            "max_tokens": MAX_TOKENS,
            "output_config": {"effort": effort or direct_model.effort},
            "messages": [{"role": "user", "content": [{"type": "text", "text": user_text}]}],
        }
        if system_prompt:
            params["system"] = [{"type": "text", "text": system_prompt, "cache_control": _EPHEMERAL}]
        self._next_id += 1
        item = _BatchItem(f"req-{self._next_id}", params, direct_model, asyncio.get_running_loop().create_future())
        self._pending.append(item)
        if self._flush is None or self._flush.done():
            self._flush = asyncio.create_task(self._flush_soon())
        return await item.future

    async def _flush_soon(self) -> None:
        await asyncio.sleep(self._collect_seconds)
        items, self._pending = self._pending, []
        if items:
            # Its own task, so requests made while this batch runs start the next one.
            task = asyncio.create_task(self._run(items))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run(self, items: List[_BatchItem]) -> None:
        by_id = {item.custom_id: item for item in items}
        try:
            await self._run_batch(by_id)
        except Exception as e:
            logger.warning("[batch] Batch of %s failed: %s", len(items), e)
        finally:
            for item in by_id.values():
                if not item.future.done():
                    item.future.set_exception(AnthropicServiceError("The batched request didn't complete."))

    async def _run_batch(self, by_id: Dict[str, _BatchItem]) -> None:
        client = self._service.get_client()
        batch = await client.beta.messages.batches.create(
            requests=[{"custom_id": cid, "params": item.params} for cid, item in by_id.items()])
        logger.info("[batch] Sent %s with %s requests", batch.id, len(by_id))
        waited = 0.0
        while getattr(batch, "processing_status", None) != "ended":
            if waited >= self._max_wait_seconds:
                logger.warning("[batch] %s took too long; cancelling it", batch.id)
                await client.beta.messages.batches.cancel(batch.id)
                return
            await asyncio.sleep(self._poll_seconds)
            waited += self._poll_seconds
            batch = await client.beta.messages.batches.retrieve(batch.id)
        total = 0.0
        async for entry in await client.beta.messages.batches.results(batch.id):
            item = by_id.get(entry.custom_id)
            if item is None or item.future.done():
                continue
            result = entry.result
            if getattr(result, "type", None) != "succeeded":
                item.future.set_exception(AnthropicServiceError(f"Batched request {getattr(result, 'type', '?')}."))
                continue
            message = result.message
            served_by = _MODELS_BY_API_ID.get(getattr(message, "model", None) or "", item.model)
            cost = _usage_cost(getattr(message, "usage", None), served_by) * BATCH_DISCOUNT
            total += cost
            await record_cost(CLAUDE_DIRECT, cost)
            if getattr(message, "stop_reason", None) == "refusal":
                item.future.set_exception(AnthropicServiceError("Claude declined this request."))
                continue
            item.future.set_result((_response_text(message), cost))
        logger.info("[batch] %s ended: %s requests, $%.4f", batch.id, len(by_id), total)

