"""
Pasters utility - Legacy compatibility module

This module provides backward compatibility for the old pasters interface
while using the new refactored service architecture.
"""

from src.services.paste_service import paste_service

# For testing compatibility, expose requests module
import requests  # noqa: F401


async def upload_to_pasters(markdown_text: str) -> str:
    """
    Upload markdown text to paste service.

    Legacy compatibility function that delegates to the new service.
    """
    return await paste_service.upload_markdown(markdown_text)
