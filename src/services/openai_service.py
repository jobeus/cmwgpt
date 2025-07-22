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

from src.config import OPENAI_API_KEY, IS_TESTING, USER_CONTEXT_URL, VECTOR_STORE_ID
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

    async def _fetch_youtube_transcript(self, video_url: str) -> str:
        """
        Fetch the English transcript (auto-generated or manually provided) for a YouTube video,
        using yt-dlp in the background. Returns the raw VTT content as a string, or an error message.

        Args:
            video_url: URL of the YouTube video.

        Returns:
            The transcript (.vtt) as a string, or an error description if something goes wrong.
        """
        # Ensure yt-dlp is available on PATH
        cmd = ["yt-dlp", "--version"]
        try:
            proc_check = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc_check.wait()
            if proc_check.returncode != 0:
                return "yt-dlp is not installed or not found on PATH."
        except OSError as e:
            logger.error(f"Error checking yt-dlp availability: {e}")
            return f"Failed to run yt-dlp: {e}"

        # Create a temporary directory to hold the subtitle file
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                # Build the yt-dlp command to download only the English VTT subtitles
                # --skip-download : do not download the video itself
                # --write-auto-sub: grab the auto-generated subtitles if no manual ones exist
                # --sub-lang en    : request English subtitles
                # --sub-format vtt : force output format to WebVTT
                # -o {tmpdir}/transcript.%(ext)s : write to a predictable filename with .vtt extension
                yt_cmd = [
                    "yt-dlp",
                    "--skip-download",
                    "--write-sub",
                    "--write-auto-sub",
                    "--sub-lang",
                    "en",
                    "--sub-format",
                    "vtt",
                    "-o",
                    os.path.join(tmpdir, "transcript.%(ext)s"),
                    video_url,
                ]

                logger.debug(f"Running yt-dlp to fetch subtitles: {' '.join(yt_cmd)}")
                proc = await asyncio.create_subprocess_exec(
                    *yt_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                try:
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(), timeout=60.0
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    return "Timed out while attempting to download subtitles."

                if proc.returncode != 0:
                    err_output = stderr.decode(errors="ignore").strip()
                    logger.warning(
                        f"yt-dlp exited with code {proc.returncode}: {err_output}"
                    )
                    return f"yt-dlp failed to fetch subtitles: {err_output}"

                # Locate the downloaded .vtt file in the temporary directory
                vtt_files = glob.glob(os.path.join(tmpdir, "*.vtt"))
                if not vtt_files:
                    return "No English subtitles found for this video."
                vtt_path = vtt_files[0]

                # Read and return its contents
                with open(vtt_path, "r", encoding="utf-8") as f:
                    transcript_text = f.read()

                logger.debug(f"Fetched transcript ({len(transcript_text)} characters)")
                return transcript_text

        except Exception as e:
            logger.error(f"Error while fetching YouTube transcript: {e}")
            return f"Failed to fetch transcript: {e}"

    async def get_chat_completion(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        system_prompt: str = None,
        channel_id: int = None,
        state_service: Any = None,
    ) -> str:
        """
        Gets a chat completion with function calling support using the new Responses API.
        """
        client = self.get_client()

        # Prepare input for the new responses API
        api_input = messages.copy()

        # Remove any existing system messages from the conversation
        api_input = [msg for msg in api_input if msg.get("role") != "system"]

        # Parse JSON content if needed (for complex payloads like attachments)
        for msg in api_input:
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip().startswith(("[", "{")):
                try:
                    # Try to parse as JSON - if successful, use parsed content
                    parsed_content = json.loads(content)
                    msg["content"] = parsed_content
                except (json.JSONDecodeError, ValueError):
                    # If parsing fails, keep original content
                    pass

        # Convert messages to the new input format and add system prompt as instructions
        instructions = system_prompt if system_prompt else None

        # Define tools for the new responses API
        tools = [
            {
                "type": "function",
                "strict": True,
                "name": "get_youtube_transcript",
                "description": "Fetch the transcript of a YouTube video from its URL. Returns the transcript as a string.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The URL of the YouTube video to transcribe",
                        }
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
            }
        ]

        if USER_CONTEXT_URL:
            tools.append(
                {
                    "type": "function",
                    "strict": True,
                    "additionalProperties": False,
                    "name": "get_user_context",
                    "description": "Fetch historical IRC quotes and context about the user for personalized responses",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                }
            )

        max_retries = 3
        base_delay = 1.0

        for attempt in range(max_retries):
            try:
                logger.debug(
                    f"""Attempting response creation with tools for model {model} (attempt {
                        attempt + 1}/{max_retries})"""
                )

                # Use new OpenAI responses API with tool calling
                response = await client.responses.create(
                    model=model,
                    input=api_input,
                    instructions=instructions,
                    tools=tools,
                    tool_choice="auto",
                )

                response_output = response.output[0]
                if response_output.type == "function_call":
                    tool_call = response_output
                else:
                    tool_call = None

                if tool_call:
                    # Process the first tool call (assuming one at a time for now)
                    function_name = (
                        tool_call.name if hasattr(tool_call, "name") else None
                    )

                    logger.info(f"OpenAI requested tool call: {function_name}")

                    if function_name == "get_user_context":
                        # Fetch user context
                        context_data = await self._fetch_user_context()

                        # Create a new input with the tool result
                        tool_result_input = api_input.copy()
                        tool_result = {
                            "type": "function_call_output",
                            "call_id": tool_call.id,
                            "output": context_data,
                        }

                        state_service.add_message_to_conversation(channel_id, tool_call)
                        tool_result_input.append(tool_result)
                        state_service.add_message_to_conversation(
                            channel_id, tool_result
                        )

                        # Make another request with the tool result
                        logger.debug("Making follow-up request with tool result")
                        follow_up_response = await client.responses.create(
                            model=model,
                            input=tool_result_input,
                            instructions=instructions,
                            tools=tools,
                            tool_choice="auto",
                        )

                        # Extract the final response
                        final_output = follow_up_response.output
                        for item in final_output:
                            if hasattr(item, "type") and item.type == "message":
                                for content in item.content:
                                    if (
                                        hasattr(content, "type")
                                        and content.type == "output_text"
                                    ):
                                        logger.debug(
                                            "Response creation with tool calling successful"
                                        )
                                        return clean_openai_response(content.text)
                    elif function_name == "get_youtube_transcript":
                        # Extract URL from tool call arguments
                        function_params = (
                            tool_call.arguments
                            if hasattr(tool_call, "arguments")
                            else "{}"
                        )
                        url = json.loads(function_params).get("url")
                        logger.info(f"Fetching YouTube transcript for {url}")
                        context_data = await self._fetch_youtube_transcript(url)

                        # Create a new input with the tool result
                        tool_result_input = api_input.copy()
                        tool_result = {
                            "type": "function_call_output",
                            "call_id": tool_call.id,
                            "output": context_data,
                        }

                        state_service.add_message_to_conversation(channel_id, tool_call)
                        tool_result_input.append(tool_result)
                        state_service.add_message_to_conversation(
                            channel_id, tool_result
                        )

                        # Make another request with the tool result
                        logger.debug("Making follow-up request with tool result")
                        follow_up_response = await client.responses.create(
                            model=model,
                            input=tool_result_input,
                            instructions=instructions,
                            tools=tools,
                            tool_choice="auto",
                        )

                        # Extract the final response
                        final_output = follow_up_response.output
                        for item in final_output:
                            if hasattr(item, "type") and item.type == "message":
                                for content in item.content:
                                    if (
                                        hasattr(content, "type")
                                        and content.type == "output_text"
                                    ):
                                        logger.debug(
                                            "Response creation with tool calling successful"
                                        )
                                        return clean_openai_response(content.text)
                    else:
                        logger.warning(f"Unknown tool call requested: {function_name}")
                        return f"I tried to call an unknown tool: {function_name}"
                else:
                    # No tool calls, return the response directly
                    logger.debug("Response creation successful (no tool calls)")
                    for item in response.output:
                        if hasattr(item, "type") and item.type == "message":
                            for content in item.content:
                                if (
                                    hasattr(content, "type")
                                    and content.type == "output_text"
                                ):
                                    return clean_openai_response(content.text)

                    # Fallback if we can't find the expected structure
                    return (
                        "I received a response but couldn't extract the text content."
                    )

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
                logger.error(
                    f"Unexpected error during chat completion with functions: {e}"
                )
                raise OpenAIServiceError(f"Unexpected error: {str(e)}") from e

        # This should never be reached, but just in case
        raise OpenAIServiceError(
            "Failed to get chat completion with functions after all retry attempts"
        )

    async def generate_image(
        self, prompt: str, model: str, edit_image: Optional[Attachment] = None
    ) -> bytes:
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
                    result = await client.images.generate(
                        model=model, prompt=prompt, n=1, response_format="b64_json"
                    )
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
                        "Image generation failed, no image data returned."
                    )

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
                    "Authentication failed. Please check API key configuration."
                ) from e

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
