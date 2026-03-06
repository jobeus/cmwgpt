"""Tests for ImageCommands with injected dependencies."""

import unittest
from unittest.mock import ANY, AsyncMock, MagicMock

from src.bot.commands.image import ImageCommands


class _AsyncTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TestImageCommandsInjection(unittest.IsolatedAsyncioTestCase):
    def test_editmodel_choices_follow_injected_runpod_flag(self):
        no_runpod_commands = ImageCommands(
            MagicMock(),
            state_service=MagicMock(),
            message_service=MagicMock(),
            runpod_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=False,
        )
        no_runpod_choices = [
            choice.value
            for choice in no_runpod_commands._create_editmodel_command().parameters[0].choices
        ]

        with_runpod_commands = ImageCommands(
            MagicMock(),
            state_service=MagicMock(),
            message_service=MagicMock(),
            runpod_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=True,
        )
        with_runpod_choices = [
            choice.value
            for choice in with_runpod_commands._create_editmodel_command().parameters[0].choices
        ]

        self.assertEqual(no_runpod_choices, ["seedream"])
        self.assertEqual(with_runpod_choices, ["seedream", "qwen", "pruna"])

    async def test_handle_draw_command_uses_injected_services(self):
        state_service = MagicMock()
        state_service.get_draw_model.return_value = "wan-2.6"

        message_service = MagicMock()
        message_service.format_prompt_message.return_value = "> test prompt"

        runpod_service = MagicMock()
        runpod_service.has_model.return_value = True
        runpod_service.generate_image = AsyncMock(return_value=(b"png-bytes", 0.1234))

        interaction = MagicMock()
        interaction.channel.id = 321
        interaction.channel.typing.return_value = _AsyncTyping()
        interaction.user.id = 654
        interaction.followup.send = AsyncMock()

        commands = ImageCommands(
            MagicMock(),
            state_service=state_service,
            message_service=message_service,
            runpod_service=runpod_service,
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=True,
        )

        await commands._handle_draw_command(interaction, "test prompt")

        state_service.mark_channel_active.assert_called_once_with(321)
        runpod_service.generate_image.assert_awaited_once_with(
            prompt="test prompt",
            model="wan-2.6",
            discord_user_id=654,
            discord_channel_id=321,
        )
        interaction.followup.send.assert_awaited_once_with(
            content="> [$0.123 @ wan-2.6] test prompt",
            file=ANY,
        )