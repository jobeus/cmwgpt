"""
OpenAI Service - Handles all OpenAI API interactions
"""

import asyncio
import tempfile
import base64
import json
import logging
import httpx
import glob
import os
import re
import io
from typing import List, Dict, Any, Optional

from openai import (
    AsyncOpenAI,
    APIError,
    RateLimitError,
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
)
from discord import Attachment
import discord

from src.config import OPENROUTER_API_KEY, IS_TESTING
from src.utils.message_utils import clean_openai_response

logger = logging.getLogger(__name__)


class OpenAIServiceError(Exception):
    """Custom exception for OpenAI service errors."""
    pass


class OpenAIService:
    """Service for handling OpenAI API interactions."""

    def __init__(self):
        self._client: Optional[AsyncOpenAI] = None

    def set_client(self, client) -> None:
        """Set a custom client (useful for testing)."""
        self._client = client

    def get_client(self) -> AsyncOpenAI:
        """Get OpenAI client with lazy initialization."""
        if self._client is None:
            if IS_TESTING:
                # In testing environment, create a mock-friendly client
                # Don't try to create a real client in testing
                class MockClient:
                    def __getattr__(self, name):  # noqa: ARG002
                        return lambda *args, **kwargs: None  # noqa: ARG005

                self._client = MockClient()
            else:
                self._client = AsyncOpenAI(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=OPENROUTER_API_KEY,
                    default_headers={
                        "HTTP-Referer": "https://github.com/jobeus/cmwgpt",
                        "X-Title": "CMWGPT Discord Bot"
                    }
                )
        return self._client

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
    ) -> str:
        """
        Gets a chat completion using the standard Chat Completions API.
        """
        client = self.get_client()

        # Prepare input for the chat completions API
        api_input = []
        if system_prompt:
            api_input.append({"role": "system", "content": system_prompt})

        # Remove any existing system messages from the conversation if duplicate
        for msg in messages:
            if msg.get("role") != "system":
                # Parse JSON content if needed (for complex payloads like attachments)
                content = msg.get("content", "")
                parsed_content = content
                if isinstance(
                        content, str) and content.strip().startswith(
                        ("[", "{")):
                    try:
                        # Try to parse as JSON - if successful, use parsed content
                        parsed_content = json.loads(content)
                    except (json.JSONDecodeError, ValueError):
                        # If parsing fails, keep original content
                        pass
                
                api_input.append({
                    "role": msg.get("role"),
                    "content": parsed_content
                })

        max_retries = 3
        base_delay = 1.0

        for attempt in range(max_retries):
            try:
                logger.debug(
                    f"Attempting response creation for model {model} (attempt {attempt + 1}/{max_retries})"
                )

                # actual_model = f"{model}:online" if not model.endswith(":online") else model
                actual_model = model
                
                logger.info(f"OPENROUTER PRE-FLIGHT - model={actual_model}, msg_len={len(api_input)}")
                logger.info(f"API HEADERS USED: {client.default_headers}")
                
                response = await client.chat.completions.create(
                    model=actual_model,
                    messages=api_input,
                    tools=[{
                        "googleSearch": {}
                    }]
                )
                
                logger.info(f"OPENROUTER POST-FLIGHT - got response object! len={len(response.choices)}")
                
                if response and response.choices:
                    response_text = response.choices[0].message.content
                    if not response_text:
                        raise OpenAIServiceError("The model API returned an empty response.")
                    return clean_openai_response(response_text)
                
                return "Failed to get a response from the model."

            except RateLimitError as e:
                logger.warning(f"Rate limit hit on attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)  # Exponential backoff
                    logger.info(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                    continue
                logger.error("Max retries exceeded for rate limit")
                raise OpenAIServiceError(
                    f"Rate limit exceeded after {max_retries} attempts. Please try again later."
                ) from e

            except AuthenticationError as e:
                logger.error(f"Authentication error: {e}")
                raise OpenAIServiceError(
                    "Authentication failed. Please check API key configuration."
                ) from e

            except APIConnectionError as e:
                logger.warning(
                    f"Connection error on attempt {attempt + 1}: {e}"
                )
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.info(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                    continue
                logger.error("Max retries exceeded for connection error")
                raise OpenAIServiceError(
                    f"Connection failed after {max_retries} attempts. Please try again later."
                ) from e

            except BadRequestError as e:
                logger.error(f"Bad request error: {e}")
                raise OpenAIServiceError(
                    f"Invalid request: {e.message if hasattr(e, 'message') else str(e)}"
                ) from e

            except APIError as e:
                logger.error(f"OpenAI API error on attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.info(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                    continue
                logger.error("Max retries exceeded for API error")
                raise OpenAIServiceError(
                    f"OpenAI API error after {max_retries} attempts: {str(e)}"
                ) from e

            except OpenAIServiceError:
                # Re-raise our own exceptions without modification
                raise

            except (httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
                logger.error(
                    f"Unexpected error during chat completion: {e}")
                raise OpenAIServiceError(f"Unexpected error: {str(e)}") from e

        # This should never be reached, but just in case
        raise OpenAIServiceError(
            "Failed to get chat completion after all retry attempts"
        )


# Global service instance
openai_service = OpenAIService()
