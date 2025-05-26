"""
OpenAI Service - Handles all OpenAI API interactions
"""

import base64
from typing import List, Dict, Any, Optional

from openai import OpenAI
from discord import Attachment

from src.config import OPENAI_API_KEY, IS_TESTING


class OpenAIService:
    """Service for handling OpenAI API interactions."""

    def __init__(self):
        self._client: Optional[OpenAI] = None

    def set_client(self, client) -> None:
        """Set a custom client (useful for testing)."""
        self._client = client

    def get_client(self) -> OpenAI:
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
                self._client = OpenAI(api_key=OPENAI_API_KEY)
        return self._client

    def get_chat_completion(self, model: str, messages: List[Dict[str, Any]]) -> str:
        """
        Gets a chat completion from the OpenAI API.

        Args:
            model: The model to use for completion
            messages: List of message dictionaries for the conversation

        Returns:
            The completion text from OpenAI
        """
        client = self.get_client()
        response = client.responses.create(model=model, input=messages)
        return response.output_text

    def generate_image(self, prompt: str, model: str, edit_image: Optional[Attachment] = None) -> bytes:
        """
        Generates an image using the OpenAI API.

        Args:
            prompt: The text prompt for image generation
            model: The model to use for generation
            edit_image: Optional image to edit instead of generating new

        Returns:
            The raw image bytes

        Raises:
            ValueError: If image generation fails
        """
        client = self.get_client()
        b64_json_data = None
        result = None

        if model == "dall-e-2" or model == "dall-e-3":
            result = client.images.generate(model=model, prompt=prompt, n=1, response_format="b64_json")
        else:  # assume gpt-image-1 or similar custom model
            if edit_image:
                file_obj = edit_image.to_file()
                result = client.images.edit(
                    model=model,
                    image=[file_obj],
                    # image expects a list of file-like objects
                    prompt=prompt,
                )
            else:
                result = client.images.generate(
                    model=model,
                    prompt=prompt,
                    n=1,
                )

        if result:
            b64_json_data = result.data[0].b64_json

        if not b64_json_data:
            raise ValueError("Image generation failed, no b64_json data returned.")

        img_bytes = base64.b64decode(b64_json_data)
        return img_bytes


# Global service instance
openai_service = OpenAIService()
