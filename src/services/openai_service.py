"""
OpenAI Service - Handles all OpenAI API interactions
"""

import asyncio
import base64
import logging
from typing import List, Dict, Any, Optional

from openai import AsyncOpenAI, APIError, RateLimitError, APIConnectionError, AuthenticationError, BadRequestError
from discord import Attachment

from src.config import OPENAI_API_KEY, IS_TESTING


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
                self._client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        return self._client

    async def get_chat_completion(
            self, model: str, messages: List[Dict[str, Any]]) -> str:
        """
        Gets a chat completion from the OpenAI API.

        Args:
            model: The model to use for completion
            messages: List of message dictionaries for the conversation

        Returns:
            The completion text from OpenAI

        Raises:
            OpenAIServiceError: If OpenAI API call fails
        """
        client = self.get_client()

        max_retries = 3
        base_delay = 1.0

        for attempt in range(max_retries):
            try:
                logger.debug(
                    f"Attempting chat completion with model {model} (attempt {attempt + 1}/{max_retries})"
                )
                response = await client.responses.create(model=model, input=messages)
                logger.debug("Chat completion successful")
                return response.output_text

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
                    "Authentication failed. Please check API key configuration.") from e

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

            except Exception as e:
                logger.error(f"Unexpected error during chat completion: {e}")
                raise OpenAIServiceError(f"Unexpected error: {str(e)}") from e

        # This should never be reached, but just in case
        raise OpenAIServiceError(
            "Failed to get chat completion after all retry attempts")

    async def generate_image(
            self,
            prompt: str,
            model: str,
            edit_image: Optional[Attachment] = None) -> bytes:
        """
        Generates an image using the OpenAI API.

        Args:
            prompt: The text prompt for image generation
            model: The model to use for generation
            edit_image: Optional image to edit instead of generating new

        Returns:
            The raw image bytes

        Raises:
            OpenAIServiceError: If image generation fails
        """
        client = self.get_client()

        max_retries = 3
        base_delay = 1.0

        for attempt in range(max_retries):
            try:
                logger.debug(
                    f"Attempting image generation with model {model} (attempt {attempt + 1}/{max_retries})"
                )
                b64_json_data = None
                result = None

                if model == "dall-e-2" or model == "dall-e-3":
                    result = await client.images.generate(model=model, prompt=prompt, n=1, response_format="b64_json")
                else:  # assume gpt-image-1 or similar custom model
                    if edit_image:
                        file_obj = edit_image.to_file()
                        result = await client.images.edit(
                            model=model,
                            image=[file_obj],
                            # image expects a list of file-like objects
                            prompt=prompt,
                        )
                    else:
                        result = await client.images.generate(
                            model=model,
                            prompt=prompt,
                            n=1,
                            moderation="low",
                        )

                if result and result.data and len(result.data) > 0:
                    b64_json_data = result.data[0].b64_json

                if not b64_json_data:
                    raise OpenAIServiceError(
                        "Image generation failed, no image data returned.")

                img_bytes = base64.b64decode(b64_json_data)
                logger.debug("Image generation successful")
                return img_bytes

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
                    "Authentication failed. Please check API key configuration.") from e

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
                # BadRequestError is often due to content policy violations,
                # don't retry
                raise OpenAIServiceError(
                    f"Request rejected: {e.message if hasattr(e, 'message') else str(e)}"
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

            except Exception as e:
                logger.error(f"Unexpected error during image generation: {e}")
                raise OpenAIServiceError(f"Unexpected error: {str(e)}") from e

        # This should never be reached, but just in case
        raise OpenAIServiceError(
            "Failed to generate image after all retry attempts")


# Global service instance
openai_service = OpenAIService()
