"""
OpenAI Service - Handles all OpenAI API interactions
"""

import asyncio
import json
import logging
import os
import tempfile
from typing import List, Dict, Any, Optional, Callable

from openai import (
    AsyncOpenAI,
    APIError,
    RateLimitError,
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
)


from src.config import get_config
from src.utils.message_utils import clean_openai_response, resolve_bot_id

logger = logging.getLogger(__name__)

SDK_MAX_RETRIES = 3
# Upstream errors OpenRouter reports inside an HTTP 200, worth one more try.
RETRYABLE_SOFT_ERRORS = {429, 500, 502, 503, 504}
SOFT_RETRY_ATTEMPTS = 2
SOFT_RETRY_DELAY_SECONDS = 1.0


class OpenAIServiceError(Exception):
    """Custom exception for OpenAI service errors."""
    pass


class OpenAIService:
    """Service for handling OpenAI API interactions."""

    def __init__(self):
        self._client: Optional[AsyncOpenAI] = None
        self._bot_id_loader: Optional[Callable[[], Optional[int]]] = None

    def set_client(self, client) -> None:
        """Set a custom client (useful for testing)."""
        self._client = client

    def set_bot_id_loader(self, bot_id_loader: Optional[Callable[[], Optional[int]]]) -> None:
        """Set a callable that returns the current bot user ID."""
        self._bot_id_loader = bot_id_loader

    def get_client(self) -> AsyncOpenAI:
        """Get OpenAI client with lazy initialization."""
        if self._client is None:
            # The SDK retries connection errors, 408/409/429 and 5xx with backoff.
            self._client = AsyncOpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=get_config().openrouter_api_key,
                default_headers={
                    "HTTP-Referer": "https://github.com/jobeus/cmwgpt",
                    "X-Title": "CMWGPT Discord Bot"
                },
                max_retries=SDK_MAX_RETRIES,
            )
        return self._client

    def _dump_bad_request(self, kwargs_dict: Dict[str, Any], client: AsyncOpenAI) -> None:
        """Dump the payload and a replay script to the system temp directory."""
        import shlex

        temp_dir = tempfile.gettempdir()
        payload_path = os.path.join(temp_dir, "bad_request.json")
        script_path = os.path.join(temp_dir, "bad_request.sh")

        try:
            with open(payload_path, "w", encoding="utf-8") as f:
                json.dump(kwargs_dict, f, indent=2)

            curl_cmd = "curl https://openrouter.ai/api/v1/chat/completions \\\n"

            # Extract headers
            headers_dict = dict(client.default_headers) if hasattr(client, 'default_headers') else {}
            # Ensure Auth is present
            if hasattr(client, 'api_key') and client.api_key:
                headers_dict['Authorization'] = f"Bearer {client.api_key}"
            elif get_config().openrouter_api_key:
                headers_dict['Authorization'] = f"Bearer {get_config().openrouter_api_key}"

            for k, v in headers_dict.items():
                if 'Omit' in str(type(v)):
                    continue
                header_str = f"{k}: {v}"
                curl_cmd += f"  -H {shlex.quote(header_str)} \\\n"

            curl_cmd += f"  -d {shlex.quote(f'@{payload_path}')}\n"

            with open(script_path, "w", encoding="utf-8") as f:
                f.write("#!/bin/bash\n\n" + curl_cmd)

            os.chmod(script_path, 0o700)
            logger.info("Saved bad request dump to %s and %s", payload_path, script_path)
        except (OSError, TypeError, ValueError) as exc:
            logger.error(f"Failed to dump bad request: {exc}")

    async def close(self) -> None:
        """Close the OpenAI client and clean up resources."""
        if self._client is not None and hasattr(self._client, 'close'):
            try:
                await self._client.close()
                logger.debug("OpenAI client closed")
            except Exception as e:
                logger.error(f"Error closing OpenAI client: {e}")
            finally:
                self._client = None

    async def get_chat_completion(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        system_prompt: str = None,
        channel_id: int = None,
        state_service: Any = None,
        bot_id: int = None,
        discord_user_id: Optional[int] = None,
        search: bool = True,
    ) -> tuple[str, float]:
        """
        Gets a chat completion from OpenRouter's Chat Completions API.

        Transport-level failures are retried by the SDK itself; this loop only
        retries what the SDK can't see: an upstream error returned inside an
        HTTP 200, or an empty reply.
        """
        client = self.get_client()

        api_input: List[Dict[str, Any]] = []
        if system_prompt:
            api_input.append({"role": "system", "content": system_prompt})
        for msg in messages:
            if msg.get("role") == "system":
                continue
            content = msg.get("content", "")
            if isinstance(content, list):
                # OpenRouter only takes text and image parts here (no audio).
                content = [p for p in content if not isinstance(p, dict) or p.get("type") in ("text", "image_url")]
            api_input.append({"role": msg.get("role"), "content": content})

        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": api_input,
            "extra_body": {"provider": {"sort": "price"}},
        }
        if search and model == "anthropic/claude-sonnet-5.5":
            kwargs["extra_body"]["plugins"] = [{"id": "web", "engine": "native"}]

        logger.info(f"[{model}] Prompt Snippet: {_prompt_snippet(api_input)}")

        for attempt in range(1, SOFT_RETRY_ATTEMPTS + 1):
            try:
                response = await client.chat.completions.create(**kwargs)
            except AuthenticationError as e:
                logger.error(f"Authentication error: {e}")
                raise OpenAIServiceError("Authentication failed. Please check API key configuration.") from e
            except BadRequestError as e:
                logger.error(f"Bad request error: {e}")
                self._dump_bad_request(kwargs, client)
                raise OpenAIServiceError(f"Invalid request: {getattr(e, 'message', str(e))}") from e
            except RateLimitError as e:
                raise OpenAIServiceError("Rate limit exceeded. Please try again later.") from e
            except APIConnectionError as e:
                raise OpenAIServiceError("Connection to OpenRouter failed. Please try again later.") from e
            except APIError as e:
                self._dump_bad_request(kwargs, client)
                raise OpenAIServiceError(f"OpenRouter API error: {e}") from e

            error_data = getattr(response, "error", None)
            if isinstance(error_data, dict):
                error_code = error_data.get("code")
                error_message = error_data.get("message", "Unknown Error")
                logger.error(f"Soft error in HTTP 200 (attempt {attempt}): {error_code} - {error_message}")
                logger.error(f"Full soft-error data: {json.dumps(error_data, indent=2, default=str)}")
                self._dump_bad_request(kwargs, client)
                if error_code in RETRYABLE_SOFT_ERRORS and attempt < SOFT_RETRY_ATTEMPTS:
                    await asyncio.sleep(SOFT_RETRY_DELAY_SECONDS * attempt)
                    continue
                raise OpenAIServiceError(f"Upstream error {error_code}: {error_message}")

            choices = getattr(response, "choices", None)
            if not choices:
                logger.error(f"Failed to get a proper response from the model. Raw response: {response}")
                self._dump_bad_request(kwargs, client)
                return "Failed to get a response from the model.", 0.0

            response_text = choices[0].message.content
            if not response_text:
                logger.error(f"Empty response text (attempt {attempt}). Raw response object: {response}")
                if attempt < SOFT_RETRY_ATTEMPTS:
                    await asyncio.sleep(SOFT_RETRY_DELAY_SECONDS * attempt)
                    continue
                self._dump_bad_request(kwargs, client)
                raise OpenAIServiceError("The model API returned an empty response.")

            snippet = response_text.strip().replace("\n", " ")
            logger.info(f"[{model}] response snippet: {snippet[:150]}{'...' if len(snippet) > 150 else ''}")
            cleaned = clean_openai_response(
                response_text, bot_id=resolve_bot_id(bot_id, state_service, self._bot_id_loader))
            return cleaned, _response_cost(response)

        raise OpenAIServiceError("Failed to get chat completion after all retry attempts")


def _prompt_snippet(api_input: List[Dict[str, Any]]) -> str:
    if not api_input:
        return "(empty prompt)"
    content = api_input[-1].get("content", "")
    if isinstance(content, list):
        content = next((p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"), "")
    text = str(content).replace("\n", " ")
    return text[:150] + ("..." if len(text) > 150 else "")


def _response_cost(response: Any) -> float:
    try:
        model_extra = getattr(getattr(response, "usage", None), "model_extra", None)
        if isinstance(model_extra, dict):
            cost_details = model_extra.get("cost_details", {})
            if isinstance(cost_details, dict):
                return cost_details.get("upstream_inference_cost", 0.0) or 0.0
    except Exception as e:
        logger.warning(f"Failed to parse cost: {e}")
    return 0.0
