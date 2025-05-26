"""
Paste Service - Handles uploading long content to paste services
"""

import io
import logging

import requests


logger = logging.getLogger(__name__)


class PasteService:
    """Service for handling paste operations."""

    def __init__(self, base_url: str = "https://paste.rs"):
        self.base_url = base_url

    def upload_text(self, text: str) -> str:
        """
        Upload text to paste service.

        Args:
            text: The text content to upload

        Returns:
            URL of the uploaded paste

        Raises:
            Exception: If upload fails
        """
        try:
            response = requests.post(self.base_url, data=io.BytesIO(text.encode("utf-8")), timeout=10)

            if response.status_code == 201:
                return response.text.strip() + ".md"
            else:
                raise Exception(f"paste.rs error: {response.status_code} - {response.text}")
        except requests.RequestException as e:
            logger.error(f"Network error uploading to paste service: {e}")
            raise Exception(f"Failed to upload to paste service: {e}")

    def upload_markdown(self, markdown_text: str) -> str:
        """
        Upload markdown text to paste service.

        Args:
            markdown_text: The markdown content to upload

        Returns:
            URL of the uploaded paste
        """
        return self.upload_text(markdown_text)


# Global service instance
paste_service = PasteService()
