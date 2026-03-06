"""Tests for MessageService with injected optional dependencies."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from src.services.message_service import MessageService


class TestMessageService(unittest.IsolatedAsyncioTestCase):
    async def test_long_reply_uses_injected_paste_service(self):
        paste_service = MagicMock()
        paste_service.upload_markdown = AsyncMock(return_value="https://paste.rs/long.md")
        channel = AsyncMock()
        service = MessageService(paste_service_instance=paste_service)

        await service.send_channel_reply(channel, "x" * 2500)

        paste_service.upload_markdown.assert_awaited_once()
        channel.send.assert_awaited_once()
        sent_message = channel.send.await_args.args[0]
        self.assertIn("too long", sent_message)
        self.assertIn("https://paste.rs/long.md", sent_message)

    async def test_long_reply_without_paste_service_falls_back_cleanly(self):
        channel = AsyncMock()
        service = MessageService(paste_service_instance=None)

        await service.send_channel_reply(channel, "x" * 2500)

        channel.send.assert_awaited_once()
        sent_message = channel.send.await_args.args[0]
        self.assertIn("over 2000 characters", sent_message.lower())


if __name__ == "__main__":
    unittest.main()