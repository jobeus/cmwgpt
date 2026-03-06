"""
OpenAI Service - Handles all OpenAI API interactions
"""

import asyncio
import json
import logging
import httpx
from typing import List, Dict, Any, Optional

from openai import (
    AsyncOpenAI,
    APIError,
    RateLimitError,
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
)


from src.config import OPENROUTER_API_KEY, IS_TESTING
from src.utils.message_utils import clean_openai_response
from src.db.logger import log_api_request

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

    def _dump_bad_request(self, kwargs_dict: Dict[str, Any], client: AsyncOpenAI) -> None:
        """Dumps the request payload to /tmp/bad_request.json and a curl command to /tmp/bad_request.sh"""
        import os
        import shlex
        try:
            with open('/tmp/bad_request.json', 'w') as f:
                json.dump(kwargs_dict, f, indent=2)
                
            curl_cmd = f"curl https://openrouter.ai/api/v1/chat/completions \\\n"
            
            # Extract headers
            headers_dict = dict(client.default_headers) if hasattr(client, 'default_headers') else {}
            # Ensure Auth is present
            if hasattr(client, 'api_key') and client.api_key:
                headers_dict['Authorization'] = f"Bearer {client.api_key}"
            elif OPENROUTER_API_KEY:
                headers_dict['Authorization'] = f"Bearer {OPENROUTER_API_KEY}"
                
            for k, v in headers_dict.items():
                if 'Omit' in str(type(v)):
                    continue
                header_str = f"{k}: {v}"
                curl_cmd += f"  -H {shlex.quote(header_str)} \\\n"
                
            curl_cmd += f"  -d @/tmp/bad_request.json\n"
            
            with open('/tmp/bad_request.sh', 'w') as f:
                f.write("#!/bin/bash\n\n" + curl_cmd)
                
            os.chmod('/tmp/bad_request.sh', 0o755)
            logger.info("Saved bad request dump to /tmp/bad_request.json and /tmp/bad_request.sh")
        except Exception as e:
            logger.error(f"Failed to dump bad request: {e}")

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
    ) -> str:
        """
        Gets a chat completion using the standard Chat Completions API.
        """
        client = self.get_client()

        # Prepare input for the chat completions API
        api_input = []
        if system_prompt:
            api_input.append({"role": "system", "content": system_prompt})

        # Remove any existing system messages from the conversation if
        # duplicate
        for msg in messages:
            if msg.get("role") != "system":
                # Parse JSON content if needed (for complex payloads like
                # attachments)
                content = msg.get("content", "")
                parsed_content = content
                if isinstance(
                        content, str) and content.strip().startswith(
                        ("[", "{")):
                    try:
                        # Try to parse as JSON - if successful, use parsed
                        # content
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
                    f"Attempting response creation for model {model} (attempt {
                        attempt + 1}/{max_retries})")

                # actual_model = f"{model}:online" if not model.endswith(":online") else model
                actual_model = model

                # Generate a snippet of the latest message
                last_msg_snippet = "(empty prompt)"
                if api_input:
                    last_msg_ptr = api_input[-1].get('content', '')
                    if isinstance(last_msg_ptr, list):
                        for part in last_msg_ptr:
                            if part.get('type') == 'text':
                                last_msg_snippet = part.get('text', '').replace("\n", " ")
                                break
                    elif isinstance(last_msg_ptr, str):
                         last_msg_snippet = last_msg_ptr.replace("\n", " ")
                
                snippet_trunc = last_msg_snippet[:150] + ("..." if len(last_msg_snippet) > 150 else "")
                logger.info(f"[{actual_model}] Prompt Snippet: {snippet_trunc}")
                logger.info(f"api headers: {client.default_headers}")

                kwargs = {
                    "model": actual_model,
                    "messages": api_input,
                    "extra_body": {
                        "provider": {
                            "sort": "price"
                        }
                    }
                }

                if actual_model == "anthropic/claude-haiku-4.5":
                    kwargs["extra_body"]["plugins"] = [{
                        "id": "web",
                        "engine": "native"
                    }]

                response = await client.chat.completions.create(**kwargs)

                if hasattr(response, 'error') and response.error is not None:
                    error_data = response.error
                    if isinstance(error_data, dict):
                        error_code = error_data.get('code')
                        error_message = error_data.get('message', 'Unknown Error')
                        
                        logger.error(f"Captured Soft-Error Payload in HTTP 200: {error_code} - {error_message}")
                        logger.error(f"Full soft-error data: {json.dumps(error_data, indent=2, default=str)}")
                        logger.error(f"Full raw response object: {response}")
                        self._dump_bad_request(kwargs, client)
                        
                        if error_code in [429, 500, 502, 503, 504]:
                           # Force this up to the retry loop catcher
                           raise APIError(message=f"Upstream provider error: {error_code}", request=None, body=error_data)
                           
                        raise OpenAIServiceError(f"Non-retryable soft-error {error_code}: {error_message}")

                # Safely inspect choices to avoid "object of type NoneType has no len()"
                num_choices = len(response.choices) if getattr(response, 'choices', None) else 0
                logger.info(
                    f"[{actual_model}] got response object! number of choices={num_choices}")

                if getattr(response, 'choices', None):
                    response_text = response.choices[0].message.content
                    
                    # Print response snippet
                    text_snippet = response_text.strip().replace("\n", " ")[:150] if response_text else "(empty)"
                    logger.info(f"[{actual_model}] response snippet: {text_snippet}{'...' if len(response_text or '') > 150 else ''}")
                    
                    if not response_text:
                        logger.error(f"⚠️ OpenAI response text was empty! Raw response object: {response}")
                        raise ValueError("The model API returned an empty response.") # Changed from OpenAIServiceError to allow caught retry

                    
                    # Attempt to get the bot_id if not explicitly provided
                    effective_bot_id = bot_id
                    if not effective_bot_id:
                        try:
                            if state_service and hasattr(state_service, 'bot_id'):
                                effective_bot_id = state_service.bot_id
                            else:
                                from src.services.message_service import message_service
                                if message_service and hasattr(message_service, 'bot') and message_service.bot and hasattr(message_service.bot, 'user'):
                                    effective_bot_id = message_service.bot.user.id
                        except Exception as e:
                            logger.warning(f"Failed to get bot_id for cleaning: {e}")
                    
                    cleaned_text = clean_openai_response(response_text, bot_id=effective_bot_id)
                    
                    try:
                        cost = 0.0
                        if hasattr(response, 'usage') and response.usage:
                            model_extra = getattr(response.usage, 'model_extra', None)
                            if isinstance(model_extra, dict):
                                cost_details = model_extra.get('cost_details', {})
                                if isinstance(cost_details, dict):
                                    cost = cost_details.get('upstream_inference_cost', 0.0)
                        if cost > 0:
                            cleaned_text = f"[${cost:.3f}] {cleaned_text}"
                    except Exception as e:
                        logger.warning(f"Failed to parse cost: {e}")
                        
                    # Extract the actual wire-level request from the httpx response
                    http_resp = getattr(response, 'http_response', None)
                    if http_resp and hasattr(http_resp, 'request'):
                        actual_req = http_resp.request
                        req_headers = dict(actual_req.headers)
                        req_body = actual_req.content.decode('utf-8', errors='replace') if actual_req.content else ""
                        req_url = str(actual_req.url)
                        req_method = actual_req.method
                        resp_status = http_resp.status_code
                        resp_headers = dict(http_resp.headers)
                    else:
                        req_headers = {}
                        req_body = kwargs
                        req_url = "https://openrouter.ai/api/v1/chat/completions"
                        req_method = "POST"
                        resp_status = 200
                        resp_headers = {}

                    await log_api_request(
                        service_name=f"openai/{actual_model}",
                        method=req_method,
                        endpoint_url=req_url,
                        request_headers=req_headers,
                        request_body=req_body,
                        response_status=resp_status,
                        response_headers=resp_headers,
                        response_body=response.model_dump() if hasattr(response, 'model_dump') else {},
                        cost=cost,
                        discord_user_id=discord_user_id,
                        discord_channel_id=channel_id
                    )
                        
                    return cleaned_text
                
                logger.error(f"❌ Failed to get a proper response from the model. Raw response: {response}")
                self._dump_bad_request(kwargs, client)
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
                self._dump_bad_request(kwargs, client)
                raise OpenAIServiceError(
                    f"Invalid request: {
                        e.message if hasattr(
                            e, 'message') else str(e)}") from e

            except APIError as e:
                logger.error(f"OpenAI API error on attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.info(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                    continue
                logger.error("Max retries exceeded for API error")
                self._dump_bad_request(kwargs, client)
                raise OpenAIServiceError(
                    f"OpenAI API error after {max_retries} attempts: {str(e)}"
                ) from e

            except OpenAIServiceError:
                # Re-raise our own exceptions without modification
                raise

            except (httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
                logger.error(f"Parsing/HTTP error on attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.info(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                    continue
                self._dump_bad_request(kwargs, client)
                raise OpenAIServiceError(f"Unexpected error: {str(e)}") from e

        # This should never be reached, but just in case
        raise OpenAIServiceError(
            "Failed to get chat completion after all retry attempts"
        )


# Global service instance
openai_service = OpenAIService()
