"""Unit tests for the stateless paste utility helpers."""

import unittest
from typing import List

from src.utils.pasters import upload_to_pasters


class StubUploader:
    def __init__(self, url: str):
        self.url = url
        self.calls: List[str] = []

    async def upload_markdown(self, markdown_text: str) -> str:
        self.calls.append(markdown_text)
        return self.url


class TestPasters(unittest.IsolatedAsyncioTestCase):
    async def test_upload_to_pasters_uses_injected_uploader(self):
        uploader = StubUploader("https://paste.rs/abc123.md")

        result = await upload_to_pasters("hello world", uploader=uploader)

        self.assertEqual(result, "https://paste.rs/abc123.md")
        self.assertEqual(uploader.calls, ["hello world"])

    async def test_upload_to_pasters_can_swap_uploaders_without_patching_globals(self):
        uploader = StubUploader("https://example.com/custom.md")

        result = await upload_to_pasters("different content", uploader=uploader)

        self.assertEqual(result, "https://example.com/custom.md")
        self.assertEqual(uploader.calls, ["different content"])


if __name__ == "__main__":
    unittest.main()
