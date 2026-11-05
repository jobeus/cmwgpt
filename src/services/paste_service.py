"""Paste Service - Handles uploading long content to paste services."""

import logging
from typing import Awaitable, Callable, Optional

import httpx


logger = logging.getLogger(__name__)


class PasteService:
    """Service for handling paste operations."""

    def __init__(
        self,
        base_url: str = "https://paste.jobe.wtf",
        *,
        cache_injector: Optional[Callable[[str, str], Awaitable[None]]] = None,
        client_factory=httpx.AsyncClient,
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
                        await self._cache_injector(paste_url, text)
                    return paste_url
                else:
                    raise Exception(
                        f"Paste service error: {response.status_code} - {response.text}")
        except httpx.RequestError as e:
            logger.error(f"Network error uploading to paste service: {e}")
            raise Exception(f"Failed to upload to paste service: {e}") from e

    async def upload_image(self, data: bytes, media_type: str = "image/png") -> str:
        """Upload a PNG or JPEG and return its URL, with an extension so it opens as an image.

        Raises:
            Exception: If the upload fails (anything but 201, e.g. 413 over 20 MiB)
        """
        extension = ".jpg" if media_type == "image/jpeg" else ".png"
        async with self._client_factory(timeout=httpx.Timeout(20.0)) as client:
            response = await client.post(self.base_url, content=data)
        if response.status_code != 201:
            raise Exception(f"Paste service error: {response.status_code} - {response.text[:200]}")
        url = response.text.strip()
        # The server adds the extension itself for images; older versions didn't.
        return url if "." in url.rsplit("/", 1)[-1] else url + extension

    async def upload_markdown(self, markdown_text: str) -> str:
        """
        Upload markdown text to paste service.

        Args:
            markdown_text: The markdown content to upload

        Returns:
            URL of the uploaded paste
        """
        return await self.upload_text(markdown_text)

