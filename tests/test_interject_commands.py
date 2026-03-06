"""Tests for InterjectCommands handlers with injected services."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord.ext.commands  # noqa: F401

from src.bot.commands.interject import InterjectCommands


def make_interaction(channel_id=123, channel_name="general"):
    interaction = MagicMock()
    interaction.channel = MagicMock()
    interaction.channel.id = channel_id
    interaction.channel.name = channel_name
    interaction.followup = AsyncMock()
    interaction.response = AsyncMock()
    return interaction


class TestInterjectCommands(unittest.IsolatedAsyncioTestCase):
    def make_commands(self, *, current_settings=None, interject_service=None):
        bot = MagicMock()
        bot.user = SimpleNamespace(id=999)
        state_service = MagicMock()
        state_service.get_interject_settings.return_value = current_settings
        commands = InterjectCommands(
            bot,
            state_service_instance=state_service,
            interject_service=interject_service,
        )
        return commands, state_service

    async def test_handle_interject_set_requires_at_least_one_setting(self):
        commands, state_service = self.make_commands(current_settings={})
        interaction = make_interaction()

        await commands._handle_interject_set(interaction, None, None, None, None, None, None, None, None)

        interaction.followup.send.assert_awaited_once_with(
            "You must provide at least one setting to change.", ephemeral=True
        )
        state_service.set_interject_settings.assert_not_called()

    async def test_handle_interject_set_merges_and_persists_settings(self):
        commands, state_service = self.make_commands(current_settings={"chance": 5, "daily_max": 2})
        interaction = make_interaction(channel_id=321)

        await commands._handle_interject_set(interaction, 10, 20, 3, 2, 60, 8, 7, False)

        state_service.mark_channel_active.assert_called_once_with(321)
        state_service.set_interject_settings.assert_called_once_with(
            321,
            {
                "chance": 10,
                "daily_max": 7,
                "cooldown": 20,
                "min_messages": 3,
                "min_authors": 2,
                "window_mins": 60,
                "context_lines": 8,
                "exclude_embeds": False,
            },
        )
        sent_message = interaction.followup.send.await_args.args[0]
        self.assertIn("Chance: `10%`", sent_message)
        self.assertIn("Daily max: `7`", sent_message)

    async def test_handle_interject_reset_clears_settings(self):
        commands, state_service = self.make_commands(current_settings={"chance": 25})
        interaction = make_interaction(channel_id=222)

        await commands._handle_interject_reset(interaction)

        state_service.mark_channel_active.assert_called_once_with(222)
        state_service.clear_interject_settings.assert_called_once_with(222)
        interaction.followup.send.assert_awaited_once_with(
            "Interjection settings for this channel have been reset to defaults.", ephemeral=True
        )

    async def test_handle_interject_view_shows_custom_and_default_values(self):
        commands, _ = self.make_commands(current_settings={"chance": 25, "exclude_embeds": False})
        interaction = make_interaction(channel_name="bots")

        await commands._handle_interject_view(interaction)

        sent_message = interaction.followup.send.await_args.args[0]
        self.assertIn("Current Interjection Settings for #bots", sent_message)
        self.assertIn("Chance: `25%` (custom)", sent_message)
        self.assertIn("Cooldown:", sent_message)
        self.assertIn("(default)", sent_message)

    async def test_handle_interject_count_requires_configured_service(self):
        commands, _ = self.make_commands(interject_service=None)
        interaction = make_interaction()

        await commands._handle_interject_count(interaction)

        interaction.followup.send.assert_awaited_once_with(
            "Interject service is not configured.", ephemeral=True
        )

    async def test_handle_interject_count_reports_remaining_budget(self):
        interject_service = MagicMock()
        interject_service.get_daily_status.return_value = (3, 10)
        commands, _ = self.make_commands(interject_service=interject_service)
        interaction = make_interaction(channel_id=456, channel_name="chat")

        await commands._handle_interject_count(interaction)

        interject_service.get_daily_status.assert_called_once_with(456)
        sent_message = interaction.followup.send.await_args.args[0]
        self.assertIn("Used today: `3`", sent_message)
        self.assertIn("Remaining: `7`", sent_message)

    async def test_setup_commands_registers_interject_group(self):
        commands, _ = self.make_commands()

        commands.setup_commands()

        commands.bot.tree.add_command.assert_called_once()

    async def test_interject_wrappers_defer_and_schedule_safe_run(self):
        commands, _ = self.make_commands(interject_service=MagicMock())

        with patch("src.bot.commands.interject.safe_run", new=AsyncMock()) as mock_safe_run, patch(
            "src.bot.commands.interject.asyncio.create_task", side_effect=lambda coro: coro.close()
        ) as mock_create_task:
            group = commands._create_interject_group()
            set_cmd = next(cmd for cmd in group.commands if cmd.name == "set")
            reset_cmd = next(cmd for cmd in group.commands if cmd.name == "reset")
            view_cmd = next(cmd for cmd in group.commands if cmd.name == "view")
            count_cmd = next(cmd for cmd in group.commands if cmd.name == "count")

            interaction = make_interaction()
            await set_cmd.callback(interaction, 1, 2, 3, 4, 5, 6, 7, False)
            interaction.response.defer.assert_awaited_with(ephemeral=True, thinking=True)

            interaction = make_interaction()
            await reset_cmd.callback(interaction)

            interaction = make_interaction()
            await view_cmd.callback(interaction)

            interaction = make_interaction()
            await count_cmd.callback(interaction)

        self.assertEqual(mock_create_task.call_count, 4)
        self.assertEqual(mock_safe_run.call_count, 4)
