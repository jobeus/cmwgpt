"""Tests for InterjectService with injected mock dependencies."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from src.services.interject_service import InterjectService


class TestInterjectServiceInjection(unittest.IsolatedAsyncioTestCase):
    async def test_do_interject_uses_injected_dependencies(self):
        state_service = MagicMock()
        state_service.get_system_prompt.return_value = None
        state_service.get_model.return_value = None

        openai_service = MagicMock()
        openai_service.get_chat_completion = AsyncMock(return_value="injected response")

        message_service = MagicMock()
        message_service.send_channel_reply = AsyncMock()

        async def mention_legend_provider(channel, bot_user):
            return "legend"

        service = InterjectService(
            state_service=state_service,
            openai_service=openai_service,
            message_service=message_service,
            system_prompt_loader=lambda: "default prompt",
            default_model="test-model",
            state_file="",
            mention_legend_provider=mention_legend_provider,
        )

        channel = MagicMock()
        channel.id = 123
        channel.name = "general"
        message = MagicMock()
        message.author.id = 456
        message.content = "hello"
        message.reference = None
        message.created_at = MagicMock()
        message.created_at.astimezone.return_value.strftime.return_value = "2024-01-01 00:00:00"

        async def history(limit):
            yield message

        channel.history = history
        bot_user = MagicMock()
        service.set_bot(MagicMock(user=bot_user))

        await service._do_interject(channel, 999)

        openai_service.get_chat_completion.assert_awaited_once()
        message_service.send_channel_reply.assert_awaited_once_with(channel, "injected response")


if __name__ == "__main__":
    unittest.main()