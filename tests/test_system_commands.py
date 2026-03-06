"""Tests for SystemCommands handlers with injected dependencies."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord.ext.commands  # noqa: F401

from src.bot.commands.system import SystemCommands


def make_interaction(channel_id=123, *, is_admin=True):
    interaction = MagicMock()
    interaction.channel = MagicMock()
    interaction.channel.id = channel_id
    interaction.user = MagicMock()
    interaction.user.guild_permissions = SimpleNamespace(administrator=is_admin)
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    return interaction


class TestSystemCommands(unittest.IsolatedAsyncioTestCase):
    def make_commands(self, *, auto_update_service=None, current_prompt=None, default_model="default-model"):
        bot = MagicMock()
        bot.user = SimpleNamespace(id=999)
        queue_service = MagicMock()
        queue_service.queue_command = AsyncMock(return_value=True)
        state_service = MagicMock()
        state_service.get_system_prompt.return_value = current_prompt
        commands = SystemCommands(
            bot,
            queue_service_instance=queue_service,
            state_service_instance=state_service,
            auto_update_service=auto_update_service,
            system_prompt_loader=lambda: "base prompt",
            default_model=default_model,
            mention_legend_provider=AsyncMock(return_value="legend text"),
        )
        return commands, bot, queue_service, state_service

    async def test_handle_model_command_sets_channel_model(self):
        commands, _, _, state_service = self.make_commands()
        interaction = make_interaction(channel_id=321)

        await commands._handle_model_command(interaction, "gpt-test")

        state_service.mark_channel_active.assert_called_once_with(321)
        state_service.set_model.assert_called_once_with(321, "gpt-test")
        interaction.followup.send.assert_awaited_once_with("Model set to `gpt-test`.", ephemeral=True)

    async def test_handle_model_command_reports_current_or_default_model(self):
        commands, _, _, state_service = self.make_commands(default_model="fallback-model")
        interaction = make_interaction(channel_id=654)
        state_service.get_model.return_value = None

        await commands._handle_model_command(interaction, None)

        interaction.followup.send.assert_awaited_once_with("Model is `fallback-model`.", ephemeral=True)

    async def test_handle_systemprompt_set_persists_custom_prompt(self):
        commands, _, _, state_service = self.make_commands()
        interaction = make_interaction(channel_id=111)

        await commands._handle_systemprompt_set(interaction, "be concise")

        state_service.mark_channel_active.assert_called_once_with(111)
        state_service.set_system_prompt.assert_called_once_with(111, "be concise")
        interaction.followup.send.assert_awaited_once()

    async def test_handle_systemprompt_set_without_prompt_shows_default_composed_prompt(self):
        commands, _, _, state_service = self.make_commands(current_prompt=None)
        interaction = make_interaction(channel_id=222)

        await commands._handle_systemprompt_set(interaction, None)

        state_service.get_system_prompt.assert_called_once_with(222)
        sent_content = interaction.followup.send.await_args.args[0]
        self.assertIn("base prompt", sent_content)
        self.assertIn("legend text", sent_content)

    async def test_handle_systemprompt_view_truncates_long_content(self):
        commands, _, _, state_service = self.make_commands(current_prompt="x" * 3000)
        interaction = make_interaction(channel_id=333)

        await commands._handle_systemprompt_view(interaction)

        state_service.get_system_prompt.assert_called_once_with(333)
        sent_content = interaction.followup.send.await_args.args[0]
        self.assertIn("[truncated]", sent_content)
        self.assertLessEqual(len(sent_content), 2015)

    async def test_handle_systemprompt_reset_clears_prompt(self):
        commands, _, _, state_service = self.make_commands()
        interaction = make_interaction(channel_id=444)

        await commands._handle_systemprompt_reset(interaction)

        state_service.mark_channel_active.assert_called_once_with(444)
        state_service.clear_system_prompt.assert_called_once_with(444)
        interaction.followup.send.assert_awaited_once_with(
            "System prompt for this channel has been reset to the default.", ephemeral=True
        )

    async def test_restart_command_denies_non_admin_users(self):
        commands, _, queue_service, _ = self.make_commands(auto_update_service=MagicMock())
        interaction = make_interaction(is_admin=False)
        restart_command = commands._create_restart_command()

        await restart_command.callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        queue_service.queue_command.assert_not_awaited()

    async def test_handle_restart_command_reports_missing_service(self):
        commands, _, _, _ = self.make_commands(auto_update_service=None)
        interaction = make_interaction()

        await commands._handle_restart_command(interaction)

        self.assertEqual(interaction.followup.send.await_count, 2)
        final_message = interaction.followup.send.await_args_list[-1].args[0]
        self.assertIn("Restart service is not configured", final_message)

    async def test_handle_restart_command_reports_failed_trigger(self):
        auto_update_service = MagicMock()
        auto_update_service.trigger_restart = AsyncMock(return_value=False)
        commands, _, _, _ = self.make_commands(auto_update_service=auto_update_service)
        interaction = make_interaction()

        await commands._handle_restart_command(interaction)

        auto_update_service.trigger_restart.assert_awaited_once_with(manual=True)
        self.assertEqual(interaction.followup.send.await_count, 2)
        final_message = interaction.followup.send.await_args_list[-1].args[0]
        self.assertIn("Failed to trigger restart", final_message)

    async def test_setup_commands_registers_all_system_commands(self):
        commands, bot, _, _ = self.make_commands(auto_update_service=MagicMock())

        commands.setup_commands()

        self.assertEqual(bot.tree.add_command.call_count, 4)

    async def test_model_command_defers_and_reports_busy_when_queue_is_full(self):
        commands, _, queue_service, _ = self.make_commands()
        queue_service.queue_command = AsyncMock(return_value=False)
        interaction = make_interaction()
        model_command = commands._create_model_command()

        await model_command.callback(interaction, None)

        interaction.response.defer.assert_awaited_once_with(ephemeral=False, thinking=True)
        queue_service.queue_command.assert_awaited_once_with(interaction, commands._handle_model_command, None)
        interaction.followup.send.assert_awaited_once_with(
            "Sorry, the bot is currently busy. Please try again in a moment.", ephemeral=True
        )

    async def test_systemprompt_set_wrapper_defers_and_reports_busy_when_queue_is_full(self):
        commands, _, queue_service, _ = self.make_commands()
        queue_service.queue_command = AsyncMock(return_value=False)
        interaction = make_interaction()
        group = commands._create_systemprompt_group()
        set_command = next(cmd for cmd in group.commands if cmd.name == "set")

        await set_command.callback(interaction, "new prompt")

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        queue_service.queue_command.assert_awaited_once_with(interaction, commands._handle_systemprompt_set, "new prompt")
        interaction.followup.send.assert_awaited_once_with(
            "Sorry, the bot is currently busy. Please try again in a moment.", ephemeral=True
        )

    async def test_systemprompt_view_wrapper_schedules_safe_run(self):
        commands, _, _, _ = self.make_commands()
        interaction = make_interaction()
        group = commands._create_systemprompt_group()
        view_command = next(cmd for cmd in group.commands if cmd.name == "view")

        with patch("src.bot.commands.system.safe_run", new=AsyncMock()) as mock_safe_run, patch(
            "src.bot.commands.system.asyncio.create_task", side_effect=lambda coro: coro.close()
        ) as mock_create_task:
            await view_command.callback(interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        mock_safe_run.assert_called_once_with(interaction, commands._handle_systemprompt_view, interaction)
        mock_create_task.assert_called_once()

    async def test_systemprompt_reset_wrapper_reports_busy_when_queue_is_full(self):
        commands, _, queue_service, _ = self.make_commands()
        queue_service.queue_command = AsyncMock(return_value=False)
        interaction = make_interaction()
        group = commands._create_systemprompt_group()
        reset_command = next(cmd for cmd in group.commands if cmd.name == "reset")

        await reset_command.callback(interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        queue_service.queue_command.assert_awaited_once_with(interaction, commands._handle_systemprompt_reset)
        interaction.followup.send.assert_awaited_once_with(
            "Sorry, the bot is currently busy. Please try again in a moment.", ephemeral=True
        )

    async def test_help_command_wrapper_schedules_safe_run(self):
        commands, _, _, _ = self.make_commands()
        interaction = make_interaction()
        help_command = commands._create_help_command()

        with patch("src.bot.commands.system.safe_run", new=AsyncMock()) as mock_safe_run, patch(
            "src.bot.commands.system.asyncio.create_task", side_effect=lambda coro: coro.close()
        ) as mock_create_task:
            await help_command.callback(interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        mock_safe_run.assert_called_once_with(interaction, commands._handle_help_command, interaction)
        mock_create_task.assert_called_once()

    async def test_handle_help_command_sends_help_text(self):
        commands, _, _, _ = self.make_commands()
        interaction = make_interaction()

        await commands._handle_help_command(interaction)

        sent_content = interaction.followup.send.await_args.args[0]
        self.assertIn("AI Bot Commands Help", sent_content)
        self.assertIn("/draw", sent_content)
        self.assertIn("/death set|view|reset", sent_content)

    async def test_restart_command_wrapper_reports_busy_when_queue_is_full(self):
        commands, _, queue_service, _ = self.make_commands(auto_update_service=MagicMock())
        queue_service.queue_command = AsyncMock(return_value=False)
        interaction = make_interaction(is_admin=True)
        restart_command = commands._create_restart_command()

        await restart_command.callback(interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=False, thinking=True)
        queue_service.queue_command.assert_awaited_once_with(interaction, commands._handle_restart_command)
        interaction.followup.send.assert_awaited_once_with(
            "Sorry, the bot is currently busy. Please try again in a moment.", ephemeral=True
        )
