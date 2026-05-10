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
import warnings
from typing import Any, Dict, List, Optional, Union

import discord

from src.config import IS_TESTING
from src.utils.http_client import create_async_client
from src.utils.message_utils import clean_openai_response

logger = logging.getLogger(__name__)

# Suppress known google-genai SDK warnings that are expected/harmless
warnings.filterwarnings("ignore", message="Interactions usage is experimental")
warnings.filterwarnings("ignore", message="Async interactions client cannot use aiohttp")

# Gemini 3.1 Flash Lite pricing (paid tier, per 1M tokens)
PRICE_INPUT_PER_M = 0.25   # text / image / video
PRICE_OUTPUT_PER_M = 1.50  # output including thinking tokens
PRICE_CACHED_PER_M = 0.025 # cached tokens

GEMINI_MODEL = "gemini-3.1-flash-lite"

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
                from google.genai import types as genai_types
                self._client = genai.Client(
                    api_key=self._api_key,
                    http_options=genai_types.HttpOptions(
                        httpxAsyncClient=create_async_client(
                            service_name="gemini",
                        ),
                    ),
                )
        return self._client

    def _build_input(self, messages: List[Dict[str, Any]], has_prev_interaction: bool) -> list:
        """
        Build the input array for Gemini 3 Interactions API.
        The Interactions API only expects the *current* user turn.
        If we don't have a previous_interaction_id but we have conversation history,
        we synthesize the history into the current turn.
        """
        if not messages:
            return [{"type": "text", "text": " "}]
            
        current_msg = messages[-1]
        
        # If no previous interaction but we have history, prepend transcript
        messages_to_transcript = []
        if has_prev_interaction:
            # Find the last 'assistant' or 'model' message
            last_bot_idx = -1
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") in ("assistant", "model"):
                    last_bot_idx = i
                    break
            
            if last_bot_idx != -1:
                # Include everything AFTER the last bot message, EXCEPT the current message
                messages_to_transcript = messages[last_bot_idx + 1:-1]
            else:
                messages_to_transcript = messages[:-1]
        elif len(messages) > 1:
            # Include ALL past messages EXCEPT the current message
            messages_to_transcript = messages[:-1]

        transcript = ""
        if messages_to_transcript:
            transcript = "The following messages occurred since your last response:\n\n"
            for msg in messages_to_transcript:
                role = "User" if msg.get("role") == "user" else "Assistant"
                # simplify content to string for transcript
                content_str = ""
                content = msg.get("content", "")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            content_str += part.get("text", "") + "\n"
                        elif isinstance(part, str):
                            content_str += part + "\n"
                        else:
                            content_str += "[Media attachment]\n"
                else:
                    content_str = str(content)
                transcript += f"{role}: {content_str.strip()}\n\n"
            transcript += "--- End missed context ---\n\n"

        # Build parts for current message
        parts = []
        content = current_msg.get("content", "")
        if isinstance(content, list):
            for i, part in enumerate(content):
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        text_val = part.get("text", "")
                        if i == 0 and transcript:
                            text_val = transcript + text_val
                            transcript = "" # only prepend once
                        parts.append({"type": "text", "text": text_val})
                    elif part.get("type") == "image_url":
                        image_url = part.get("image_url", {}).get("url", "")
                        if image_url.startswith("data:"):
                            try:
                                header, b64data = image_url.split(",", 1)
                                mime_type = header.split(":")[1].split(";")[0]
                                parts.append({
                                    "type": "image",
                                    "data": b64data,
                                    "mime_type": mime_type,
                                })
                            except (ValueError, IndexError):
                                pass
                        else:
                            parts.append({
                                "type": "image",
                                "uri": image_url,
                                "mime_type": "image/jpeg",
                            })
                elif isinstance(part, str):
                    text_val = part
                    if i == 0 and transcript:
                        text_val = transcript + text_val
                        transcript = ""
                    parts.append({"type": "text", "text": text_val})
        else:
            text_val = str(content)
            if transcript:
                text_val = transcript + text_val
            parts.append({"type": "text", "text": text_val})
            
        return parts

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
    ) -> tuple[Union[str, Dict[str, Any], None], float]:
        """
        Get a chat completion using the Gemini Interactions API.

        Returns either a plain string, or a dict {"text": ..., "files": [...]}
        if the response includes images.
        """
        client = self._get_client()

        max_retries = 3
        base_delay = 1.0

        # Check for a cached previous interaction ID for this channel
        prev_interaction_id = self._get_previous_interaction_id(channel_id)
        if prev_interaction_id:
            logger.info(f"[gemini] Continuing conversation with previous_interaction_id={prev_interaction_id[:20]}...")

        # Build input for the current turn
        input_parts = self._build_input(messages, bool(prev_interaction_id))

        for attempt in range(max_retries):
            try:
                # Build kwargs for interactions.create
                kwargs = {
                    "model": GEMINI_MODEL,
                    "input": input_parts,
                    "tools": [
                        {"type": "google_search"},
                        {"type": "code_execution"},
                        {"type": "url_context"}],
                    "store": True,
                }

                if system_prompt:
                    kwargs["system_instruction"] = system_prompt

                if prev_interaction_id:
                    kwargs["previous_interaction_id"] = prev_interaction_id

                # Build generation_config
                gen_config = {}
                if thinking_level:
                    gen_config["thinking_level"] = thinking_level
                if gen_config:
                    kwargs["generation_config"] = gen_config

                # Log the prompt snippet
                last_content = input_parts[-1].get("text", "") if input_parts else ""
                snippet_trunc = str(last_content).replace("\n", " ")[:150]
                logger.info(f"[gemini/{GEMINI_MODEL}] Prompt Snippet: {snippet_trunc}{'...' if len(str(last_content)) > 150 else ''}")

                interaction = await client.aio.interactions.create(**kwargs)

                if interaction is None:
                    raise GeminiServiceError("Interactions API returned None")

                # Convert to dict to robustly handle API structure variations (steps vs outputs)
                try:
                    interaction_dict = interaction.model_dump()
                except AttributeError:
                    try:
                        interaction_dict = interaction.dict()
                    except AttributeError:
                        interaction_dict = vars(interaction)

                # Extract outputs
                text_parts = []
                image_files = []

                def process_output_item(item):
                    if not isinstance(item, dict):
                        # Fallback for unexpected objects
                        item = vars(item) if hasattr(item, "__dict__") else {}
                        
                    output_type = item.get("type", "")
                    if output_type == "text" or ("text" in item and item["text"]):
                        text_parts.append(item.get("text", ""))
                    elif output_type == "image" and "data" in item:
                        try:
                            img_data = base64.b64decode(item["data"])
                            ext = "png"
                            mime = item.get("mime_type", "image/png")
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
                        summary = item.get("summary", "")
                        if summary:
                            logger.info(f"[gemini] Thinking: {summary[:200]}")

                if "steps" in interaction_dict and interaction_dict["steps"]:
                    # Gemini 3.1 Interactions API docs suggest content is nested in steps
                    last_step = interaction_dict["steps"][-1]
                    for item in last_step.get("content", []):
                        process_output_item(item)
                elif "outputs" in interaction_dict and interaction_dict["outputs"]:
                    # Fallback for alternative or legacy schema
                    for item in interaction_dict["outputs"]:
                        process_output_item(item)

                response_text = "\n".join(text_parts) if text_parts else ""

                if not response_text and not image_files:
                    logger.error(f"⚠️ Gemini response had no text or images. Raw keys: {list(interaction_dict.keys())}")
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

                # Log snippet
                text_snippet = response_text.strip().replace("\n", " ")[:150] if response_text else "(empty)"
                logger.info(f"[gemini/{GEMINI_MODEL}] response snippet: {text_snippet}{'...' if len(response_text or '') > 150 else ''}")


                # Cache the interaction ID for future turns
                interaction_id = getattr(interaction, 'id', None)
                if interaction_id and channel_id:
                    self._update_interaction_cache(channel_id, interaction_id)
                    logger.info(f"[gemini] Cached interaction_id={interaction_id[:20]}... for channel {channel_id}")

                # Return according to response format
                if image_files:
                    return {"text": response_text or "Here's what I generated:", "files": image_files}, cost
                return response_text, cost

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
