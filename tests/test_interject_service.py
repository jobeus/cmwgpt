"""
Unit tests for the Interject Service.
Tests activity checking, cooldown logic, daily cap, and chance roll.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestInterjectService(unittest.TestCase):
    """Tests for InterjectService."""

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        # Import fresh for each test
        from src.services.interject_service import InterjectService
        self.service = InterjectService(
            state_service=MagicMock(),
            openai_service=MagicMock(),
            message_service=MagicMock(),
            state_file="",
        )

        # Set up a mock bot
        self.mock_bot = MagicMock()
        self.mock_bot.user = MagicMock()
        self.mock_bot.user.id = 99999
        self.service.set_bot(self.mock_bot)
        self.service.start()

    def tearDown(self):
        self.loop.close()

    # ----- Cooldown tests -----

    def test_cooldown_not_set_initially(self):
        """Channel should not be on cooldown initially."""
        self.assertFalse(self.service._is_on_cooldown(12345))

    def test_cooldown_applied(self):
        """After applying cooldown, channel should be on cooldown."""
        self.service._apply_cooldown(12345)
        self.assertTrue(self.service._is_on_cooldown(12345))

    def test_cooldown_expires(self):
        """Cooldown should expire after the configured time."""
        self.service._cooldowns[12345] = time.time() - 1  # Already expired
        self.assertFalse(self.service._is_on_cooldown(12345))

    # ----- Daily cap tests -----

    def test_daily_cap_not_reached_initially(self):
        """Daily cap should not be reached initially."""
        self.assertFalse(self.service._is_daily_cap_reached(12345))

    def test_daily_cap_reached(self):
        """Daily cap should be reached when count hits the limit."""
        today = datetime.now().strftime("%Y-%m-%d")
        self.service._daily_tracker = {"date": today, "counts": {12345: 10}}
        self.assertTrue(self.service._is_daily_cap_reached(12345))

    def test_daily_cap_resets_on_new_day(self):
        """Daily cap should reset when the date changes."""
        self.service._daily_tracker = {"date": "1999-01-01", "counts": {12345: 99}}
        self.assertFalse(self.service._is_daily_cap_reached(12345))

    def test_increment_daily_count(self):
        """Incrementing daily count should increase the counter."""
        today = datetime.now().strftime("%Y-%m-%d")
        self.service._daily_tracker = {"date": today, "counts": {}}
        self.service._increment_daily_count(12345)
        self.assertEqual(self.service._daily_tracker["counts"]["12345"], 1)

    def test_increment_daily_count_new_day(self):
        """Incrementing on a new day should reset and set to 1."""
        self.service._daily_tracker = {"date": "1999-01-01", "counts": {"12345": 99}}
        self.service._increment_daily_count(12345)
        self.assertEqual(self.service._daily_tracker["counts"]["12345"], 1)

    # ----- Chance roll tests -----

    def test_roll_chance_always_passes_at_100(self):
        """Roll should always pass at 100%."""
        for _ in range(50):
            self.assertTrue(self.service._roll_chance(100))

    def test_roll_chance_always_fails_at_0(self):
        """Roll should always fail at 0%."""
        for _ in range(50):
            self.assertFalse(self.service._roll_chance(0))

    # ----- Activity check tests -----

    def _make_mock_message(self, author_id, content, is_bot=False,
                           embeds=None, attachments=None, mentions=None,
                           created_at=None):
        """Helper to create a mock Discord message."""
        msg = MagicMock()
        msg.author = MagicMock()
        msg.author.id = author_id
        msg.author.bot = is_bot
        msg.content = content
        msg.embeds = embeds or []
        msg.attachments = attachments or []
        msg.mentions = mentions or []
        msg.id = id(msg)  # Unique ID
        if created_at is None:
            created_at = datetime.now(timezone.utc)
        msg.created_at = created_at
        return msg

    @patch("src.services.interject_service.MIN_MESSAGES", 3)
    @patch("src.services.interject_service.MIN_UNIQUE_AUTHORS", 2)
    @patch("src.services.interject_service.ACTIVITY_WINDOW_MINUTES", 10)
    @patch("src.services.interject_service.EXCLUDE_EMBEDS", True)
    def test_activity_check_passes_with_qualifying_messages(self):
        """Activity check should pass when all messages qualify."""
        async def run_test():
            now = datetime.now(timezone.utc)
            messages = [
                self._make_mock_message(111, "Hello!", created_at=now),
                self._make_mock_message(222, "Hey there!", created_at=now),
                self._make_mock_message(111, "What's up?", created_at=now),
                self._make_mock_message(222, "Not much!", created_at=now),
            ]

            mock_channel = MagicMock()

            async def mock_history(limit):
                for m in messages[:limit]:
                    yield m

            mock_channel.history = mock_history

            result = await self.service._check_channel_activity(mock_channel, 99999)
            self.assertTrue(result)

        self.loop.run_until_complete(run_test())

    @patch("src.services.interject_service.MIN_MESSAGES", 3)
    @patch("src.services.interject_service.MIN_UNIQUE_AUTHORS", 2)
    @patch("src.services.interject_service.ACTIVITY_WINDOW_MINUTES", 10)
    @patch("src.services.interject_service.EXCLUDE_EMBEDS", True)
    def test_activity_check_fails_with_bot_message_in_streak(self):
        """Activity check should fail when a bot message is in the streak."""
        async def run_test():
            now = datetime.now(timezone.utc)
            messages = [
                self._make_mock_message(111, "Hello!", created_at=now),
                self._make_mock_message(99999, "I'm a bot!", is_bot=True, created_at=now),
                self._make_mock_message(222, "Hey!", created_at=now),
                self._make_mock_message(111, "What's up?", created_at=now),
            ]

            mock_channel = MagicMock()

            async def mock_history(limit):
                for m in messages[:limit]:
                    yield m

            mock_channel.history = mock_history

            result = await self.service._check_channel_activity(mock_channel, 99999)
            self.assertFalse(result)

        self.loop.run_until_complete(run_test())

    @patch("src.services.interject_service.MIN_MESSAGES", 3)
    @patch("src.services.interject_service.MIN_UNIQUE_AUTHORS", 2)
    @patch("src.services.interject_service.ACTIVITY_WINDOW_MINUTES", 10)
    @patch("src.services.interject_service.EXCLUDE_EMBEDS", True)
    def test_activity_check_fails_with_embed_in_streak(self):
        """Activity check should fail when a message has embeds."""
        async def run_test():
            now = datetime.now(timezone.utc)
            embed_msg = self._make_mock_message(
                333, "Check this link", embeds=[MagicMock()], created_at=now
            )
            messages = [
                self._make_mock_message(111, "Hello!", created_at=now),
                embed_msg,
                self._make_mock_message(222, "Hey!", created_at=now),
                self._make_mock_message(111, "What's up?", created_at=now),
            ]

            mock_channel = MagicMock()

            async def mock_history(limit):
                for m in messages[:limit]:
                    yield m

            mock_channel.history = mock_history

            result = await self.service._check_channel_activity(mock_channel, 99999)
            self.assertFalse(result)

        self.loop.run_until_complete(run_test())

    @patch("src.services.interject_service.MIN_MESSAGES", 3)
    @patch("src.services.interject_service.MIN_UNIQUE_AUTHORS", 2)
    @patch("src.services.interject_service.ACTIVITY_WINDOW_MINUTES", 10)
    @patch("src.services.interject_service.EXCLUDE_EMBEDS", True)
    def test_activity_check_fails_with_bot_mention_in_streak(self):
        """Activity check should fail when a message mentions the bot."""
        async def run_test():
            now = datetime.now(timezone.utc)
            bot_mention_user = MagicMock()
            bot_mention_user.id = 99999
            mention_msg = self._make_mock_message(
                222, "Hey <@99999> help", mentions=[bot_mention_user], created_at=now
            )
            messages = [
                self._make_mock_message(111, "Hello!", created_at=now),
                mention_msg,
                self._make_mock_message(222, "Hey!", created_at=now),
                self._make_mock_message(111, "What's up?", created_at=now),
            ]

            mock_channel = MagicMock()

            async def mock_history(limit):
                for m in messages[:limit]:
                    yield m

            mock_channel.history = mock_history

            result = await self.service._check_channel_activity(mock_channel, 99999)
            self.assertFalse(result)

        self.loop.run_until_complete(run_test())

    @patch("src.services.interject_service.MIN_MESSAGES", 3)
    @patch("src.services.interject_service.MIN_UNIQUE_AUTHORS", 2)
    @patch("src.services.interject_service.ACTIVITY_WINDOW_MINUTES", 10)
    @patch("src.services.interject_service.EXCLUDE_EMBEDS", True)
    def test_activity_check_fails_with_single_author(self):
        """Activity check should fail when only one author is present."""
        async def run_test():
            now = datetime.now(timezone.utc)
            messages = [
                self._make_mock_message(111, "Hello!", created_at=now),
                self._make_mock_message(111, "Anyone here?", created_at=now),
                self._make_mock_message(111, "Guess not", created_at=now),
                self._make_mock_message(111, "Oh well", created_at=now),
            ]

            mock_channel = MagicMock()

            async def mock_history(limit):
                for m in messages[:limit]:
                    yield m

            mock_channel.history = mock_history

            result = await self.service._check_channel_activity(mock_channel, 99999)
            self.assertFalse(result)

        self.loop.run_until_complete(run_test())

    @patch("src.services.interject_service.MIN_MESSAGES", 5)
    @patch("src.services.interject_service.MIN_UNIQUE_AUTHORS", 2)
    @patch("src.services.interject_service.ACTIVITY_WINDOW_MINUTES", 10)
    @patch("src.services.interject_service.EXCLUDE_EMBEDS", True)
    def test_activity_check_fails_with_too_few_messages(self):
        """Activity check should fail when there aren't enough messages."""
        async def run_test():
            now = datetime.now(timezone.utc)
            messages = [
                self._make_mock_message(111, "Hello!", created_at=now),
                self._make_mock_message(222, "Hey!", created_at=now),
                self._make_mock_message(111, "How goes?", created_at=now),
            ]

            mock_channel = MagicMock()

            async def mock_history(limit):
                for m in messages[:limit]:
                    yield m

            mock_channel.history = mock_history

            result = await self.service._check_channel_activity(mock_channel, 99999)
            self.assertFalse(result)

        self.loop.run_until_complete(run_test())

    @patch("src.services.interject_service.MIN_MESSAGES", 3)
    @patch("src.services.interject_service.MIN_UNIQUE_AUTHORS", 2)
    @patch("src.services.interject_service.ACTIVITY_WINDOW_MINUTES", 10)
    @patch("src.services.interject_service.EXCLUDE_EMBEDS", False)
    def test_activity_check_passes_with_embeds_when_exclude_disabled(self):
        """Activity check should pass with embeds when EXCLUDE_EMBEDS is False."""
        async def run_test():
            now = datetime.now(timezone.utc)
            messages = [
                self._make_mock_message(111, "Hello!", embeds=[MagicMock()], created_at=now),
                self._make_mock_message(222, "Hey there!", created_at=now),
                self._make_mock_message(111, "What's up?", created_at=now),
                self._make_mock_message(222, "Not much!", created_at=now),
            ]

            mock_channel = MagicMock()

            async def mock_history(limit):
                for m in messages[:limit]:
                    yield m

            mock_channel.history = mock_history

            result = await self.service._check_channel_activity(mock_channel, 99999)
            self.assertTrue(result)

        self.loop.run_until_complete(run_test())

    # ----- on_new_message bail-out tests -----

    def test_on_new_message_skips_bot_messages(self):
        """on_new_message should skip messages from bots."""
        async def run_test():
            msg = self._make_mock_message(111, "Hello!", is_bot=True)
            msg.channel = MagicMock(spec=["history"])

            with patch.object(self.service, '_check_channel_activity') as mock_check:
                await self.service.on_new_message(msg)
                mock_check.assert_not_called()

        self.loop.run_until_complete(run_test())

    def test_on_new_message_skips_dms(self):
        """on_new_message should skip direct messages."""
        async def run_test():
            msg = self._make_mock_message(111, "Hello!")
            msg.channel = MagicMock()  # Not a TextChannel

            with patch.object(self.service, '_check_channel_activity') as mock_check:
                await self.service.on_new_message(msg)
                mock_check.assert_not_called()

        self.loop.run_until_complete(run_test())

    def test_on_new_message_skips_bot_mentions(self):
        """on_new_message should skip messages that mention the bot."""
        async def run_test():
            import discord
            msg = self._make_mock_message(111, "Hey <@99999>", mentions=[self.mock_bot.user])
            msg.channel = MagicMock(spec=discord.TextChannel)

            with patch.object(self.service, '_check_channel_activity') as mock_check:
                await self.service.on_new_message(msg)
                mock_check.assert_not_called()

        self.loop.run_until_complete(run_test())

    def test_on_new_message_skips_when_on_cooldown(self):
        """on_new_message should skip when channel is on cooldown."""
        async def run_test():
            import discord
            msg = self._make_mock_message(111, "Hello!")
            msg.channel = MagicMock(spec=discord.TextChannel)
            msg.channel.id = 12345

            self.service._apply_cooldown(12345)

            with patch.object(self.service, '_check_channel_activity') as mock_check:
                await self.service.on_new_message(msg)
                mock_check.assert_not_called()

        self.loop.run_until_complete(run_test())

    def test_on_new_message_skips_when_not_running(self):
        """on_new_message should skip when service is stopped."""
        async def run_test():
            self.service.stop()
            msg = self._make_mock_message(111, "Hello!")

            with patch.object(self.service, '_check_channel_activity') as mock_check:
                await self.service.on_new_message(msg)
                mock_check.assert_not_called()

        self.loop.run_until_complete(run_test())

    def test_get_setting_daily_status_and_state_persistence_helpers(self):
        self.service._state_service.get_interject_settings.return_value = {"daily_max": 7, "cooldown": 3}
        self.assertEqual(self.service._get_setting(12345, "daily_max", 10), 7)
        self.assertEqual(self.service._get_setting(12345, "missing", 10), 10)

        today = datetime.now().strftime("%Y-%m-%d")
        self.service._daily_tracker = {"date": today, "counts": {"12345": 2}}
        self.assertEqual(self.service.get_daily_status(12345), (2, 7))

        self.service._daily_tracker = {"date": "1999-01-01", "counts": {"12345": 99}}
        self.assertEqual(self.service.get_daily_status(12345), (0, 7))

    def test_save_and_load_state_error_paths(self):
        self.service._daily_tracker = {"date": "2024-01-01", "counts": {"123": 1}}
        with patch("builtins.open", side_effect=OSError("denied")):
            self.service._save_state()

        with patch("os.path.exists", return_value=True), patch(
            "builtins.open", side_effect=ValueError("bad")
        ):
            self.service._load_state()

    def test_on_new_message_skips_when_daily_cap_or_lock_or_activity_fail(self):
        async def run_test():
            import discord

            msg = self._make_mock_message(111, "Hello!")
            msg.channel = MagicMock(spec=discord.TextChannel)
            msg.channel.id = 12345
            msg.channel.name = "general"
            msg.mentions = []

            with patch.object(self.service, "_is_daily_cap_reached", return_value=True), patch.object(
                self.service, "_check_channel_activity", new=AsyncMock()
            ) as mock_check:
                await self.service.on_new_message(msg)
                mock_check.assert_not_called()

            with patch.object(self.service._lock, "locked", return_value=True), patch.object(
                self.service, "_check_channel_activity", new=AsyncMock()
            ) as mock_check:
                await self.service.on_new_message(msg)
                mock_check.assert_not_called()

            with patch.object(self.service, "_check_channel_activity", new=AsyncMock(return_value=False)), patch.object(
                self.service, "_roll_chance"
            ) as mock_roll:
                await self.service.on_new_message(msg)
                mock_roll.assert_not_called()

        self.loop.run_until_complete(run_test())

    def test_on_new_message_applies_cooldown_on_failed_roll_and_handles_errors(self):
        async def run_test():
            import discord

            msg = self._make_mock_message(111, "Hello!")
            msg.channel = MagicMock(spec=discord.TextChannel)
            msg.channel.id = 12345
            msg.channel.name = "general"
            msg.mentions = []

            with patch.object(self.service, "_check_channel_activity", new=AsyncMock(return_value=True)), patch.object(
                self.service, "_roll_chance", return_value=False
            ), patch.object(self.service, "_apply_cooldown") as mock_cooldown:
                await self.service.on_new_message(msg)
                mock_cooldown.assert_called_once_with(12345)

            with patch.object(self.service, "_check_channel_activity", new=AsyncMock(side_effect=RuntimeError("boom"))):
                await self.service.on_new_message(msg)

        self.loop.run_until_complete(run_test())

    def test_do_interject_handles_reply_formats_none_dict_and_failures(self):
        async def run_test():
            self.service._state_service.get_system_prompt.return_value = "channel prompt"
            self.service._state_service.get_model.return_value = "chosen-model"
            self.service._message_service.send_channel_reply = AsyncMock()

            channel = MagicMock()
            channel.id = 123
            channel.name = "general"

            ref_author = SimpleNamespace(id=222)
            ref_msg = SimpleNamespace(
                content="earlier text",
                created_at=MagicMock(),
                author=ref_author,
            )
            ref_msg.created_at.astimezone.return_value.strftime.return_value = "2024-01-01 00:00:00"

            msg1 = MagicMock()
            msg1.author.id = 99999
            msg1.content = "[$1.23] bot said hi"
            msg1.reference = None
            msg1.id = 1
            msg1.created_at.astimezone.return_value.strftime.return_value = "2024-01-01 00:00:01"

            msg2 = MagicMock()
            msg2.author.id = 123
            msg2.content = "replying"
            msg2.id = 2
            msg2.reference = SimpleNamespace(message_id=55, resolved=ref_msg, cached_message=None)
            msg2.created_at.astimezone.return_value.strftime.return_value = "2024-01-01 00:00:02"

            msg3 = MagicMock()
            msg3.author.id = 456
            msg3.content = "fallback"
            msg3.id = 55
            msg3.reference = None
            msg3.created_at.astimezone.return_value.strftime.return_value = "2024-01-01 00:00:03"

            async def history(limit):
                for item in [msg2, msg3, msg1]:
                    yield item

            channel.history = history
            self.mock_bot.user = SimpleNamespace(id=99999)

            self.service._openai_service.get_chat_completion = AsyncMock(side_effect=[(None, 0.0), ({"text": "dict reply"}, 0.0), RuntimeError("boom")])
            self.service._mention_legend_provider = AsyncMock(return_value=("legend text", 0.0))

            await self.service._do_interject(channel, 99999)
            self.service._message_service.send_channel_reply.assert_not_awaited()

            await self.service._do_interject(channel, 99999)
            self.service._message_service.send_channel_reply.assert_awaited_once_with(channel, "dict reply")
            self.assertIn(123, self.service._cooldowns)
            self.assertEqual(self.service._daily_tracker["counts"]["123"], 1)

            await self.service._do_interject(channel, 99999)

            call = self.service._openai_service.get_chat_completion.await_args_list[1]
            self.assertEqual(call.kwargs["model"], "chosen-model")
            self.assertIn("legend text", call.kwargs["system_prompt"])
            messages = call.kwargs["messages"]
            self.assertIn("bot said hi", messages[0]["content"][0]["text"])
            self.assertIn("Replying to message", messages[2]["content"][0]["text"])

        self.loop.run_until_complete(run_test())

    def test_do_interject_with_audio_attachment(self):
        async def run_test():
            self.service._state_service.get_system_prompt.return_value = "channel prompt"
            self.service._state_service.get_model.return_value = "chosen-model"
            self.service._message_service.send_channel_reply = AsyncMock()

            channel = MagicMock()
            channel.id = 123
            channel.name = "general"

            audio_attach = MagicMock()
            audio_attach.content_type = "audio/ogg"
            audio_attach.filename = "voice_interject.ogg"
            audio_attach.duration = 5.0
            audio_attach.is_voice_message = MagicMock(return_value=True)

            msg = MagicMock()
            msg.author.id = 123
            msg.content = "Check this voice note"
            msg.id = 1
            msg.reference = None
            msg.embeds = []
            msg.attachments = [audio_attach]
            msg.created_at.astimezone.return_value.strftime.return_value = "2024-01-01 00:00:00"

            async def history(limit):
                yield msg

            channel.history = history
            self.mock_bot.user = SimpleNamespace(id=99999)

            self.service._openai_service.get_chat_completion = AsyncMock(return_value=("reply", 0.0))
            self.service._mention_legend_provider = AsyncMock(return_value=("legend text", 0.0))

            with patch("src.services.interject_service.attachment_to_base64_data_url", AsyncMock(return_value="data:audio/ogg;base64,AUDIO_DATA")):
                await self.service._do_interject(channel, 99999)

            self.service._message_service.send_channel_reply.assert_awaited_once_with(channel, "reply")
            
            call = self.service._openai_service.get_chat_completion.await_args
            messages = call.kwargs["messages"]
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0]["content"][1]["type"], "audio_url")
            self.assertEqual(messages[0]["content"][1]["audio_url"]["url"], "data:audio/ogg;base64,AUDIO_DATA")
            self.assertIn("[Sent an audio message/voice clip (5.0s): voice_interject.ogg]", messages[0]["content"][0]["text"])

        self.loop.run_until_complete(run_test())


if __name__ == "__main__":
    unittest.main()
