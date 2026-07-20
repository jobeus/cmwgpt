"""Tests for DeathCommands handlers and channel authorization."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord.ext.commands  # noqa: F401

from src.bot.commands.death import DeathCommands


def make_interaction(channel_id=123, channel_name="deaths"):
    interaction = MagicMock()
    interaction.channel = MagicMock()
    interaction.channel.id = channel_id
    interaction.channel.name = channel_name
    interaction.channel_id = channel_id
    interaction.followup = AsyncMock()
    interaction.response = AsyncMock()
    return interaction


class TestDeathCommands(unittest.IsolatedAsyncioTestCase):
    def make_commands(self, *, current_settings=None, death_channel_id="123", death_service=None):
        state_service = MagicMock()
        state_service.get_death_settings.return_value = current_settings
        commands = DeathCommands(
            MagicMock(),
            state_service_instance=state_service,
            death_channel_id=death_channel_id,
            death_service_instance=death_service,
        )
        return commands, state_service

    async def test_handle_death_set_rejects_unauthorized_channel(self):
        commands, state_service = self.make_commands(death_channel_id="999")
        interaction = make_interaction(channel_id=123)

        await commands._handle_death_set(interaction, 30, 1000, 12)

        interaction.followup.send.assert_awaited_once_with(
            "This command can only be used in the designated death announcements channel.", ephemeral=True
        )
        state_service.mark_channel_active.assert_not_called()

    async def test_handle_death_set_requires_at_least_one_setting(self):
        commands, state_service = self.make_commands(current_settings={}, death_channel_id="123")
        interaction = make_interaction(channel_id=123)

        await commands._handle_death_set(interaction, None, None, None)

        state_service.mark_channel_active.assert_called_once_with(123)
        interaction.followup.send.assert_awaited_once_with(
            "You must provide at least one setting to change.", ephemeral=True
        )

    async def test_handle_death_set_updates_global_settings(self):
        commands, state_service = self.make_commands(current_settings={"interval": 15}, death_channel_id="123")
        interaction = make_interaction(channel_id=123)

        await commands._handle_death_set(interaction, 30, 200000, 6)

        state_service.set_death_settings.assert_called_once_with(
            {"interval": 30, "min_views": 200000, "pageview_months": 6}
        )
        sent_message = interaction.followup.send.await_args.args[0]
        self.assertIn("Poll interval: `30s`", sent_message)
        self.assertIn("Min views: `200,000`", sent_message)

    async def test_handle_death_reset_clears_settings(self):
        commands, state_service = self.make_commands(death_channel_id="123")
        interaction = make_interaction(channel_id=123)

        await commands._handle_death_reset(interaction)

        state_service.mark_channel_active.assert_called_once_with(123)
        state_service.clear_death_settings.assert_called_once_with()
        interaction.followup.send.assert_awaited_once_with(
            "Death settings have been reset to defaults.", ephemeral=True
        )

    async def test_handle_death_view_shows_custom_and_default_values(self):
        commands, _ = self.make_commands(current_settings={"interval": 45}, death_channel_id="123")
        interaction = make_interaction(channel_id=123)

        await commands._handle_death_view(interaction)

        sent_message = interaction.followup.send.await_args.args[0]
        self.assertIn("Current Death Settings", sent_message)
        self.assertIn("Poll interval: `45s` (custom)", sent_message)
        self.assertIn("(default)", sent_message)

    async def test_check_auth_fails_when_no_death_channel_is_configured(self):
        commands, _ = self.make_commands(death_channel_id="")

        self.assertFalse(commands._check_auth(make_interaction(channel_id=123)))

    async def test_handle_death_reset_rejects_unauthorized_channel(self):
        commands, state_service = self.make_commands(death_channel_id="999")
        interaction = make_interaction(channel_id=123)

        await commands._handle_death_reset(interaction)

        interaction.followup.send.assert_awaited_once_with(
            "This command can only be used in the designated death announcements channel.", ephemeral=True
        )
        state_service.clear_death_settings.assert_not_called()

    async def test_handle_death_view_rejects_unauthorized_channel(self):
        commands, state_service = self.make_commands(death_channel_id="999")
        interaction = make_interaction(channel_id=123)

        await commands._handle_death_view(interaction)

        interaction.followup.send.assert_awaited_once_with(
            "This command can only be used in the designated death announcements channel.", ephemeral=True
        )
        state_service.get_death_settings.assert_not_called()

    async def test_setup_commands_registers_death_group(self):
        commands, _ = self.make_commands(death_channel_id="123")

        commands.setup_commands()

        commands.bot.tree.add_command.assert_called_once()

    async def test_handle_forcedeath_invokes_service(self):
        death_service = MagicMock()
        death_service.force_announce = AsyncMock(return_value=(True, "Forced death announcement for **Kevin Keegan**."))
        commands, _ = self.make_commands(death_channel_id="123", death_service=death_service)
        interaction = make_interaction(channel_id=123)

        await commands._handle_forcedeath(interaction, "https://en.wikipedia.org/wiki/Kevin_Keegan")

        death_service.force_announce.assert_awaited_once_with("https://en.wikipedia.org/wiki/Kevin_Keegan")
        interaction.followup.send.assert_awaited_once_with(
            "Forced death announcement for **Kevin Keegan**.", ephemeral=True
        )

    async def test_handle_forcedeath_reports_failure(self):
        death_service = MagicMock()
        death_service.force_announce = AsyncMock(return_value=(False, "Could not determine a Wikipedia article title from that input."))
        commands, _ = self.make_commands(death_channel_id="123", death_service=death_service)
        interaction = make_interaction(channel_id=123)

        await commands._handle_forcedeath(interaction, "garbage")

        sent = interaction.followup.send.await_args.args[0]
        self.assertTrue(sent.startswith("❌"))

    async def test_handle_limerick_invokes_service(self):
        death_service = MagicMock()
        death_service.write_limerick = AsyncMock(
            return_value=(True, "There once was a keeper named Kev...")
        )
        commands, _ = self.make_commands(death_channel_id="123", death_service=death_service)
        interaction = make_interaction(channel_id=123)

        await commands._handle_limerick(interaction, "Kevin Keegan")

        death_service.write_limerick.assert_awaited_once_with("Kevin Keegan")
        interaction.followup.send.assert_awaited_once_with(
            content="There once was a keeper named Kev...", ephemeral=False
        )

    async def test_handle_limerick_reports_failure(self):
        death_service = MagicMock()
        death_service.write_limerick = AsyncMock(
            return_value=(False, "Could not determine a person from that input.")
        )
        commands, _ = self.make_commands(death_channel_id="123", death_service=death_service)
        interaction = make_interaction(channel_id=123)

        await commands._handle_limerick(interaction, "")

        sent = interaction.followup.send.await_args.kwargs["content"]
        self.assertTrue(sent.startswith("❌"))

    async def test_handle_limerick_without_service(self):
        commands, _ = self.make_commands(death_channel_id="123", death_service=None)
        interaction = make_interaction(channel_id=123)

        await commands._handle_limerick(interaction, "Kevin Keegan")

        interaction.followup.send.assert_awaited_once_with(
            content="The limerick service is not available.", ephemeral=False
        )

    async def test_limerick_command_defers_publicly_for_any_user(self):
        death_service = MagicMock()
        death_service.write_limerick = AsyncMock(return_value=(True, "verse"))
        commands, _ = self.make_commands(death_channel_id="123", death_service=death_service)

        registered = {}
        commands.bot.tree.command = lambda **kwargs: (lambda fn: registered.setdefault(kwargs["name"], fn) or fn)

        commands._create_limerick_command()
        limerick = registered["limerick"]

        interaction = make_interaction(channel_id=123)
        interaction.user.guild_permissions.administrator = False

        with patch("src.bot.commands.death.safe_run", new=AsyncMock()), patch(
            "src.bot.commands.death.asyncio.create_task", side_effect=lambda coro: coro.close()
        ):
            await limerick(interaction, "Kevin Keegan")

        interaction.response.defer.assert_awaited_once_with(ephemeral=False, thinking=True)

    async def test_forcedeath_command_rejects_non_admin(self):
        death_service = MagicMock()
        death_service.force_announce = AsyncMock()
        commands, _ = self.make_commands(death_channel_id="123", death_service=death_service)

        registered = {}
        commands.bot.tree.command = lambda **kwargs: (lambda fn: registered.setdefault(kwargs["name"], fn) or fn)

        commands._create_forcedeath_command()
        forcedeath = registered["forcedeath"]

        interaction = make_interaction(channel_id=123)
        interaction.user.guild_permissions.administrator = False

        await forcedeath(interaction, "https://en.wikipedia.org/wiki/Kevin_Keegan")

        interaction.response.send_message.assert_awaited_once_with(
            "❌ You need administrator permissions to use this command.", ephemeral=True
        )
        death_service.force_announce.assert_not_awaited()

    async def test_death_wrappers_defer_and_schedule_safe_run(self):
        commands, _ = self.make_commands(death_channel_id="123")

        with patch("src.bot.commands.death.safe_run", new=AsyncMock()) as mock_safe_run, patch(
            "src.bot.commands.death.asyncio.create_task", side_effect=lambda coro: coro.close()
        ) as mock_create_task:
            group = commands._create_death_group()
            set_cmd = next(cmd for cmd in group.commands if cmd.name == "set")
            reset_cmd = next(cmd for cmd in group.commands if cmd.name == "reset")
            view_cmd = next(cmd for cmd in group.commands if cmd.name == "view")

            interaction = make_interaction(channel_id=123)
            await set_cmd.callback(interaction, 30, 200000, 6)

            interaction = make_interaction(channel_id=123)
            await reset_cmd.callback(interaction)

            interaction = make_interaction(channel_id=123)
            await view_cmd.callback(interaction)

        self.assertEqual(mock_safe_run.call_count, 3)
        self.assertEqual(mock_create_task.call_count, 3)
