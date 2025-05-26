"""
OpenAI Service - Handles all OpenAI API interactions
"""

import asyncio
import base64
import json
import logging
import httpx
from typing import List, Dict, Any, Optional

from openai import AsyncOpenAI, APIError, RateLimitError, APIConnectionError, AuthenticationError, BadRequestError
from discord import Attachment

from src.config import OPENAI_API_KEY, IS_TESTING, USER_CONTEXT_URL
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
                self._client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        return self._client

    async def _fetch_user_context(self) -> str:
        """
        Fetch user context from the configured URL.

        Returns:
            User context data as string, or error message if fetch fails
        """
        if not USER_CONTEXT_URL:
            return "User context URL not configured."

        try:
            logger.debug(f"Fetching user context from: {USER_CONTEXT_URL}")
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(USER_CONTEXT_URL)
                response.raise_for_status()
                context_data = response.text
                logger.debug(
                    f"""Successfully fetched user context ({
                    len(context_data)} characters)"""
                )
                return context_data
        except httpx.TimeoutException:
            logger.warning("Timeout while fetching user context")
            return "User context fetch timed out."
        except httpx.HTTPStatusError as e:
            logger.warning(
                f"""HTTP error while fetching user context: {
                e.response.status_code}"""
            )
            return f"""User context fetch failed with HTTP {
                e.response.status_code}."""
        except (httpx.ConnectError, httpx.ReadError, OSError) as e:
            logger.error(f"Error fetching user context: {e}")
            return f"User context fetch failed: {str(e)}"

    async def get_chat_completion(self, model: str, messages: List[Dict[str, Any]], system_prompt: str = None) -> str:
        """
        Gets a chat completion from the OpenAI API with function calling support.

        Args:
            model: The model to use for completion
            messages: List of message dictionaries for the conversation (without system prompt)
            system_prompt: Optional system prompt to prepend to messages

        Returns:
            The completion text from OpenAI

        Raises:
            OpenAIServiceError: If OpenAI API call fails
        """
        # Check if user context URL is configured to enable function calling
        if USER_CONTEXT_URL:
            return await self._get_chat_completion_with_functions(model, messages, system_prompt)
        else:
            return await self._get_chat_completion_legacy(model, messages, system_prompt)

    async def _get_chat_completion_with_functions(
        self, model: str, messages: List[Dict[str, Any]], system_prompt: str = None
    ) -> str:
        """
        Gets a chat completion with function calling support.
        """
        client = self.get_client()

        # Prepare messages with system prompt if provided
        api_messages = messages.copy()

        # Remove any existing system messages from the conversation
        api_messages = [msg for msg in api_messages if msg.get("role") != "system"]

        # Parse JSON content if needed (for complex payloads like attachments)
        for msg in api_messages:
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip().startswith(("[", "{")):
                try:
                    # Try to parse as JSON - if successful, use parsed content
                    parsed_content = json.loads(content)
                    msg["content"] = parsed_content
                except (json.JSONDecodeError, ValueError):
                    # If parsing fails, keep original content
                    pass

        # Add system prompt at the beginning if provided
        if system_prompt:
            api_messages.insert(0, {"role": "system", "content": system_prompt})

        # Define the get_user_context function
        functions = [
            {
                "name": "get_user_context",
                "description": "Fetch historical IRC quotes and context about the user for personalized responses",
                "parameters": {"type": "object", "properties": {}, "required": []},
            }
        ]

        max_retries = 3
        base_delay = 1.0

        for attempt in range(max_retries):
            try:
                logger.debug(
                    f"""Attempting chat completion with functions for model {model} (attempt {
                        attempt + 1}/{max_retries})"""
                )

                # Use standard OpenAI chat completions API with function
                # calling
                response = await client.chat.completions.create(
                    model=model, messages=api_messages, functions=functions, function_call="auto"
                )

                message = response.choices[0].message

                # Check if the model wants to call a function
                if message.function_call:
                    function_name = message.function_call.name
                    logger.info(f"OpenAI requested function call: {function_name}")

                    if function_name == "get_user_context":
                        # Fetch user context
                        context_data = await self._fetch_user_context()

                        # Add the assistant's function call message to
                        # conversation
                        api_messages.append(
                            {
                                "role": "assistant",
                                "content": None,
                                "function_call": {"name": function_name, "arguments": message.function_call.arguments},
                            }
                        )

                        # Add the function response
                        api_messages.append({"role": "function", "name": function_name, "content": context_data})

                        # Make another request with the function result
                        logger.debug("Making follow-up request with function result")
                        follow_up_response = await client.chat.completions.create(
                            model=model, messages=api_messages, functions=functions, function_call="auto"
                        )

                        final_message = follow_up_response.choices[0].message
                        logger.debug("Chat completion with function calling successful")
                        return clean_openai_response(final_message.content)
                    else:
                        logger.warning(f"Unknown function call requested: {function_name}")
                        return f"I tried to call an unknown function: {function_name}"
                else:
                    # No function call, return the response directly
                    logger.debug("Chat completion successful (no function call)")
                    return clean_openai_response(message.content)

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
                raise OpenAIServiceError("Authentication failed. Please check API key configuration.") from e

            except APIConnectionError as e:
                logger.warning(
                    f"""Connection error on attempt {
                    attempt + 1}: {e}"""
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
                    f"""Invalid request: {
                    e.message if hasattr(
                        e, 'message') else str(e)}"""
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
                    f"""OpenAI API error after {max_retries} attempts: {
                        str(e)}"""
                ) from e

            except OpenAIServiceError:
                # Re-raise our own exceptions without modification
                raise

            except (httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
                logger.error(f"Unexpected error during chat completion with functions: {e}")
                raise OpenAIServiceError(f"Unexpected error: {str(e)}") from e

        # This should never be reached, but just in case
        raise OpenAIServiceError("Failed to get chat completion with functions after all retry attempts")

    async def _get_chat_completion_legacy(
        self, model: str, messages: List[Dict[str, Any]], system_prompt: str = None
    ) -> str:
        """
        Gets a chat completion using the legacy API (without function calling).
        """
        client = self.get_client()

        # Prepare messages with system prompt if provided
        api_messages = messages.copy()

        # Remove any existing system messages from the conversation
        api_messages = [msg for msg in api_messages if msg.get("role") != "system"]

        # Parse JSON content if needed (for complex payloads like attachments)
        for msg in api_messages:
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip().startswith(("[", "{")):
                try:
                    # Try to parse as JSON - if successful, use parsed content
                    parsed_content = json.loads(content)
                    msg["content"] = parsed_content
                except (json.JSONDecodeError, ValueError):
                    # If parsing fails, keep original content
                    pass

        # Add system prompt at the beginning if provided
        if system_prompt:
            api_messages.insert(0, {"role": "system", "content": system_prompt})

        max_retries = 3
        base_delay = 1.0

        for attempt in range(max_retries):
            try:
                logger.debug(
                    f"""Attempting legacy chat completion with model {model} (attempt {
                        attempt + 1}/{max_retries})"""
                )
                response = await client.responses.create(
                    model=model,
                    input=api_messages,
                    tools=[{"type": "web_search_preview"}],
                )
                logger.debug("Legacy chat completion successful")
                return clean_openai_response(response.output_text)

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
                raise OpenAIServiceError("Authentication failed. Please check API key configuration.") from e

            except APIConnectionError as e:
                logger.warning(
                    f"""Connection error on attempt {
                    attempt + 1}: {e}"""
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
                    f"""Invalid request: {
                    e.message if hasattr(
                        e, 'message') else str(e)}"""
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
                    f"""OpenAI API error after {max_retries} attempts: {
                        str(e)}"""
                ) from e

            except OpenAIServiceError:
                # Re-raise our own exceptions without modification
                raise

            except (httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
                logger.error(f"Unexpected error during legacy chat completion: {e}")
                raise OpenAIServiceError(f"Unexpected error: {str(e)}") from e

        # This should never be reached, but just in case
        raise OpenAIServiceError("Failed to get legacy chat completion after all retry attempts")

    async def generate_image(self, prompt: str, model: str, edit_image: Optional[Attachment] = None) -> bytes:
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
                    f"""Attempting image generation with model {model} (attempt {
                        attempt + 1}/{max_retries})"""
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
                    raise OpenAIServiceError("Image generation failed, no image data returned.")

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
                raise OpenAIServiceError("Authentication failed. Please check API key configuration.") from e

            except APIConnectionError as e:
                logger.warning(
                    f"""Connection error on attempt {
                    attempt + 1}: {e}"""
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
                    f"""Request rejected: {
                    e.message if hasattr(
                        e, 'message') else str(e)}"""
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
                    f"""OpenAI API error after {max_retries} attempts: {
                        str(e)}"""
                ) from e

            except OpenAIServiceError:
                # Re-raise our own exceptions without modification
                raise

            except (httpx.HTTPError, ValueError, TypeError) as e:
                logger.error(f"Unexpected error during image generation: {e}")
                raise OpenAIServiceError(f"Unexpected error: {str(e)}") from e

        # This should never be reached, but just in case
        raise OpenAIServiceError("Failed to generate image after all retry attempts")


# Global service instance
openai_service = OpenAIService()
