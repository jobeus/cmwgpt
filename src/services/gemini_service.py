"""
Gemini Service - Handles Google Gemini API interactions via the Interactions API.

Uses the google-genai SDK's Interactions API (client.aio.interactions.create)
to communicate directly with Google's Gemini models, bypassing OpenRouter.
"""

import asyncio
import base64
import io
import logging
import os
import time
from typing import Any, Dict, List, Optional, Union

import discord

from src.config import IS_TESTING
from src.db.logger import log_api_request
from src.utils.message_utils import clean_openai_response

logger = logging.getLogger(__name__)

# Gemini 3.1 Flash Lite pricing (paid tier, per 1M tokens)
PRICE_INPUT_PER_M = 0.25   # text / image / video
PRICE_OUTPUT_PER_M = 1.50  # output including thinking tokens
PRICE_CACHED_PER_M = 0.025 # cached tokens

GEMINI_MODEL = "gemini-3.1-flash-lite-preview"

# How long before a channel's cached interaction ID expires (seconds)
INTERACTION_CACHE_TTL = 600  # 10 minutes


class GeminiServiceError(Exception):
    """Custom exception for Gemini service errors."""
    pass


class GeminiService:
    """Service for handling Google Gemini Interactions API calls."""

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self._client = None
        self._bot_id_loader = None
        # Per-channel cache: {channel_id: {"interaction_id": str, "timestamp": float}}
        self._interaction_cache: Dict[int, Dict[str, Any]] = {}

    def set_bot_id_loader(self, bot_id_loader):
        """Set a callable that returns the current bot user ID."""
        self._bot_id_loader = bot_id_loader

    def _get_previous_interaction_id(self, channel_id: Optional[int]) -> Optional[str]:
        """Get the cached interaction ID for a channel, if still fresh."""
        if not channel_id:
            return None
        cached = self._interaction_cache.get(channel_id)
        if not cached:
            return None
        if time.time() - cached["timestamp"] > INTERACTION_CACHE_TTL:
            # Expired — discard
            del self._interaction_cache[channel_id]
            logger.info(f"[gemini] Interaction cache expired for channel {channel_id}")
            return None
        return cached["interaction_id"]

    def _update_interaction_cache(self, channel_id: Optional[int], interaction_id: str) -> None:
        """Store the latest interaction ID for a channel."""
        if not channel_id:
            return
        self._interaction_cache[channel_id] = {
            "interaction_id": interaction_id,
            "timestamp": time.time(),
        }

    def clear_channel_cache(self, channel_id: int) -> None:
        """Clear the cached interaction for a channel (e.g. on model change or /reset)."""
        self._interaction_cache.pop(channel_id, None)
        logger.info(f"[gemini] Cleared interaction cache for channel {channel_id}")

    def _get_client(self):
        """Lazy-init the google-genai client."""
        if self._client is None:
            if IS_TESTING:
                class MockClient:
                    class _aio:
                        class interactions:
                            @staticmethod
                            async def create(**kwargs):
                                return None
                    aio = _aio()
                self._client = MockClient()
            else:
                from google import genai
                self._client = genai.Client(api_key=self._api_key)
        return self._client

    def _convert_messages_to_input(
        self, messages: List[Dict[str, Any]], system_prompt: str
    ) -> list:
        """
        Convert OpenAI-format messages to Interactions API input format.

        The Interactions API expects input as a list of turns with 'role' and 'content'.
        Roles: 'user' or 'model' (not 'assistant').
        Content can be a string or a list of content objects with 'type' field.
        """
        input_turns = []

        # Add system prompt as first user turn (Interactions API doesn't have
        # a dedicated system instruction field — we prepend it)
        if system_prompt:
            input_turns.append({
                "role": "user",
                "content": f"[System Instructions — follow these for all responses]\n{system_prompt}"
            })
            input_turns.append({
                "role": "model",
                "content": "Understood. I'll follow those instructions."
            })

        for msg in messages:
            role = msg.get("role", "user")
            if role == "system":
                continue  # Already handled above
            # Map 'assistant' -> 'model' for Gemini
            if role == "assistant":
                role = "model"

            content = msg.get("content", "")

            # Handle list-of-parts content (OpenAI multimodal format)
            if isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            parts.append({
                                "type": "text",
                                "text": part.get("text", "")
                            })
                        elif part.get("type") == "image_url":
                            image_url = part.get("image_url", {}).get("url", "")
                            if image_url.startswith("data:"):
                                # Parse data URL: data:mime_type;base64,DATA
                                try:
                                    header, b64data = image_url.split(",", 1)
                                    mime_type = header.split(":")[1].split(";")[0]
                                    parts.append({
                                        "type": "image",
                                        "data": b64data,
                                        "mime_type": mime_type,
                                    })
                                except (ValueError, IndexError):
                                    logger.warning(f"Failed to parse image data URL, skipping")
                            else:
                                # Remote URL
                                parts.append({
                                    "type": "image",
                                    "uri": image_url,
                                    "mime_type": "image/jpeg",
                                })
                    elif isinstance(part, str):
                        parts.append({"type": "text", "text": part})

                if parts:
                    input_turns.append({"role": role, "content": parts})
            elif isinstance(content, str):
                if content.strip():
                    input_turns.append({"role": role, "content": content})
            else:
                # Fallback
                input_turns.append({"role": role, "content": str(content)})

        return input_turns

    def _estimate_cost(self, usage: Any) -> float:
        """Estimate cost from Interactions API usage metadata."""
        if not usage:
            return 0.0

        try:
            # The usage object has attributes like total_input_tokens, etc.
            input_tokens = getattr(usage, 'total_input_tokens', 0) or 0
            output_tokens = getattr(usage, 'total_output_tokens', 0) or 0
            thought_tokens = getattr(usage, 'total_thought_tokens', 0) or 0
            cached_tokens = getattr(usage, 'total_cached_tokens', 0) or 0

            # If attributes don't exist, try dict access
            if isinstance(usage, dict):
                input_tokens = usage.get('total_input_tokens', 0) or 0
                output_tokens = usage.get('total_output_tokens', 0) or 0
                thought_tokens = usage.get('total_thought_tokens', 0) or 0
                cached_tokens = usage.get('total_cached_tokens', 0) or 0

            input_cost = input_tokens * PRICE_INPUT_PER_M / 1_000_000
            output_cost = (output_tokens + thought_tokens) * PRICE_OUTPUT_PER_M / 1_000_000
            cached_cost = cached_tokens * PRICE_CACHED_PER_M / 1_000_000

            return input_cost + output_cost + cached_cost
        except Exception as e:
            logger.warning(f"Failed to estimate Gemini cost: {e}")
            return 0.0

    async def get_chat_completion(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        system_prompt: str = None,
        channel_id: int = None,
        discord_user_id: Optional[int] = None,
        bot_id: int = None,
        thinking_level: Optional[str] = None,
        # Accept and ignore extra kwargs for compatibility
        state_service: Any = None,
    ) -> Union[str, Dict[str, Any], None]:
        """
        Get a chat completion using the Gemini Interactions API.

        Returns either a plain string, or a dict {"text": ..., "files": [...]}
        if the response includes images.
        """
        client = self._get_client()

        input_turns = self._convert_messages_to_input(messages, system_prompt)

        max_retries = 3
        base_delay = 1.0

        # Check for a cached previous interaction ID for this channel
        prev_interaction_id = self._get_previous_interaction_id(channel_id)
        if prev_interaction_id:
            logger.info(f"[gemini] Continuing conversation with previous_interaction_id={prev_interaction_id[:20]}...")

        for attempt in range(max_retries):
            try:
                # Build kwargs for interactions.create
                kwargs = {
                    "model": GEMINI_MODEL,
                    "input": input_turns,
                    "tools": [
                        {"type": "google_search"},
                        {"type": "code_execution"},
                        {"type": "url_context"}],
                    "store": True,
                }

                if prev_interaction_id:
                    kwargs["previous_interaction_id"] = prev_interaction_id

                # Build generation_config
                gen_config = {}
                if thinking_level:
                    gen_config["thinking_level"] = thinking_level
                if gen_config:
                    kwargs["generation_config"] = gen_config

                # Log the prompt snippet
                last_turn = input_turns[-1] if input_turns else {}
                last_content = last_turn.get("content", "")
                if isinstance(last_content, list):
                    snippet = next(
                        (p.get("text", "") for p in last_content if isinstance(p, dict) and p.get("type") == "text"),
                        "(multimodal)"
                    )
                elif isinstance(last_content, str):
                    snippet = last_content
                else:
                    snippet = str(last_content)
                snippet_trunc = snippet.replace("\n", " ")[:150]
                logger.info(f"[gemini/{GEMINI_MODEL}] Prompt Snippet: {snippet_trunc}{'...' if len(snippet) > 150 else ''}")

                interaction = await client.aio.interactions.create(**kwargs)

                if interaction is None:
                    raise GeminiServiceError("Interactions API returned None")

                # Extract outputs
                text_parts = []
                image_files = []

                for output in interaction.outputs:
                    output_type = getattr(output, 'type', None)
                    if output_type == "text":
                        text_parts.append(output.text)
                    elif output_type == "image":
                        # Image output: has data (base64) and mime_type
                        try:
                            img_data = base64.b64decode(output.data)
                            ext = "png"
                            mime = getattr(output, 'mime_type', 'image/png')
                            if "jpeg" in mime or "jpg" in mime:
                                ext = "jpg"
                            elif "webp" in mime:
                                ext = "webp"
                            elif "gif" in mime:
                                ext = "gif"

                            file = discord.File(
                                io.BytesIO(img_data),
                                filename=f"gemini_output.{ext}"
                            )
                            image_files.append(file)
                        except Exception as e:
                            logger.error(f"Failed to process image output: {e}")
                    elif output_type == "thought":
                        # Just log thinking summaries, don't include in response
                        summary = getattr(output, 'summary', None)
                        if summary:
                            logger.info(f"[gemini] Thinking: {summary[:200]}")
                    # Skip other types (google_search_result, etc.)

                response_text = "\n".join(text_parts) if text_parts else ""

                if not response_text and not image_files:
                    logger.error(f"⚠️ Gemini response had no text or images. Raw: {interaction}")
                    raise ValueError("Gemini returned an empty response.")

                # Clean the response text
                effective_bot_id = bot_id
                if not effective_bot_id and self._bot_id_loader:
                    try:
                        effective_bot_id = self._bot_id_loader()
                    except Exception:
                        pass

                if response_text:
                    response_text = clean_openai_response(response_text, bot_id=effective_bot_id)

                # Calculate cost
                cost = 0.0
                usage = getattr(interaction, 'usage', None)
                if usage:
                    cost = self._estimate_cost(usage)
                    logger.info(f"[gemini] Usage: {usage}, Estimated cost: ${cost:.4f}")

                if cost > 0 and response_text:
                    response_text = f"[${cost:.3f}] {response_text}"

                # Log snippet
                text_snippet = response_text.strip().replace("\n", " ")[:150] if response_text else "(empty)"
                logger.info(f"[gemini/{GEMINI_MODEL}] response snippet: {text_snippet}{'...' if len(response_text or '') > 150 else ''}")

                # Log to DB
                await log_api_request(
                    service_name=f"gemini/{GEMINI_MODEL}",
                    method="POST",
                    endpoint_url="https://generativelanguage.googleapis.com/v1beta/interactions",
                    request_headers={},
                    request_body={"model": GEMINI_MODEL, "input_turns": len(input_turns), "thinking_level": thinking_level},
                    response_status=200,
                    response_headers={},
                    response_body={"text_length": len(response_text), "image_count": len(image_files), "usage": str(usage)},
                    cost=cost,
                    discord_user_id=discord_user_id,
                    discord_channel_id=channel_id,
                )

                # Cache the interaction ID for future turns
                interaction_id = getattr(interaction, 'id', None)
                if interaction_id and channel_id:
                    self._update_interaction_cache(channel_id, interaction_id)
                    logger.info(f"[gemini] Cached interaction_id={interaction_id[:20]}... for channel {channel_id}")

                # Return according to response format
                if image_files:
                    return {"text": response_text or "Here's what I generated:", "files": image_files}
                return response_text

            except GeminiServiceError:
                # On error, clear the cache in case the interaction ID is stale
                if prev_interaction_id and channel_id:
                    self.clear_channel_cache(channel_id)
                    prev_interaction_id = None
                raise

            except Exception as e:
                error_str = str(e)
                logger.error(f"Gemini API error on attempt {attempt + 1}/{max_retries}: {error_str}")

                # Check for retryable errors
                retryable = any(k in error_str.lower() for k in [
                    "429", "rate limit", "500", "502", "503", "504",
                    "timeout", "connection", "unavailable", "internal"
                ])

                if retryable and attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.info(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                    continue

                if attempt >= max_retries - 1:
                    raise GeminiServiceError(
                        f"Gemini API error after {max_retries} attempts: {error_str}"
                    ) from e
                raise GeminiServiceError(f"Gemini API error: {error_str}") from e

        raise GeminiServiceError("Failed to get Gemini completion after all retry attempts")

    async def close(self) -> None:
        """Clean up resources."""
        self._client = None


# Helper to check if a model string should route to Gemini
GEMINI_MODEL_ALIASES = {"google", "google-high"}


def is_gemini_model(model: str) -> bool:
    """Check if the model string should be handled by the Gemini service."""
    return model in GEMINI_MODEL_ALIASES


def get_thinking_level(model: str) -> Optional[str]:
    """Return the thinking level for a Gemini model alias, or None."""
    if model == "google-high":
        return "high"
    return None
