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
from typing import List, Dict, Any, Optional, Tuple, Union

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

from src.config import OPENAI_API_KEY, IS_TESTING, USER_CONTEXT_URL, VECTOR_STORE_ID, PROXY_ADDRESS
from src.utils.message_utils import clean_openai_response

logger = logging.getLogger(__name__)

def clean_subtitle_text(subtitle_text: str) -> str:
    # Remove SRT/VTT timestamps (e.g., "00:00:10,500 --> 00:00:12,000")
    text = re.sub(
        r"\d{2}:\d{2}:\d{2}[.,]\d{3} --> \d{2}:\d{2}:\d{2}[.,]\d{3}",
        "",
        subtitle_text
    )

    # Remove alignment/position metadata (e.g., "align:start position:0%")
    text = re.sub(r"align:start position:\d+%.*", "", text)

    # Remove ASS dialogue timing (e.g., "Dialogue: 0,0:00:01.00,0:00:02.00,Default,...")
    text = re.sub(
        r"^Dialogue:.*?,\d+:\d{2}:\d{2}\.\d+,\d+:\d{2}:\d{2}\.\d+.*?,",
        "",
        text,
        flags=re.MULTILINE
    )

    # Remove numeric line numbers in SRT
    text = re.sub(r"^\d+\s*$", "", text, flags=re.MULTILINE)

    # Remove ASS/SSA headers or other bracketed info
    text = re.sub(r"^\[.*?\].*$", "", text, flags=re.MULTILINE)

    # Remove leftover style tags (e.g., {italic}, <i>)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\{[^}]+\}", "", text)

    # Split lines, strip whitespace, remove empty lines
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # Remove consecutive duplicate lines
    cleaned_lines = []
    previous_line = None
    for line in lines:
        if line != previous_line:
            cleaned_lines.append(line)
        previous_line = line

    return "\n".join(cleaned_lines)

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
                # --proxy          : use proxy if PROXY_ADDRESS is configured
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
                ]

                # Add proxy if configured
                if PROXY_ADDRESS:
                    yt_cmd.extend(["--proxy", f"http://{PROXY_ADDRESS}"])
                    logger.debug(f"Using proxy for yt-dlp: {PROXY_ADDRESS}")

                yt_cmd.append(video_url)

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
                cleaned_subtitle_text = clean_subtitle_text(transcript_text)
                return cleaned_subtitle_text

        except Exception as e:
            logger.error(f"Error while fetching YouTube transcript: {e}")
            return f"Failed to fetch transcript: {e}"

    def _extract_response_text_and_store_id(self, response, channel_id: int, state_service: Any) -> Optional[str]:
        """
        Extract response text and store response ID in one operation.

        Args:
            response: OpenAI response object
            channel_id: Discord channel ID
            state_service: State service instance

        Returns:
            Extracted response text or None if not found
        """
        # Store response ID first
        if response and hasattr(response, 'id') and channel_id and state_service:
            try:
                state_service.set_response_id(channel_id, response.id)
                logger.debug(f"Stored response ID {response.id} for channel {channel_id}")
            except Exception as e:
                logger.warning(f"Failed to store response ID: {e}")

        # Extract response text
        if not response or not hasattr(response, 'output'):
            return None

        for item in response.output:
            if hasattr(item, "type") and item.type == "message":
                for content in item.content:
                    if hasattr(content, "type") and content.type == "output_text":
                        return clean_openai_response(content.text)

        return None
    
    async def _handle_image_generation_output(self, response_output) -> tuple[Optional[str], List[discord.File]]:
        """
        Handle image generation output from OpenAI response.

        Args:
            response_output: Image generation response output object

        Returns:
            Tuple of (description text, list of Discord File objects)
        """
        try:
            files_to_upload = []

            image_data = base64.b64decode(response_output.result)

            if image_data:
                # Create Discord file
                filename = f"generated_image.png"
                discord_file = discord.File(io.BytesIO(image_data), filename=filename)
                files_to_upload.append(discord_file)
                logger.info(f"Prepared generated image for upload: {filename}")
            else:
                logger.warning(f"No image data found for generated image")

            # Create description text
            image_count = len(files_to_upload)
            if image_count > 0:
                description = f""
            else:
                description = "🎨 error w/ image generation: no images were generated."

            return description, files_to_upload

        except Exception as e:
            logger.error(f"Error processing image generation output: {e}")
            return "🎨 **Image Generation:** Error processing generated images.", []

    def _prepare_api_params(self, model: str, api_input: List[Dict[str, Any]], instructions: str,
                           tools: List[Dict[str, Any]],
                           previous_response_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Prepare API parameters for OpenAI responses.create call.

        Args:
            model: Model name
            api_input: Input messages
            instructions: System instructions
            tools: Available tools
            previous_response_id: Previous response ID for continuity

        Returns:
            Dictionary of API parameters
        """
        params = {
            "model": model,
            "input": api_input,
            "instructions": instructions,
            "tools": tools,
            "tool_choice": "auto",
        }

        if previous_response_id:
            params["previous_response_id"] = previous_response_id

        return params

    async def _handle_openai_response_with_continuity(self, client, model: str, api_input: List[Dict[str, Any]],
                                                     instructions: str, tools: List[Dict[str, Any]],
                                                     channel_id: int, state_service: Any) -> str:
        """
        Handle OpenAI API response with conversation continuity in one comprehensive operation.

        Args:
            client: OpenAI client
            model: Model name
            api_input: Input messages
            instructions: System instructions
            tools: Available tools
            channel_id: Discord channel ID
            state_service: State service instance

        Returns:
            Response text
        """
        # Get previous response ID for continuity
        previous_response_id = None
        if channel_id and state_service:
            previous_response_id = state_service.get_response_id(channel_id)
            if previous_response_id:
                logger.debug(f"Using previous response ID for continuity: {previous_response_id}")

        # Make initial API call
        api_params = self._prepare_api_params(model, api_input, instructions, tools, previous_response_id)
        logger.debug(f"_handle_openai_response_with_continuity API call with params: \n{json.dumps(api_params, indent=2)}\n\n")
        response = await client.responses.create(**api_params)

        # Process all response outputs and collect results
        response_parts = []
        files_to_upload = []

        for response_output in response.output:
            if response_output.type == "function_call":
                # Handle traditional function calling
                return await self._handle_tool_call(client, response_output, model, api_input, instructions,
                                                    tools, response.output, previous_response_id, channel_id, state_service)
            elif response_output.type == "image_generation_call":
                # Handle image generation results
                image_text, image_files = await self._handle_image_generation_output(response_output)
                if image_text is not None:  # More explicit check - empty string is still valid
                    response_parts.append(image_text)
                if image_files:
                    files_to_upload.extend(image_files)
            elif response_output.type == "message":
                # Handle regular text message
                for content in response_output.content:
                    if hasattr(content, "type") and content.type == "output_text":
                        response_parts.append(clean_openai_response(content.text))

        # Store response ID
        if response and hasattr(response, 'id') and channel_id and state_service:
            try:
                state_service.set_response_id(channel_id, response.id)
                logger.debug(f"Stored response ID {response.id} for channel {channel_id}")
            except Exception as e:
                logger.warning(f"Failed to store response ID: {e}")

        # Combine all response parts
        if response_parts:
            final_text = "\n\n".join(response_parts)
        elif files_to_upload:
            # If we have files but no text, provide a minimal message
            # This prevents Discord's "Cannot send an empty message" error
            final_text = "Here are the generated images:"
        else:
            final_text = "I received a response but couldn't extract the text content."

        # Return both text and files for upload
        if files_to_upload:
            return {"text": final_text, "files": files_to_upload}
        else:
            return final_text

    async def _handle_tool_call(self, client, tool_call, model: str, api_input: List[Dict[str, Any]],
                               instructions: str, tools: List[Dict[str, Any]], response_output: List[Dict[str, Any]],
                               previous_response_id: Optional[str], channel_id: int, state_service: Any) -> str:
        """
        Handle tool call execution and follow-up response.

        Args:
            client: OpenAI client
            tool_call: Tool call object
            model: Model name
            api_input: Original input messages
            instructions: System instructions
            tools: Available tools
            response_output: Full response output (including reasoning)
            previous_response_id: Previous response ID
            channel_id: Discord channel ID
            state_service: State service instance

        Returns:
            Response text
        """
        function_name = tool_call.name if hasattr(tool_call, "name") else None
        logger.info(f"OpenAI requested tool call: {function_name}")

        if function_name == "get_user_context":
            context_data = await self._fetch_user_context()
        elif function_name == "get_youtube_transcript":
            function_params = tool_call.arguments if hasattr(tool_call, "arguments") else "{}"
            url = json.loads(function_params).get("url")
            logger.info(f"Fetching YouTube transcript for {url}")
            context_data = await self._fetch_youtube_transcript(url)
        else:
            logger.warning(f"Unknown tool call requested: {function_name}")
            return f"I tried to call an unknown tool: {function_name}"

        # Create tool result input
        tool_result_input = api_input.copy()
        tool_result = {
            "type": "function_call_output",
            "call_id": tool_call.call_id,
            "output": context_data,
        }

        for response in response_output:
            if response.type == "reasoning":
                tool_result_input.append(response.model_dump(exclude={"status"}))
            else:
                tool_result_input.append(response.model_dump())
        tool_result_input.append(tool_result)
        if state_service and channel_id:
            state_service.add_message_to_conversation(channel_id, tool_result)

        # Make follow-up request
        logger.debug("Making follow-up request with tool result")
        follow_up_params = self._prepare_api_params(model, tool_result_input, instructions, tools, previous_response_id)
        logger.debug(f"_handle_tool_call API call with params: \n{json.dumps(follow_up_params, indent=2)}\n\n")
        follow_up_response = await client.responses.create(**follow_up_params)

        # Extract response and store ID
        logger.debug("Response creation with tool calling successful")
        response_text = self._extract_response_text_and_store_id(follow_up_response, channel_id, state_service)
        return response_text or "I received a response but couldn't extract the text content."

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
                "type": "image_generation",
                "moderation": "low",
                "quality": "medium",
                "size": "auto",
                "background": "auto",
            },
            {"type": "web_search_preview"},
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
            },
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

        if VECTOR_STORE_ID:
            tools.append(
                {
                    "type": "file_search",
                    "vector_store_ids": [VECTOR_STORE_ID],
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

                # Handle OpenAI response with conversation continuity
                return await self._handle_openai_response_with_continuity(
                    client, model, api_input, instructions, tools, channel_id, state_service
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
