"""
Runpod Service - Handles image generation via Runpod API
"""

import httpx
import logging
from typing import Optional
from src.config import RUNPOD_IO_API_KEY
from src.utils.http_client import create_async_client

logger = logging.getLogger(__name__)


class RunpodServiceError(Exception):
    """Custom exception for Runpod service errors."""
    pass


class RunpodService:
    def __init__(self):
        self.api_key = RUNPOD_IO_API_KEY
        self.models = {
            "z-image": {
                "url": "https://api.runpod.ai/v2/z-image-turbo/runsync",
                "payload": lambda prompt: {
                    "input": {
                        "prompt": prompt,
                        "size": "1280*1280",
                        "strength": 0.8,
                        "seed": -1,
                        "output_format": "png",
                        "enable_safety_checker": False
                    }
                }
            },
            "wan-2.6": {
                "url": "https://api.runpod.ai/v2/wan-2-6-t2i/runsync",
                "payload": lambda prompt: {
                    "input": {
                        "prompt": prompt,
                        "size": "1440*1024",
                        "seed": -1,
                        "enable_safety_checker": False
                    }
                }
            },
            "pruna": {
                "url": "https://api.runpod.ai/v2/p-image-t2i/runsync",
                "payload": lambda prompt: {
                    "input": {
                        "prompt": prompt,
                        "aspect_ratio": "16:9",
                        "disable_safety_checker": True,
                        "seed": 0
                    }
                }
            },
            "seedream": {
                "url": "https://api.runpod.ai/v2/seedream-v4-t2i/runsync",
                "payload": lambda prompt: {
                    "input": {
                        "prompt": prompt,
                        "negative_prompt": "",
                        "size": "2048*2048",
                        "seed": -1,
                        "enable_safety_checker": False
                    }
                }
            },
            "qwen": {
                "url": "https://api.runpod.ai/v2/qwen-image-t2i/runsync",
                "payload": lambda prompt: {
                    "input": {
                        "prompt": prompt,
                        "negative_prompt": "",
                        "size": "1328*1328",
                        "seed": -1,
                        "enable_safety_checker": False
                    }
                }
            },
            "flux": {
                "url": "https://api.runpod.ai/v2/black-forest-labs-flux-1-dev/runsync",
                "payload": lambda prompt: {
                    "input": {
                        "prompt": prompt,
                        "seed": -1,
                        "num_inference_steps": 28,
                        "guidance": 7,
                        "negative_prompt": "",
                        "image_format": "png",
                        "width": 1024,
                        "height": 1024
                    }
                }
            }
        }

        self.edit_models = {
            "seedream": {
                "url": "https://api.runpod.ai/v2/seedream-v4-edit/runsync",
                "payload": lambda prompt, images: {
                    "input": {
                        "prompt": prompt,
                        "images": images,
                        "size": "4096*4096",
                        "enable_safety_checker": False
                    }
                }
            },
            "qwen": {
                "url": "https://api.runpod.ai/v2/qwen-image-edit-2511/runsync",
                "payload": lambda prompt, images: {
                    "input": {
                        "prompt": prompt,
                        "images": images,
                        "size": "1024*1024",
                        "seed": -1,
                        "output_format": "jpeg",
                        "enable_base64_output": False,
                        "enable_sync_mode": False
                    }
                }
            },
            "pruna": {
                "url": "https://api.runpod.ai/v2/p-image-edit/runsync",
                "payload": lambda prompt, images: {
                    "input": {
                        "prompt": prompt,
                        "images": images,
                        "aspect_ratio": "match_input_image",
                        "seed": -1,
                        "disable_safety_checker": False,
                        "enable_sync_mode": False
                    }
                }
            }
        }

    def has_model(self, model: str) -> bool:
        return model in self.models

    def has_edit_model(self, model: str) -> bool:
        return model in self.edit_models

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def _execute_request(self,
                               url: str,
                               payload: dict,
                               operation: str,
                               discord_user_id: Optional[int] = None,
                               discord_channel_id: Optional[int] = None) -> tuple[bytes, Optional[float]]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        timeout = httpx.Timeout(90.0)
        async with create_async_client(timeout=timeout, follow_redirects=True, service_name="runpod") as client:
            try:
                logger.info(f"Sending {operation} request to Runpod: {url}...")
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()

                status = data.get("status")
                if status == "COMPLETED":
                    output = data.get("output", {})
                    # Some endpoints return "result" instead of "image_url"
                    image_url = output.get("image_url") or output.get("result")
                    cost = output.get("cost")

                    if not image_url or not isinstance(image_url, str):
                        raise RunpodServiceError(
                            f"Runpod {operation} completed but no valid image URL found in output: {output}")

                    logger.info(f"Downloading Runpod image from: {image_url}")
                    img_response = await client.get(image_url)
                    img_response.raise_for_status()

                    return img_response.content, cost
                elif status == "FAILED":
                    error_msg = data.get("error", "Unknown error")
                    raise RunpodServiceError(
                        f"Runpod {operation} failed: {error_msg}")
                else:
                    raise RunpodServiceError(
                        f"Runpod returned unexpected status: {status} - request might have timed out.")

            except httpx.HTTPError as e:
                logger.error(f"HTTP error during Runpod API call: {e}")
                raise RunpodServiceError(
                    f"Failed to communicate with Runpod API: {str(e)}")
            except RunpodServiceError:
                raise
            except Exception as e:
                logger.error(f"Unexpected error in Runpod service: {e}")
                raise RunpodServiceError(f"Unexpected error: {str(e)}")

    async def generate_image(
            self, prompt: str, model: str, discord_user_id: Optional[int] = None, discord_channel_id: Optional[int] = None) -> tuple[bytes, Optional[float]]:
        if not self.is_configured():
            raise RunpodServiceError("Runpod API key is not configured.")

        if not self.has_model(model):
            raise RunpodServiceError(
                f"Model {model} is not supported by Runpod service.")

        model_config = self.models[model]
        return await self._execute_request(model_config["url"], model_config["payload"](prompt), "generate", discord_user_id, discord_channel_id)

    async def edit_image(self, prompt: str, model: str,
                         images: list[str], discord_user_id: Optional[int] = None, discord_channel_id: Optional[int] = None) -> tuple[bytes, Optional[float]]:
        if not self.is_configured():
            raise RunpodServiceError("Runpod API key is not configured.")

        if not self.has_edit_model(model):
            raise RunpodServiceError(
                f"Model {model} is not supported for editing by Runpod service.")

        model_config = self.edit_models[model]
        return await self._execute_request(model_config["url"], model_config["payload"](prompt, images), "edit", discord_user_id, discord_channel_id)


runpod_service = RunpodService()
