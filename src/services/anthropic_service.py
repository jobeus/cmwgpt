"""
Anthropic Service - Talks to the Claude API directly (no OpenRouter).

Accepts the same OpenAI-style message array the rest of the bot builds
(``text`` / ``image_url`` parts) and converts it to Messages API content blocks.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import anthropic

from src.config import get_config
from src.utils.message_utils import clean_openai_response, resolve_bot_id

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


# Bot-facing model alias -> Claude API model.
DIRECT_MODELS: Dict[str, DirectModel] = {
    # Sonnet 5.5 recommends ``low`` for chat and search; it still thinks when a message needs it.
    "sonnet-5.5-direct": DirectModel("claude-sonnet-5-5", 2.00, 10.00, 2.50, 0.20, effort="low"),
    # Opus 5.5 can't turn thinking off; its default ``medium`` overthinks chat, so run it at ``low``.
    "opus-5.5-direct": DirectModel("claude-opus-5-5", 4.00, 20.00, 5.00, 0.20, effort="low"),
}
_MODELS_BY_API_ID: Dict[str, DirectModel] = {m.api_id: m for m in DIRECT_MODELS.values()}

PRICE_WEB_SEARCH = 0.01

MAX_TOKENS = 16000
WEB_SEARCH_MAX_USES = 10
# A server-tool turn can come back as ``pause_turn``; resume it this many times.
MAX_CONTINUATIONS = 5

# Cache diagnostics (beta): each request names the previous response in its
# channel, and the API reports where the prompt-cache prefix diverged from it.
# Fingerprints are only kept for requests that carry this beta, so it's sent
# on every request.
CACHE_DIAGNOSIS_BETA = "cache-diagnosis-2026-04-07"

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[\w/+.-]+);base64,(?P<data>.+)$", re.DOTALL)
_SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


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
                logger.debug(f"[anthropic] Skipping unsupported image type {mime}")
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
            converted.append({"role": role, "content": blocks})

    # The conversation must open with a user turn.
    if converted and converted[0]["role"] != "user":
        converted.insert(0, {"role": "user", "content": [{"type": "text", "text": "(earlier conversation)"}]})
    return converted


_EPHEMERAL = {"type": "ephemeral"}


def apply_history_breakpoints(
    api_messages: List[Dict[str, Any]], previous_index: Optional[int]
) -> Optional[int]:
    """Mark cache breakpoints on the history and return this request's history index.

    The last message is the mention. Everything before it is history that the next
    mention in the channel re-sends unchanged, so it gets a breakpoint (written now,
    read next time). The mention itself is left unmarked because its rendering can
    still change (Discord unfurls link embeds a moment after posting). A second
    breakpoint goes where the previous request's history ended, so the read hits even
    when more than 20 blocks (the API's lookback limit) were added since.
    """
    current_index = len(api_messages) - 2
    if current_index < 0:
        return None
    marked = {current_index}
    if previous_index is not None and 0 <= previous_index < current_index:
        marked.add(previous_index)
    for index in marked:
        blocks = api_messages[index]["content"]
        blocks[-1] = {**blocks[-1], "cache_control": _EPHEMERAL}
    return current_index


def _usage_cost(usage: Any, model: DirectModel) -> float:
    """Price a response's ``usage`` block in USD at ``model``'s rates."""
    if usage is None:
        return 0.0
    cost = (
        (getattr(usage, "input_tokens", 0) or 0) * model.price_input
        + (getattr(usage, "output_tokens", 0) or 0) * model.price_output
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * model.price_cache_write
        + (getattr(usage, "cache_read_input_tokens", 0) or 0) * model.price_cache_read
    ) / 1_000_000
    server_tool_use = getattr(usage, "server_tool_use", None)
    searches = (getattr(server_tool_use, "web_search_requests", 0) or 0) if server_tool_use else 0
    return cost + searches * PRICE_WEB_SEARCH


def _log_cache_diagnostics(api_model: str, channel_id: Optional[int], response: Any) -> None:
    """Log why the prompt cache missed, from the response's cache diagnostics."""
    diagnostics = getattr(response, "diagnostics", None)
    if diagnostics is None:
        return
    reason = getattr(diagnostics, "cache_miss_reason", None)
    where = f"[{api_model}] channel={channel_id}"
    if reason is None:
        # Either nothing to report or the comparison hadn't finished yet.
        logger.debug(f"{where} cache diagnostics: no miss reported")
        return
    missed = getattr(reason, "cache_missed_input_tokens", None)
    if missed:
        logger.warning(f"{where} cache miss: {reason.type}, ~{missed} input tokens not read from cache")
    else:
        logger.info(f"{where} cache diagnostics: {reason.type}")


class AnthropicService:
    """Service for handling direct Claude API interactions."""

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key
        self._client: Optional[anthropic.AsyncAnthropic] = None
        self._bot_id_loader: Optional[Callable[[], Optional[int]]] = None
        # channel_id -> index of the last history message marked in the previous request
        self._history_breakpoints: Dict[int, int] = {}
        # channel_id -> id of the previous response, for cache diagnostics
        self._last_message_ids: Dict[int, str] = {}

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

    async def close(self) -> None:
        """Close the Anthropic client and clean up resources."""
        if self._client is not None and hasattr(self._client, "close"):
            try:
                await self._client.close()
                logger.debug("Anthropic client closed")
            except Exception as e:
                logger.error(f"Error closing Anthropic client: {e}")
            finally:
                self._client = None

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
    ) -> Tuple[str, float]:
        """Get a chat completion from the Claude API. Returns ``(text, cost)``.

        With ``on_text``, the reply is streamed and ``on_text`` gets the cleaned text so far.
        ``effort`` overrides the model's default effort level.
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
            "max_tokens": MAX_TOKENS,
            "output_config": {"effort": effort or direct_model.effort},
            # On a safety decline, let the API re-run on its recommended fallback model.
            "betas": ["server-side-fallback-2026-07-01", CACHE_DIAGNOSIS_BETA],
            "fallbacks": "default",
        }
        if system_prompt:
            # Its own breakpoint, so the system prompt still reads from cache when the history doesn't.
            kwargs["system"] = [{"type": "text", "text": system_prompt, "cache_control": _EPHEMERAL}]
        previous_index = self._history_breakpoints.get(channel_id) if channel_id else None
        history_index = apply_history_breakpoints(api_messages, previous_index)
        if search:
            kwargs["tools"] = [{
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": WEB_SEARCH_MAX_USES,
            }]

        last_text = api_messages[-1]["content"][0].get("text", "") if api_messages else ""
        snippet = last_text.replace("\n", " ")
        logger.info(f"[{api_model}] Prompt Snippet: {snippet[:150]}{'...' if len(snippet) > 150 else ''}")

        total_cost = 0.0
        previous_message_id = self._last_message_ids.get(channel_id) if channel_id else None
        streamed = ""
        try:
            response = None
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
                                on_text(clean_openai_response(streamed, bot_id=resolved_bot_id))
                        response = await stream.get_final_message()
                else:
                    response = await client.beta.messages.create(**request)
                # A safety fallback can serve the turn on another model; price it at that model's rates.
                served_by = _MODELS_BY_API_ID.get(getattr(response, "model", None), direct_model)
                total_cost += _usage_cost(getattr(response, "usage", None), served_by)
                _log_cache_diagnostics(api_model, channel_id, response)
                previous_message_id = getattr(response, "id", None) or previous_message_id
                if response.stop_reason != "pause_turn":
                    break
                # Resume a paused server-tool turn by sending its content back.
                api_messages = api_messages + [{"role": "assistant", "content": response.content}]
        except anthropic.AuthenticationError as e:
            logger.error(f"Anthropic authentication error: {e}")
            raise AnthropicServiceError("Authentication failed. Please check ANTHROPIC_API_KEY.") from e
        except anthropic.BadRequestError as e:
            logger.error(f"Anthropic bad request: {e}")
            raise AnthropicServiceError(f"Invalid request: {e.message}") from e
        except anthropic.RateLimitError as e:
            logger.error(f"Anthropic rate limit after retries: {e}")
            raise AnthropicServiceError("Rate limit exceeded. Please try again later.") from e
        except anthropic.APIStatusError as e:
            logger.error(f"Anthropic API error ({e.status_code}): {e}")
            raise AnthropicServiceError(f"Claude API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            logger.error(f"Anthropic connection error: {e}")
            raise AnthropicServiceError("Connection to the Claude API failed. Please try again later.") from e

        if response is None:
            raise AnthropicServiceError("The Claude API returned no response.")
        if channel_id and history_index is not None:
            self._history_breakpoints[channel_id] = history_index
        if channel_id and previous_message_id:
            self._last_message_ids[channel_id] = previous_message_id

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            logger.warning(f"[{api_model}] Request declined (category={category})")
            raise AnthropicServiceError(f"Claude declined this request (category: {category or 'unknown'}).")

        response_text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ).strip()
        usage = getattr(response, "usage", None)
        logger.info(
            f"[{response.model}] stop_reason={response.stop_reason} cost=${total_cost:.4f} "
            f"cache_read={getattr(usage, 'cache_read_input_tokens', 0)} "
            f"cache_write={getattr(usage, 'cache_creation_input_tokens', 0)} "
            f"response snippet: {response_text.replace(chr(10), ' ')[:150]}"
        )
        if not response_text:
            raise AnthropicServiceError("The Claude API returned an empty response.")

        cleaned = clean_openai_response(response_text, bot_id=resolved_bot_id)
        return cleaned, total_cost
