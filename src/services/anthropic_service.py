"""
Anthropic Service - Talks to the Claude API directly (no OpenRouter).

Accepts the same OpenAI-style message array the rest of the bot builds
(``text`` / ``image_url`` parts) and converts it to Messages API content blocks.
"""

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import anthropic

from src.config import get_config
from src.utils.http_client import create_async_httpx2_client
from src.utils.message_utils import clean_openai_response

logger = logging.getLogger(__name__)

# Bot-facing model alias -> Claude API model id.
DIRECT_MODELS: Dict[str, str] = {
    "sonnet-5.5-direct": "claude-sonnet-5-5",
}

# Claude Sonnet 5.5 pricing (USD per 1M tokens) and web search (per search).
PRICE_INPUT_PER_M = 2.00
PRICE_OUTPUT_PER_M = 10.00
PRICE_CACHE_WRITE_PER_M = 2.50
PRICE_CACHE_READ_PER_M = 0.20
PRICE_WEB_SEARCH = 0.01

MAX_TOKENS = 16000
EFFORT = "medium"
WEB_SEARCH_MAX_USES = 5
# A web-search turn can come back as ``pause_turn``; resume it this many times.
MAX_CONTINUATIONS = 3

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
        if isinstance(content, str) and content.strip().startswith(("[", "{")):
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, ValueError):
                pass

        parts = content if isinstance(content, list) else [content]
        blocks = [b for b in (_convert_part(p, role) for p in parts) if b is not None]
        if blocks:
            converted.append({"role": role, "content": blocks})

    # The conversation must open with a user turn.
    if converted and converted[0]["role"] != "user":
        converted.insert(0, {"role": "user", "content": [{"type": "text", "text": "(earlier conversation)"}]})
    return converted


def _usage_cost(usage: Any) -> float:
    """Price a response's ``usage`` block in USD."""
    if usage is None:
        return 0.0
    cost = (
        (getattr(usage, "input_tokens", 0) or 0) * PRICE_INPUT_PER_M
        + (getattr(usage, "output_tokens", 0) or 0) * PRICE_OUTPUT_PER_M
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * PRICE_CACHE_WRITE_PER_M
        + (getattr(usage, "cache_read_input_tokens", 0) or 0) * PRICE_CACHE_READ_PER_M
    ) / 1_000_000
    server_tool_use = getattr(usage, "server_tool_use", None)
    searches = (getattr(server_tool_use, "web_search_requests", 0) or 0) if server_tool_use else 0
    return cost + searches * PRICE_WEB_SEARCH


class AnthropicService:
    """Service for handling direct Claude API interactions."""

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key
        self._client: Optional[anthropic.AsyncAnthropic] = None
        self._bot_id_loader: Optional[Callable[[], Optional[int]]] = None

    def set_client(self, client) -> None:
        """Set a custom client (useful for testing)."""
        self._client = client

    def set_bot_id_loader(self, bot_id_loader: Optional[Callable[[], Optional[int]]]) -> None:
        """Set a callable that returns the current bot user ID."""
        self._bot_id_loader = bot_id_loader

    def get_client(self) -> anthropic.AsyncAnthropic:
        """Get the Anthropic client with lazy initialization."""
        if self._client is None:
            if get_config().is_testing:
                class MockClient:
                    def __getattr__(self, name):  # noqa: ARG002
                        return lambda *args, **kwargs: None  # noqa: ARG005

                self._client = MockClient()
            else:
                api_key = self._api_key or get_config().anthropic_api_key
                if not api_key:
                    raise AnthropicServiceError("ANTHROPIC_API_KEY is not configured.")
                self._client = anthropic.AsyncAnthropic(
                    api_key=api_key,
                    max_retries=3,
                    http_client=create_async_httpx2_client(
                        service_name="anthropic",
                        timeout=600.0,
                    ),
                )
        return self._client

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

    def _resolve_bot_id(self, bot_id: Optional[int], state_service: Any) -> Optional[int]:
        if bot_id:
            return bot_id
        try:
            if state_service and hasattr(state_service, "bot_id"):
                return state_service.bot_id
            if self._bot_id_loader:
                return self._bot_id_loader()
        except Exception as e:
            logger.warning(f"Failed to get bot_id for cleaning: {e}")
        return None

    async def get_chat_completion(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        channel_id: Optional[int] = None,  # noqa: ARG002 - identity kwarg shared by all services
        state_service: Any = None,
        bot_id: Optional[int] = None,
        discord_user_id: Optional[int] = None,  # noqa: ARG002 - identity kwarg shared by all services
        search: bool = True,
    ) -> Tuple[str, float]:
        """Get a chat completion from the Claude API. Returns ``(text, cost)``."""
        client = self.get_client()
        api_model = DIRECT_MODELS.get(model, model)
        api_messages = build_anthropic_messages(messages)
        if not api_messages:
            raise AnthropicServiceError("No usable messages to send to Claude.")

        kwargs: Dict[str, Any] = {
            "model": api_model,
            "max_tokens": MAX_TOKENS,
            "output_config": {"effort": EFFORT},
            # On a safety decline, let the API re-run on its recommended fallback model.
            "betas": ["server-side-fallback-2026-07-01"],
            "fallbacks": "default",
        }
        if system_prompt:
            kwargs["system"] = system_prompt
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
        try:
            response = None
            for _ in range(MAX_CONTINUATIONS + 1):
                response = await client.beta.messages.create(messages=api_messages, **kwargs)
                total_cost += _usage_cost(getattr(response, "usage", None))
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

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            logger.warning(f"[{api_model}] Request declined (category={category})")
            raise AnthropicServiceError(f"Claude declined this request (category: {category or 'unknown'}).")

        response_text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ).strip()
        logger.info(
            f"[{response.model}] stop_reason={response.stop_reason} cost=${total_cost:.4f} "
            f"response snippet: {response_text.replace(chr(10), ' ')[:150]}"
        )
        if not response_text:
            raise AnthropicServiceError("The Claude API returned an empty response.")

        cleaned = clean_openai_response(response_text, bot_id=self._resolve_bot_id(bot_id, state_service))
        return cleaned, total_cost
