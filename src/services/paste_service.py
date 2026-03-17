"""Paste Service - Handles uploading long content to paste services."""

import logging
from typing import Callable, Optional

import httpx

from src.utils.url_utils import inject_article_cache
from src.utils.http_client import create_async_client

logger = logging.getLogger(__name__)


class PasteService:
    """Service for handling paste operations."""

    def __init__(
        self,
        base_url: str = "https://paste.rs",
        *,
        cache_injector: Optional[Callable[[str, str], None]] = None,
        client_factory=create_async_client,
    ):
        self.base_url = base_url
        self._cache_injector = cache_injector
        self._client_factory = client_factory

    async def upload_text(self, text: str) -> str:
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
            async with self._client_factory(timeout=httpx.Timeout(10.0)) as client:
                response = await client.post(
                    self.base_url, content=text.encode("utf-8"))

                if response.status_code == 201:
                    paste_url = response.text.strip() + ".md"
                    if self._cache_injector:
                        self._cache_injector(paste_url, text)
                    return paste_url
                else:
                    raise Exception(
                        f"paste.rs error: {response.status_code} - {response.text}")
        except httpx.RequestError as e:
            logger.error(f"Network error uploading to paste service: {e}")
            raise Exception(f"Failed to upload to paste service: {e}")

    async def upload_markdown(self, markdown_text: str) -> str:
        """
        Upload markdown text to paste service.

        Args:
            markdown_text: The markdown content to upload

        Returns:
            URL of the uploaded paste
        """
        return await self.upload_text(markdown_text)


paste_service = PasteService(cache_injector=inject_article_cache)
