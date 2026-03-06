"""Stateless paste helpers."""

from typing import Protocol

import requests  # noqa: F401


class MarkdownPasteUploader(Protocol):
    async def upload_markdown(self, markdown_text: str) -> str:
        """Upload markdown and return a URL."""


async def upload_to_pasters(markdown_text: str, uploader: MarkdownPasteUploader) -> str:
    """Upload markdown text using an injected uploader dependency."""
    return await uploader.upload_markdown(markdown_text)
