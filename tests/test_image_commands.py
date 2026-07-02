"""Tests for ImageCommands with injected dependencies."""

import unittest
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from src.bot.commands.image import ImageCommands
from src.services.openai_service import OpenAIServiceError
from src.services.runpod_service import RunpodServiceError


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
            runpod_service=MagicMock(), gemini_service=MagicMock(),
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
            runpod_service=MagicMock(), gemini_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=True,
        )
        with_runpod_choices = [
            choice.value
            for choice in with_runpod_commands._create_editmodel_command().parameters[0].choices
        ]

        self.assertEqual(no_runpod_choices, ["gemini-3-pro-image", "gemini-3.1-flash-image", "seedream"])
        self.assertEqual(with_runpod_choices, ["gemini-3-pro-image", "gemini-3.1-flash-image", "seedream", "qwen", "pruna"])

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
            runpod_service=runpod_service, gemini_service=MagicMock(),
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

    async def test_handle_drawmodel_command_reports_default_when_unset(self):
        state_service = MagicMock()
        state_service.get_draw_model.return_value = None
        interaction = MagicMock()
        interaction.channel.id = 123
        interaction.followup.send = AsyncMock()

        commands = ImageCommands(
            MagicMock(),
            state_service=state_service,
            message_service=MagicMock(),
            runpod_service=MagicMock(), gemini_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=True,
        )

        await commands._handle_drawmodel_command(interaction)

        interaction.followup.send.assert_awaited_once_with(
            "Default draw model is `seedream`.", ephemeral=True
        )

    async def test_handle_edit_command_uses_runpod_service_and_formats_attachments(self):
        state_service = MagicMock()
        state_service.get_edit_model.return_value = "qwen"

        message_service = MagicMock()
        message_service.format_attachment_message.return_value = "> edited prompt"

        runpod_service = MagicMock()
        runpod_service.has_edit_model.return_value = True
        runpod_service.edit_image = AsyncMock(return_value=(b"edited-bytes", 0.4321))

        interaction = MagicMock()
        interaction.channel.id = 456
        interaction.channel.typing.return_value = _AsyncTyping()
        interaction.user.id = 789
        interaction.followup.send = AsyncMock()

        edit_image = MagicMock()
        edit_image.filename = "original.png"
        edit_image.read = AsyncMock(return_value=b"img-1")
        second_image = MagicMock()
        second_image.read = AsyncMock(return_value=b"img-2")

        commands = ImageCommands(
            MagicMock(),
            state_service=state_service,
            message_service=message_service,
            runpod_service=runpod_service, gemini_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=True,
        )

        await commands._handle_edit_command(
            interaction,
            "edited prompt",
            edit_image,
            second_image,
        )

        runpod_service.edit_image.assert_awaited_once()
        kwargs = runpod_service.edit_image.await_args.kwargs
        self.assertEqual(kwargs["prompt"], "edited prompt")
        self.assertEqual(kwargs["model"], "qwen")
        self.assertEqual(len(kwargs["images"]), 2)
        message_service.format_attachment_message.assert_called_once()
        interaction.followup.send.assert_awaited_once_with(content="> edited prompt", file=ANY, embed=ANY)

    async def test_handle_edit_command_rejects_unsupported_models(self):
        state_service = MagicMock()
        state_service.get_edit_model.return_value = "not-supported"
        runpod_service = MagicMock()
        runpod_service.has_edit_model.return_value = False

        interaction = MagicMock()
        interaction.channel.id = 456
        interaction.channel.typing.return_value = _AsyncTyping()
        interaction.followup.send = AsyncMock()

        edit_image = MagicMock()
        edit_image.filename = "original.png"

        commands = ImageCommands(
            MagicMock(),
            state_service=state_service,
            message_service=MagicMock(),
            runpod_service=runpod_service, gemini_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=True,
        )

        await commands._handle_edit_command(interaction, "edited prompt", edit_image)

        interaction.followup.send.assert_awaited_once_with(
            content="Sorry, model not-supported does not support editing."
        )

    async def test_handle_editmodel_command_sets_model(self):
        state_service = MagicMock()
        interaction = MagicMock()
        interaction.channel.id = 222
        interaction.followup.send = AsyncMock()

        commands = ImageCommands(
            MagicMock(),
            state_service=state_service,
            message_service=MagicMock(),
            runpod_service=MagicMock(), gemini_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=True,
        )

        await commands._handle_editmodel_command(interaction, "pruna")

        state_service.set_edit_model.assert_called_once_with(222, "pruna")
        interaction.followup.send.assert_awaited_once_with(
            "Default edit model set to `pruna`.", ephemeral=True
        )

    async def test_handle_drawmodel_command_sets_model(self):
        state_service = MagicMock()
        interaction = MagicMock()
        interaction.channel.id = 888
        interaction.followup.send = AsyncMock()

        commands = ImageCommands(
            MagicMock(),
            state_service=state_service,
            message_service=MagicMock(),
            runpod_service=MagicMock(), gemini_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=True,
        )

        await commands._handle_drawmodel_command(interaction, "wan-2.6")

        state_service.set_draw_model.assert_called_once_with(888, "wan-2.6")
        interaction.followup.send.assert_awaited_once_with(
            "Default draw model set to `wan-2.6`.", ephemeral=True
        )

    async def test_handle_editmodel_command_reports_default_when_unset(self):
        state_service = MagicMock()
        state_service.get_edit_model.return_value = None
        interaction = MagicMock()
        interaction.channel.id = 999
        interaction.followup.send = AsyncMock()

        commands = ImageCommands(
            MagicMock(),
            state_service=state_service,
            message_service=MagicMock(),
            runpod_service=MagicMock(), gemini_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="default-edit",
            enable_runpod_models=True,
        )

        await commands._handle_editmodel_command(interaction)

        interaction.followup.send.assert_awaited_once_with(
            "Default edit model is `default-edit`.", ephemeral=True
        )

    async def test_handle_draw_command_reports_runpod_errors(self):
        state_service = MagicMock()
        message_service = MagicMock()
        message_service.format_prompt_message.return_value = "> prompt"
        runpod_service = MagicMock()
        runpod_service.has_model.return_value = True
        runpod_service.generate_image = AsyncMock(side_effect=RunpodServiceError("bad model"))

        interaction = MagicMock()
        interaction.channel.id = 101
        interaction.channel.typing.return_value = _AsyncTyping()
        interaction.user.id = 202
        interaction.followup.send = AsyncMock()

        commands = ImageCommands(
            MagicMock(),
            state_service=state_service,
            message_service=message_service,
            runpod_service=runpod_service, gemini_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=True,
        )

        await commands._handle_draw_command(interaction, "prompt", "seedream")

        sent_content = interaction.followup.send.await_args.kwargs["content"]
        self.assertIn("bad model", sent_content)

    async def test_handle_draw_command_runpod_error_uses_fallback_when_discord_send_fails(self):
        state_service = MagicMock()
        message_service = MagicMock()
        message_service.format_prompt_message.return_value = "> prompt"
        runpod_service = MagicMock()
        runpod_service.has_model.return_value = True
        runpod_service.generate_image = AsyncMock(side_effect=RunpodServiceError("bad model"))

        interaction = MagicMock()
        interaction.channel.id = 111
        interaction.channel.typing.return_value = _AsyncTyping()
        interaction.user.id = 222
        interaction.followup.send = AsyncMock(side_effect=[RuntimeError("send failed"), RuntimeError("fallback failed")])

        commands = ImageCommands(
            MagicMock(),
            state_service=state_service,
            message_service=message_service,
            runpod_service=runpod_service, gemini_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=True,
        )

        await commands._handle_draw_command(interaction, "prompt", "seedream")

        self.assertEqual(interaction.followup.send.await_count, 2)

    async def test_handle_draw_command_reports_unexpected_errors(self):
        state_service = MagicMock()
        message_service = MagicMock()
        message_service.format_prompt_message.return_value = "> prompt"
        runpod_service = MagicMock()
        runpod_service.has_model.return_value = False

        interaction = MagicMock()
        interaction.channel.id = 103
        interaction.channel.typing.return_value = _AsyncTyping()
        interaction.followup.send = AsyncMock()

        commands = ImageCommands(
            MagicMock(),
            state_service=state_service,
            message_service=message_service,
            runpod_service=runpod_service, gemini_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=True,
        )

        await commands._handle_draw_command(interaction, "prompt", "seedream")

        sent_content = interaction.followup.send.await_args.kwargs["content"]
        self.assertIn("unexpected error", sent_content.lower())

    async def test_handle_draw_command_reports_openai_errors_with_fallback_send(self):
        state_service = MagicMock()
        message_service = MagicMock()
        message_service.format_prompt_message.return_value = "> prompt"
        runpod_service = MagicMock()
        runpod_service.has_model.return_value = True
        runpod_service.generate_image = AsyncMock(side_effect=OpenAIServiceError("oops"))

        interaction = MagicMock()
        interaction.channel.id = 112
        interaction.channel.typing.return_value = _AsyncTyping()
        interaction.user.id = 223
        interaction.followup.send = AsyncMock(side_effect=[RuntimeError("send failed"), RuntimeError("fallback failed")])

        commands = ImageCommands(
            MagicMock(),
            state_service=state_service,
            message_service=message_service,
            runpod_service=runpod_service, gemini_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=True,
        )

        await commands._handle_draw_command(interaction, "prompt", "seedream")

        self.assertEqual(interaction.followup.send.await_count, 2)

    async def test_handle_draw_command_reports_unexpected_errors_with_fallback_send(self):
        state_service = MagicMock()
        message_service = MagicMock()
        message_service.format_prompt_message.return_value = "> prompt"
        runpod_service = MagicMock()
        runpod_service.has_model.return_value = False

        interaction = MagicMock()
        interaction.channel.id = 113
        interaction.channel.typing.return_value = _AsyncTyping()
        interaction.followup.send = AsyncMock(side_effect=[RuntimeError("send failed"), RuntimeError("fallback failed")])

        commands = ImageCommands(
            MagicMock(),
            state_service=state_service,
            message_service=message_service,
            runpod_service=runpod_service, gemini_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=True,
        )

        await commands._handle_draw_command(interaction, "prompt", "seedream")

        self.assertEqual(interaction.followup.send.await_count, 2)

    async def test_handle_edit_command_reports_openai_errors(self):
        state_service = MagicMock()
        message_service = MagicMock()
        message_service.format_prompt_message.return_value = "> prompt"
        runpod_service = MagicMock()
        runpod_service.has_edit_model.return_value = True
        runpod_service.edit_image = AsyncMock(side_effect=OpenAIServiceError("oops"))

        interaction = MagicMock()
        interaction.channel.id = 104
        interaction.channel.typing.return_value = _AsyncTyping()
        interaction.user.id = 203
        interaction.followup.send = AsyncMock()

        edit_image = MagicMock()
        edit_image.filename = "original.png"
        edit_image.read = AsyncMock(return_value=b"img")

        commands = ImageCommands(
            MagicMock(),
            state_service=state_service,
            message_service=message_service,
            runpod_service=runpod_service, gemini_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=True,
        )

        await commands._handle_edit_command(interaction, "prompt", edit_image)

        sent_content = interaction.followup.send.await_args.kwargs["content"]
        self.assertIn("error while editing", sent_content.lower())

    async def test_handle_edit_command_openai_error_uses_fallback_when_discord_send_fails(self):
        state_service = MagicMock()
        message_service = MagicMock()
        message_service.format_prompt_message.return_value = "> prompt"
        runpod_service = MagicMock()
        runpod_service.has_edit_model.return_value = True
        runpod_service.edit_image = AsyncMock(side_effect=OpenAIServiceError("oops"))

        interaction = MagicMock()
        interaction.channel.id = 114
        interaction.channel.typing.return_value = _AsyncTyping()
        interaction.user.id = 224
        interaction.followup.send = AsyncMock(side_effect=[RuntimeError("send failed"), RuntimeError("fallback failed")])

        edit_image = MagicMock()
        edit_image.filename = "original.png"
        edit_image.read = AsyncMock(return_value=b"img")

        commands = ImageCommands(
            MagicMock(),
            state_service=state_service,
            message_service=message_service,
            runpod_service=runpod_service, gemini_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=True,
        )

        await commands._handle_edit_command(interaction, "prompt", edit_image)

        self.assertEqual(interaction.followup.send.await_count, 2)

    async def test_handle_edit_command_reports_unexpected_errors(self):
        state_service = MagicMock()
        message_service = MagicMock()
        message_service.format_prompt_message.return_value = "> prompt"
        runpod_service = MagicMock()
        runpod_service.has_edit_model.return_value = True
        runpod_service.edit_image = AsyncMock(side_effect=RuntimeError("boom"))

        interaction = MagicMock()
        interaction.channel.id = 105
        interaction.channel.typing.return_value = _AsyncTyping()
        interaction.user.id = 204
        interaction.followup.send = AsyncMock()

        edit_image = MagicMock()
        edit_image.filename = "original.png"
        edit_image.read = AsyncMock(return_value=b"img")

        commands = ImageCommands(
            MagicMock(),
            state_service=state_service,
            message_service=message_service,
            runpod_service=runpod_service, gemini_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=True,
        )

        await commands._handle_edit_command(interaction, "prompt", edit_image)

        sent_content = interaction.followup.send.await_args.kwargs["content"]
        self.assertIn("unexpected error editing", sent_content.lower())

    async def test_handle_edit_command_reports_unexpected_errors_with_fallback_send(self):
        state_service = MagicMock()
        message_service = MagicMock()
        message_service.format_prompt_message.return_value = "> prompt"
        runpod_service = MagicMock()
        runpod_service.has_edit_model.return_value = True
        runpod_service.edit_image = AsyncMock(side_effect=RuntimeError("boom"))

        interaction = MagicMock()
        interaction.channel.id = 115
        interaction.channel.typing.return_value = _AsyncTyping()
        interaction.user.id = 225
        interaction.followup.send = AsyncMock(side_effect=[RuntimeError("send failed"), RuntimeError("fallback failed")])

        edit_image = MagicMock()
        edit_image.filename = "original.png"
        edit_image.read = AsyncMock(return_value=b"img")

        commands = ImageCommands(
            MagicMock(),
            state_service=state_service,
            message_service=message_service,
            runpod_service=runpod_service, gemini_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=True,
        )

        await commands._handle_edit_command(interaction, "prompt", edit_image)

        self.assertEqual(interaction.followup.send.await_count, 2)

    async def test_setup_commands_registers_all_image_commands(self):
        bot = MagicMock()
        commands = ImageCommands(
            bot,
            state_service=MagicMock(),
            message_service=MagicMock(),
            runpod_service=MagicMock(), gemini_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=True,
        )

        commands.setup_commands()

        self.assertEqual(bot.tree.add_command.call_count, 4)

    async def test_draw_command_wrapper_defers_and_schedules_safe_run(self):
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        commands = ImageCommands(
            MagicMock(),
            state_service=MagicMock(),
            message_service=MagicMock(),
            runpod_service=MagicMock(), gemini_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=True,
        )
        draw = commands._create_draw_command()

        with patch("src.bot.commands.image.safe_run", new=AsyncMock()) as mock_safe_run, patch(
            "src.bot.commands.image.asyncio.create_task", side_effect=lambda coro: coro.close()
        ) as mock_create_task:
            await draw.callback(interaction, "a prompt", None, None, None, None, None)

        interaction.response.defer.assert_awaited_once_with(ephemeral=False, thinking=True)
        mock_safe_run.assert_called_once_with(interaction, commands._handle_draw_command, interaction, "a prompt", None, None, None, None, None)
        mock_create_task.assert_called_once()

    async def test_drawmodel_command_wrapper_defers_and_schedules_safe_run(self):
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        commands = ImageCommands(
            MagicMock(),
            state_service=MagicMock(),
            message_service=MagicMock(),
            runpod_service=MagicMock(), gemini_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=True,
        )
        drawmodel = commands._create_drawmodel_command()

        with patch("src.bot.commands.image.safe_run", new=AsyncMock()) as mock_safe_run, patch(
            "src.bot.commands.image.asyncio.create_task", side_effect=lambda coro: coro.close()
        ) as mock_create_task:
            await drawmodel.callback(interaction, "seedream")

        interaction.response.defer.assert_awaited_once_with(ephemeral=False, thinking=True)
        mock_safe_run.assert_called_once_with(interaction, commands._handle_drawmodel_command, interaction, "seedream")
        mock_create_task.assert_called_once()

    async def test_edit_command_wrapper_defers_and_schedules_safe_run(self):
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        edit_image = MagicMock()
        commands = ImageCommands(
            MagicMock(),
            state_service=MagicMock(),
            message_service=MagicMock(),
            runpod_service=MagicMock(), gemini_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=True,
        )
        edit = commands._create_edit_command()

        with patch("src.bot.commands.image.safe_run", new=AsyncMock()) as mock_safe_run, patch(
            "src.bot.commands.image.asyncio.create_task", side_effect=lambda coro: coro.close()
        ) as mock_create_task:
            await edit.callback(interaction, "fix it", edit_image, None, None, None, "qwen")

        interaction.response.defer.assert_awaited_once_with(ephemeral=False, thinking=True)
        mock_safe_run.assert_called_once_with(
            interaction,
            commands._handle_edit_command,
            interaction,
            "fix it",
            edit_image,
            None,
            None,
            None,
            "qwen",
        )
        mock_create_task.assert_called_once()

    async def test_editmodel_command_wrapper_defers_and_schedules_safe_run(self):
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        commands = ImageCommands(
            MagicMock(),
            state_service=MagicMock(),
            message_service=MagicMock(),
            runpod_service=MagicMock(), gemini_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=True,
        )
        editmodel = commands._create_editmodel_command()

        with patch("src.bot.commands.image.safe_run", new=AsyncMock()) as mock_safe_run, patch(
            "src.bot.commands.image.asyncio.create_task", side_effect=lambda coro: coro.close()
        ) as mock_create_task:
            await editmodel.callback(interaction, "pruna")

        interaction.response.defer.assert_awaited_once_with(ephemeral=False, thinking=True)
        mock_safe_run.assert_called_once_with(interaction, commands._handle_editmodel_command, interaction, "pruna")
        mock_create_task.assert_called_once()
