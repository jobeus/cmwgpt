"""Tests for DiscordBotClient lifecycle and message orchestration."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import src.startup
from src.bot.client import DiscordBotClient


class FakeTextChannel:
    def __init__(self, channel_id=123):
        self.id = channel_id


class TestDiscordBotClient(unittest.IsolatedAsyncioTestCase):
    def make_client(self, *, reply_to_mentions=True, default_model="default-model", guild_id=""):
        client = object.__new__(DiscordBotClient)
        client.config = SimpleNamespace(
            reply_to_mentions=reply_to_mentions,
            default_model=default_model,
            discord_guild_id=guild_id,
        )
        client.mention_handler = MagicMock()
        client.mention_handler.queue_mention = AsyncMock(return_value=True)
        client._ready_once_completed = False
        client.services = SimpleNamespace(
            state_service=MagicMock(),
            queue_service=MagicMock(),
            openai_service=MagicMock(),
            message_service=MagicMock(),
            paste_service=MagicMock(),
            runpod_service=MagicMock(),
            restart_handler=MagicMock(),
            auto_update_service=MagicMock(),
            announcement_service=MagicMock(),
            death_service=MagicMock(),
            memory_store=MagicMock(),
        )
        client.services.memory_store.migrate_from_mariadb = AsyncMock()
        client.services.queue_service.start = AsyncMock()
        client.services.queue_service.stop = AsyncMock()
        client.services.auto_update_service.start = AsyncMock()
        client.services.auto_update_service.stop = AsyncMock()
        client.services.auto_update_service.get_status = MagicMock(
            return_value={"enabled": False, "running": False, "port": 8787}
        )
        client.services.death_service.start = MagicMock()
        client.services.death_service.stop = MagicMock()
        client.services.announcement_service.announce_update = AsyncMock()
        bot_user = SimpleNamespace(id=999)
        client.bot = MagicMock()
        client.bot.user = bot_user
        client.bot.process_commands = AsyncMock()
        client.bot.tree = MagicMock()
        client.bot.tree.sync = AsyncMock()
        client.bot.tree.copy_global_to = MagicMock()
        return client, bot_user

    async def test_handle_message_ignores_bot_authored_messages(self):
        client, bot_user = self.make_client()
        message = SimpleNamespace(
            author=SimpleNamespace(bot=True),
            channel=FakeTextChannel(),
            mentions=[bot_user],
        )

        with patch("src.bot.client.discord.TextChannel", FakeTextChannel):
            await client._handle_message(message)

        client.mention_handler.queue_mention.assert_not_awaited()
        client.bot.process_commands.assert_not_awaited()

    async def test_handle_message_ignores_non_text_channels(self):
        client, bot_user = self.make_client()
        message = SimpleNamespace(
            author=SimpleNamespace(bot=False),
            channel=SimpleNamespace(id=55),
            mentions=[bot_user],
        )

        with patch("src.bot.client.discord.TextChannel", FakeTextChannel):
            await client._handle_message(message)

        client.mention_handler.queue_mention.assert_not_awaited()
        client.bot.process_commands.assert_not_awaited()

    async def test_handle_message_queues_mention_with_channel_model(self):
        client, bot_user = self.make_client()
        client.services.state_service.get_model.return_value = "channel-model"
        channel = FakeTextChannel(channel_id=321)
        message = SimpleNamespace(
            author=SimpleNamespace(bot=False),
            channel=channel,
            mentions=[bot_user],
        )

        with patch("src.bot.client.discord.TextChannel", FakeTextChannel):
            await client._handle_message(message)

        client.mention_handler.queue_mention.assert_awaited_once_with(
            message, bot_user, "channel-model"
        )
        client.bot.process_commands.assert_awaited_once_with(message)

    async def test_handle_message_uses_default_model_fallback(self):
        client, bot_user = self.make_client(default_model="fallback-model")
        client.services.state_service.get_model.return_value = None
        message = SimpleNamespace(
            author=SimpleNamespace(bot=False),
            channel=FakeTextChannel(channel_id=333),
            mentions=[bot_user],
        )

        with patch("src.bot.client.discord.TextChannel", FakeTextChannel):
            await client._handle_message(message)

        queued_model = client.mention_handler.queue_mention.await_args.args[2]
        self.assertEqual(queued_model, "fallback-model")

    async def test_handle_message_replaces_retired_model_with_default(self):
        client, bot_user = self.make_client(default_model="fallback-model")
        client.services.state_service.get_model.return_value = "hybrid"
        message = SimpleNamespace(
            author=SimpleNamespace(bot=False),
            channel=FakeTextChannel(channel_id=334),
            mentions=[bot_user],
        )

        with patch("src.bot.client.discord.TextChannel", FakeTextChannel):
            await client._handle_message(message)

        queued_model = client.mention_handler.queue_mention.await_args.args[2]
        self.assertEqual(queued_model, "fallback-model")

    async def test_handle_message_skips_mention_queue_when_replies_disabled(self):
        client, bot_user = self.make_client(reply_to_mentions=False)
        message = SimpleNamespace(
            author=SimpleNamespace(bot=False),
            channel=FakeTextChannel(channel_id=444),
            mentions=[bot_user],
        )

        with patch("src.bot.client.discord.TextChannel", FakeTextChannel):
            await client._handle_message(message)

        client.mention_handler.queue_mention.assert_not_awaited()
        client.bot.process_commands.assert_awaited_once_with(message)

    async def test_send_update_announcement_skips_without_active_channels(self):
        client, _ = self.make_client()
        client.services.state_service.get_active_channels.return_value = []

        await client._send_update_announcement_if_needed()

        client.services.announcement_service.announce_update.assert_not_awaited()

    async def test_send_update_announcement_uses_manual_restart_flag(self):
        client, _ = self.make_client()
        client.services.state_service.get_active_channels.return_value = [123]
        client._check_for_restart_info = MagicMock(return_value=True)

        with patch("src.bot.client.asyncio.sleep", new=AsyncMock()):
            await client._send_update_announcement_if_needed()

        client.services.announcement_service.announce_update.assert_awaited_once_with(
            was_manual=True
        )

    async def test_send_update_announcement_swallows_errors(self):
        client, _ = self.make_client()
        client.services.state_service.get_active_channels.return_value = [123]
        client.services.announcement_service.announce_update = AsyncMock(side_effect=RuntimeError("boom"))

        with patch("src.bot.client.asyncio.sleep", new=AsyncMock()):
            await client._send_update_announcement_if_needed()

        client.services.announcement_service.announce_update.assert_awaited_once()

    async def test_on_ready_syncs_to_guild_and_starts_services(self):
        client, _ = self.make_client(guild_id="777")
        client._send_update_announcement_if_needed = AsyncMock()
        events = {}
        client.bot.event = lambda fn: events.setdefault(fn.__name__, fn) or fn

        with patch("src.bot.client.discord.Object", side_effect=lambda id: SimpleNamespace(id=id)):
            client._setup_events()
            await events["on_ready"]()

        client.bot.tree.copy_global_to.assert_called_once()
        guild_arg = client.bot.tree.copy_global_to.call_args.kwargs["guild"]
        self.assertEqual(guild_arg.id, 777)
        client.bot.tree.sync.assert_awaited_once_with(guild=guild_arg)
        client.services.queue_service.start.assert_awaited_once_with()
        client.services.auto_update_service.start.assert_awaited_once_with()
        client.services.death_service.start.assert_called_once_with()
        client.services.memory_store.migrate_from_mariadb.assert_awaited_once_with()
        client._send_update_announcement_if_needed.assert_awaited_once_with()

    async def test_on_ready_skips_one_time_setup_on_gateway_reconnect(self):
        client, _ = self.make_client(guild_id="")
        client._send_update_announcement_if_needed = AsyncMock()
        events = {}
        client.bot.event = lambda fn: events.setdefault(fn.__name__, fn) or fn

        client._setup_events()
        await events["on_ready"]()
        await events["on_ready"]()

        client.bot.tree.sync.assert_awaited_once_with()
        client.services.queue_service.start.assert_awaited_once_with()
        client.services.auto_update_service.start.assert_awaited_once_with()
        client.services.death_service.start.assert_called_once_with()
        client._send_update_announcement_if_needed.assert_awaited_once_with()

    async def test_on_ready_syncs_globally_without_guild(self):
        client, _ = self.make_client(guild_id="")
        client._send_update_announcement_if_needed = AsyncMock()
        events = {}
        client.bot.event = lambda fn: events.setdefault(fn.__name__, fn) or fn

        client._setup_events()
        await events["on_ready"]()

        client.bot.tree.copy_global_to.assert_not_called()
        client.bot.tree.sync.assert_awaited_once_with()

    async def test_init_wires_services_state_and_command_setup(self):
        intents = SimpleNamespace(message_content=False, members=False)
        fake_bot = MagicMock()
        fake_bot.user = None
        fake_bot.event = lambda fn: fn
        config = SimpleNamespace(
            default_draw_model="seedream",
            default_edit_model="seedream",
            runpod_io_api_key="runpod-key",
            default_model="gpt-test",
            system_prompt_path="prompt.txt",
            death_channel_id="123",
            discord_guild_id="",
            reply_to_mentions=True,
            discord_bot_token="token",
        )
        services = SimpleNamespace(
            mention_handler=MagicMock(),
            openai_service=MagicMock(),
            state_service=MagicMock(),
            queue_service=MagicMock(),
            restart_handler=MagicMock(),
            auto_update_service=MagicMock(),
            announcement_service=MagicMock(),
            death_service=MagicMock(),
            message_service=MagicMock(),
            paste_service=MagicMock(),
            runpod_service=MagicMock(),
            gemini_service=MagicMock(),
            anthropic_service=MagicMock(),
        )
        services.state_service.load_state_from_temp_files.return_value = False
        services.state_service.get_last_git_sha.return_value = None

        with patch("src.bot.client.discord.Intents.default", return_value=intents), patch(
            "src.bot.client.commands.Bot", return_value=fake_bot
        ), patch.object(DiscordBotClient, "_get_current_git_sha", return_value="abc123"), patch(
            "src.bot.client.ImageCommands"
        ) as mock_image, patch("src.bot.client.SystemCommands") as mock_system, patch(
            "src.bot.client.DeathCommands"
        ) as mock_death:
            client = DiscordBotClient(config=config, services=services)

        self.assertIs(client.bot, fake_bot)
        self.assertTrue(intents.message_content)
        self.assertTrue(intents.members)
        for service in (services.openai_service, services.gemini_service, services.anthropic_service):
            service.set_bot_id_loader.assert_called_once()
            self.assertIsNone(service.set_bot_id_loader.call_args.args[0]())
        fake_bot.user = SimpleNamespace(id=4242)
        self.assertEqual(services.anthropic_service.set_bot_id_loader.call_args.args[0](), 4242)
        services.restart_handler.set_shutdown_callback.assert_called_once_with(client.shutdown)
        self.assertEqual(
            mock_system.call_args.kwargs["on_model_change"], client._clear_model_caches
        )
        services.state_service.load_state_from_temp_files.assert_called_once_with()
        services.state_service.set_last_git_sha.assert_called_once_with("abc123")
        services.auto_update_service.set_restart_callback.assert_called_once_with(
            services.restart_handler.perform_restart
        )
        services.announcement_service.set_bot.assert_called_once_with(fake_bot)
        services.death_service.set_bot.assert_called_once_with(fake_bot)
        mock_image.return_value.setup_commands.assert_called_once_with()
        mock_system.return_value.setup_commands.assert_called_once_with()
        mock_death.return_value.setup_commands.assert_called_once_with()

    async def test_check_for_restart_info_reads_manual_restart_from_state_service(self):
        client, _ = self.make_client()
        client.services.state_service.restart_info = {"manual_restart": True}
        self.assertTrue(client._check_for_restart_info())

        client.services.state_service.restart_info = {"manual_restart": False}
        self.assertFalse(client._check_for_restart_info())

        client.services.state_service.restart_info = {}
        self.assertFalse(client._check_for_restart_info())

    async def test_clear_model_caches_clears_gemini_and_anthropic(self):
        client, _ = self.make_client()
        client.services.gemini_service = MagicMock()
        client.services.anthropic_service = MagicMock()

        client._clear_model_caches(55)

        client.services.gemini_service.clear_channel_cache.assert_called_once_with(55)
        client.services.anthropic_service.clear_channel_cache.assert_called_once_with(55)

        client.services.anthropic_service = None
        client._clear_model_caches(56)
        client.services.gemini_service.clear_channel_cache.assert_called_with(56)

    async def test_run_only_runs_the_bot(self):
        client = object.__new__(DiscordBotClient)
        client.config = SimpleNamespace(discord_bot_token="token")
        client.bot = MagicMock()
        client.services = MagicMock()

        with patch("src.startup.shutdown_services", new=AsyncMock()) as mock_shutdown:
            client.run()

        client.bot.run.assert_called_once_with("token")
        mock_shutdown.assert_not_awaited()

    async def test_shutdown_runs_services_shutdown_then_closes_bot_once(self):
        client, _ = self.make_client()
        client._shutdown_started = False
        client.bot.is_closed.return_value = False
        client.bot.close = AsyncMock()
        order = MagicMock()

        async def fake_shutdown_services(services):
            self.assertIs(services, client.services)
            order("services")

        client.bot.close.side_effect = lambda: order("close")

        with patch("src.startup.shutdown_services", side_effect=fake_shutdown_services) as mock_shutdown:
            await client.shutdown()
            await client.shutdown()

        mock_shutdown.assert_called_once_with(client.services)
        client.bot.close.assert_awaited_once_with()
        self.assertEqual([c.args[0] for c in order.call_args_list], ["services", "close"])

    async def test_shutdown_skips_close_when_bot_already_closed(self):
        client, _ = self.make_client()
        client._shutdown_started = False
        client.bot.is_closed.return_value = True
        client.bot.close = AsyncMock()

        with patch("src.startup.shutdown_services", new=AsyncMock()) as mock_shutdown:
            await client.shutdown()

        mock_shutdown.assert_awaited_once_with(client.services)
        client.bot.close.assert_not_awaited()

    async def test_load_saved_state_and_git_sha_helpers_cover_fresh_and_error_paths(self):
        client = object.__new__(DiscordBotClient)
        client.services = SimpleNamespace(
            state_service=MagicMock(),
            auto_update_service=MagicMock(),
            restart_handler=MagicMock(),
        )

        client.services.state_service.load_state_from_temp_files.return_value = False
        with patch("builtins.print") as mock_print:
            client._load_saved_state()
        mock_print.assert_called_with("🆕 Starting with fresh state")

        client.services.state_service.load_state_from_temp_files.side_effect = RuntimeError("boom")
        with patch("builtins.print") as mock_print:
            client._load_saved_state()
        mock_print.assert_called_with("⚠️  Failed to restore state, starting fresh")

        client.services.state_service.get_last_git_sha.return_value = "existing"
        client._get_current_git_sha = MagicMock(return_value="newsha")
        client._set_current_git_sha()
        client.services.state_service.set_last_git_sha.assert_not_called()

        client.services.state_service.get_last_git_sha.return_value = None
        client._get_current_git_sha = MagicMock(return_value=None)
        client._set_current_git_sha()

        client._get_current_git_sha = MagicMock(side_effect=RuntimeError("boom"))
        client._set_current_git_sha()

    async def test_get_current_git_sha_and_setup_auto_update_error_paths(self):
        client = object.__new__(DiscordBotClient)
        client.services = SimpleNamespace(
            auto_update_service=MagicMock(),
            restart_handler=MagicMock(),
        )

        with patch("src.bot.client.subprocess.run", return_value=SimpleNamespace(returncode=1, stdout="", stderr="fatal")):
            self.assertIsNone(client._get_current_git_sha())

        with patch("src.bot.client.subprocess.run", side_effect=RuntimeError("boom")):
            self.assertIsNone(client._get_current_git_sha())

        client.services.auto_update_service.set_restart_callback.side_effect = RuntimeError("boom")
        client._setup_auto_update()

    async def test_create_bot_delegates_to_startup_factory(self):
        with patch.object(src.startup, "create_bot_client", return_value="bot-client"):
            from src.bot.client import create_bot

            result = create_bot()

        self.assertEqual(result, "bot-client")
