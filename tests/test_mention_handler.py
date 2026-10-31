"""Tests for MentionHandler context building and queue delegation."""

import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from src.services.openai_service import OpenAIServiceError
from src.bot.handlers.mention import MentionHandler
from src.services.cost_service import OPENROUTER, record_cost


class AsyncTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TestMentionHandler(unittest.IsolatedAsyncioTestCase):
    def make_handler(self, *, attachment_converter=None, url_converter=None, url_content_fetcher=None,
                     gemini_service=None, anthropic_service=None):
        queue_service = MagicMock()
        queue_service.queue_mention = AsyncMock(return_value=True)
        state_service = MagicMock()
        state_service.get_system_prompt.return_value = None
        openai_service = MagicMock()
        message_service = MagicMock()
        message_service.send_channel_reply = AsyncMock()
        message_service.send_channel_reply_with_files = AsyncMock()
        handler = MentionHandler(
            state_service=state_service,
            openai_service=openai_service,
            message_service=message_service,
            queue_service=queue_service,
            system_prompt_loader=lambda: "base system prompt",
            include_num_chatlines=10,
            mention_legend_provider=AsyncMock(return_value=("legend text", 0.0)),
            attachment_converter=attachment_converter or AsyncMock(return_value="data:file/plain;base64,AAA"),
            url_converter=url_converter or AsyncMock(return_value="data:image/png;base64,BBB"),
            url_content_fetcher=url_content_fetcher or AsyncMock(return_value=("", [])),
            gemini_service=gemini_service,
            anthropic_service=anthropic_service,
        )
        return handler, state_service, queue_service, openai_service, message_service

    async def test_queue_mention_delegates_to_queue_service(self):
        handler, _, queue_service, _, _ = self.make_handler()
        message = MagicMock()
        bot_user = SimpleNamespace(id=999)

        result = await handler.queue_mention(message, bot_user, "gpt-test")

        self.assertTrue(result)
        queue_service.queue_mention.assert_awaited_once_with(
            message=message,
            bot_user=bot_user,
            model="gpt-test",
            handler=handler.handle_mention,
        )

    async def test_prepare_context_handles_reply_urls_and_assistant_image_split(self):
        async def url_content_fetcher(text, *, cache_only):
            return ("[fetched-url]\n", []) if "https://site.example" in text else ("", [])

        url_converter = AsyncMock(return_value="data:image/png;base64,IMG")
        handler, _, _, _, _ = self.make_handler(
            url_converter=url_converter,
            url_content_fetcher=AsyncMock(side_effect=url_content_fetcher),
        )

        created_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        channel = MagicMock()
        channel.id = 123
        channel.name = "general"

        replied_msg = SimpleNamespace(
            author=SimpleNamespace(id=111),
            id=1,
            content="earlier text",
            channel=channel,
            reference=None,
            attachments=[],
            embeds=[],
            created_at=created_at,
        )
        assistant_embed = SimpleNamespace(
            title="preview",
            description=None,
            url=None,
            image=SimpleNamespace(url="https://img.example/preview.png", proxy_url=None),
            thumbnail=None,
        )
        bot_user = SimpleNamespace(id=999)
        assistant_msg = SimpleNamespace(
            author=SimpleNamespace(id=999),
            id=2,
            content="[$0.010 @ qwen] image reply",
            channel=channel,
            reference=None,
            attachments=[],
            embeds=[assistant_embed],
            created_at=created_at,
        )
        mention_msg = SimpleNamespace(
            author=SimpleNamespace(id=222),
            id=3,
            content="look at https://site.example",
            channel=channel,
            reference=SimpleNamespace(message_id=1, resolved=None, cached_message=None),
            attachments=[],
            embeds=[],
            created_at=created_at,
        )

        async def history(limit=None, after=None):
            for msg in [mention_msg, assistant_msg, replied_msg]:
                yield msg

        channel.history = history

        with patch.dict("os.environ", {"TZ": "America/Denver"}, clear=False):
            context, system_prompt, _ = await handler._prepare_mention_context(mention_msg, bot_user)

        self.assertEqual(len(context), 4)
        self.assertEqual(context[1]["role"], "assistant")
        self.assertEqual(context[2]["role"], "user")
        self.assertEqual(context[2]["content"][1]["type"], "image_url")
        self.assertIn("[2024-01-01 05:00:00] [3] <@222>:", context[-1]["content"][0]["text"])
        self.assertIn(
            '[Replying to message: "[2024-01-01 05:00:00] [1] <@111>: earlier text"]',
            context[-1]["content"][0]["text"],
        )
        self.assertIn("earlier text", context[-1]["content"][0]["text"])
        self.assertIn("[fetched-url]", context[-1]["content"][0]["text"])
        self.assertIn("legend text", system_prompt)
        url_converter.assert_awaited_once_with("https://img.example/preview.png")

    async def test_prepare_context_swallow_attachment_conversion_failures(self):
        handler, _, _, _, _ = self.make_handler(
            attachment_converter=AsyncMock(side_effect=Exception("bad attachment")),
        )
        created_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        channel = MagicMock()
        channel.id = 555
        channel.name = "uploads"
        bot_user = SimpleNamespace(id=999)
        attachment = SimpleNamespace(content_type="image/png")
        mention_msg = SimpleNamespace(
            author=SimpleNamespace(id=123),
            id=10,
            content="here is a file",
            channel=channel,
            reference=None,
            attachments=[attachment],
            embeds=[],
            created_at=created_at,
        )

        async def history(limit=None, after=None):
            yield mention_msg

        channel.history = history

        context, _, _ = await handler._prepare_mention_context(mention_msg, bot_user)

        self.assertEqual(len(context), 1)
        self.assertEqual(context[0]["role"], "user")
        self.assertEqual(len(context[0]["content"]), 1)

    async def test_prepare_context_marks_empty_messages_and_non_image_files(self):
        handler, _, _, _, _ = self.make_handler(
            attachment_converter=AsyncMock(return_value="data:file/plain;base64,FILE"),
            url_converter=AsyncMock(return_value="data:image/png;base64,THUMB"),
        )
        created_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        channel = MagicMock()
        channel.id = 777
        channel.name = "mixed"
        bot_user = SimpleNamespace(id=999)
        attachment = SimpleNamespace(content_type="text/plain", filename="test.txt")
        embed = SimpleNamespace(
            title="embed title",
            description="embed desc",
            url="https://embed.example",
            image=None,
            thumbnail=SimpleNamespace(url="https://img.example/thumb.png", proxy_url=None),
        )
        mention_msg = SimpleNamespace(
            author=SimpleNamespace(id=123),
            id=10,
            content="",
            channel=channel,
            reference=None,
            attachments=[attachment],
            embeds=[embed],
            created_at=created_at,
        )

        async def history(limit=None, after=None):
            yield mention_msg

        channel.history = history

        context, _, _ = await handler._prepare_mention_context(mention_msg, bot_user)

        self.assertIn("[Embeds:", context[0]["content"][0]["text"])
        self.assertIn("[Attached file:", context[0]["content"][0]["text"])
        self.assertEqual(context[0]["content"][1]["type"], "image_url")

    async def test_prepare_context_uses_cached_history_custom_prompt_and_image_attachments(self):
        handler, state_service, _, _, _ = self.make_handler(
            attachment_converter=AsyncMock(return_value="data:image/png;base64,IMG")
        )
        state_service.get_system_prompt.return_value = "channel prompt"
        created_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        channel = MagicMock()
        channel.id = 888
        channel.name = "cached"
        bot_user = SimpleNamespace(id=999)
        image_attachment = SimpleNamespace(content_type="image/png")
        older_msg = SimpleNamespace(
            author=SimpleNamespace(id=111),
            id=1,
            content="older",
            channel=channel,
            reference=None,
            attachments=[],
            embeds=[],
            created_at=created_at,
        )
        mention_msg = SimpleNamespace(
            author=SimpleNamespace(id=123),
            id=2,
            content="newer",
            channel=channel,
            reference=None,
            attachments=[image_attachment],
            embeds=[],
            created_at=created_at,
        )
        history_calls = []

        async def first_history(limit=None, after=None):
            history_calls.append((limit, after))
            for msg in [mention_msg, older_msg]:
                yield msg

        async def second_history(limit=None, after=None):
            history_calls.append((limit, after))
            yield mention_msg

        channel.history = first_history

        # Patch the mention module's reference to the time module (not
        # time.time globally, which logging handlers may also call).
        with patch("src.bot.handlers.mention.time", MagicMock(time=MagicMock(side_effect=[1000, 1005]))):
            context, system_prompt, _ = await handler._prepare_mention_context(mention_msg, bot_user)
            channel.history = second_history
            cached_context, _, _ = await handler._prepare_mention_context(mention_msg, bot_user)

        self.assertTrue(system_prompt.startswith("channel prompt\n\n## How this chat reaches you\nYou are <@"))
        self.assertIn("## How you work (your own architecture)", system_prompt)
        self.assertEqual(context[-1]["content"][1]["type"], "image_url")
        self.assertEqual(history_calls[0], (10, None))
        self.assertEqual(history_calls[1], (100, older_msg))
        self.assertEqual(handler._history_cache[channel.id]["timestamp"], 1005)
        self.assertIn("newer", cached_context[-1]["content"][0]["text"])

    async def test_prepare_context_with_audio_attachments(self):
        handler, _, _, _, _ = self.make_handler(
            attachment_converter=AsyncMock(return_value="data:audio/ogg;base64,AUDIO")
        )
        created_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        channel = MagicMock()
        channel.id = 890
        channel.name = "audio-channel"
        bot_user = SimpleNamespace(id=999)
        
        audio_attachment = MagicMock()
        audio_attachment.content_type = "audio/ogg"
        audio_attachment.filename = "voice.ogg"
        audio_attachment.duration = 4.5
        audio_attachment.is_voice_message = MagicMock(return_value=True)

        mention_msg = SimpleNamespace(
            author=SimpleNamespace(id=123),
            id=12,
            content="here is a voice message",
            channel=channel,
            reference=None,
            attachments=[audio_attachment],
            embeds=[],
            created_at=created_at,
        )

        async def history(limit=None, after=None):
            yield mention_msg

        channel.history = history

        with patch("src.bot.handlers.mention.transcribe_audio_attachment", AsyncMock(return_value="Mocked Groq Transcript")):
            context, _, _ = await handler._prepare_mention_context(mention_msg, bot_user)

        self.assertEqual(len(context), 1)
        self.assertEqual(context[0]["role"], "user")
        
        # Checking that the content has only the text payload, no audio_url payload
        payload = context[0]["content"]
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["type"], "text")
        
        # Checking that the text payload has the placeholder text for the audio message and the transcript
        self.assertIn("[Sent an audio message/voice clip (4.5s): voice.ogg\nTranscript: Mocked Groq Transcript]", payload[0]["text"])

    async def test_prepare_context_marks_truly_empty_messages_and_swallow_embed_preview_failures(self):
        handler, _, _, _, _ = self.make_handler(
            url_converter=AsyncMock(side_effect=RuntimeError("preview failed")),
        )
        created_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        channel = MagicMock()
        channel.id = 889
        channel.name = "empty"
        bot_user = SimpleNamespace(id=999)
        embed = SimpleNamespace(
            title="preview",
            description=None,
            url=None,
            image=SimpleNamespace(url="https://img.example/preview.png", proxy_url=None),
            thumbnail=None,
        )
        empty_msg = SimpleNamespace(
            author=SimpleNamespace(id=123),
            id=10,
            content="",
            channel=channel,
            reference=None,
            attachments=[],
            embeds=[embed],
            created_at=created_at,
        )

        async def history(limit=None, after=None):
            yield empty_msg

        channel.history = history

        with patch("src.utils.chat_context.logger.warning") as mock_warning:
            context, _, _ = await handler._prepare_mention_context(empty_msg, bot_user)

        self.assertIn("[Embeds:", context[0]["content"][0]["text"])
        self.assertEqual(len(context[0]["content"]), 1)
        mock_warning.assert_called_once()

        truly_empty = SimpleNamespace(
            author=SimpleNamespace(id=123),
            id=11,
            content="",
            channel=channel,
            reference=None,
            attachments=[],
            embeds=[],
            created_at=created_at,
        )

        async def empty_history(limit=None, after=None):
            yield truly_empty

        handler._history_cache = {}
        channel.history = empty_history
        context, _, _ = await handler._prepare_mention_context(truly_empty, bot_user)
        self.assertIn("[Empty Message]", context[0]["content"][0]["text"])

    async def test_handle_mention_sends_plain_text_reply(self):
        handler, state_service, _, openai_service, message_service = self.make_handler()
        handler._prepare_mention_context = AsyncMock(return_value=([{"role": "user", "content": "hi"}], "system", 0.0))
        openai_service.get_chat_completion = AsyncMock(return_value=("hello there", 0.0))
        channel = MagicMock()
        channel.id = 1
        channel.typing.return_value = AsyncTyping()
        message = SimpleNamespace(author=SimpleNamespace(id=123), channel=channel)
        bot_user = SimpleNamespace(id=999)

        await handler.handle_mention(message, bot_user, "gpt-test")

        state_service.mark_channel_active.assert_called_once_with(1)
        message_service.send_channel_reply.assert_awaited_once_with(channel, "hello there")

    def make_streaming_channel(self):
        preview = MagicMock()
        preview.edit = AsyncMock()
        preview.delete = AsyncMock()
        channel = MagicMock()
        channel.id = 1
        channel.typing.return_value = AsyncTyping()
        channel.send = AsyncMock(return_value=preview)
        return channel, preview

    async def test_handle_mention_streams_then_edits_in_final_reply(self):
        handler, _, _, openai_service, message_service = self.make_handler()
        message_service.DISCORD_MESSAGE_LIMIT = 2000
        message_service.fit_to_discord = AsyncMock(side_effect=lambda text: text)
        handler._prepare_mention_context = AsyncMock(return_value=([{"role": "user", "content": "hi"}], "system", 0.0))

        async def streamed_completion(**kwargs):
            kwargs["on_text"]("hello")
            await asyncio.sleep(0)
            # The prefix is what was recorded while answering, not what the call returns.
            await record_cost(OPENROUTER, 0.01)
            return "hello there", 999.0

        openai_service.get_chat_completion = streamed_completion
        channel, preview = self.make_streaming_channel()
        message = SimpleNamespace(author=SimpleNamespace(id=123), channel=channel)

        await handler.handle_mention(message, SimpleNamespace(id=999), "gpt-test")

        channel.send.assert_awaited_once()
        preview.edit.assert_awaited_once_with(content="[$0.010] hello there", suppress=True)
        message_service.send_channel_reply.assert_not_awaited()

    async def test_handle_mention_deletes_partial_reply_when_stream_fails(self):
        handler, _, _, openai_service, message_service = self.make_handler()
        message_service.DISCORD_MESSAGE_LIMIT = 2000
        handler._prepare_mention_context = AsyncMock(return_value=([{"role": "user", "content": "hi"}], "system", 0.0))

        async def failing_completion(**kwargs):
            kwargs["on_text"]("half an ans")
            await asyncio.sleep(0)
            raise OpenAIServiceError("stream broke")

        openai_service.get_chat_completion = failing_completion
        channel, preview = self.make_streaming_channel()
        message = SimpleNamespace(author=SimpleNamespace(id=123), channel=channel)

        await handler.handle_mention(message, SimpleNamespace(id=999), "gpt-test")

        preview.delete.assert_awaited_once()
        self.assertIn("stream broke", message_service.send_channel_reply.await_args.args[1])

    async def test_handle_mention_sends_files_when_response_contains_them(self):
        handler, _, _, openai_service, message_service = self.make_handler()
        handler._prepare_mention_context = AsyncMock(return_value=([{"role": "user", "content": "hi"}], "system", 0.0))
        openai_service.get_chat_completion = AsyncMock(
            return_value=({"text": "here you go", "files": ["file-a"]}, 0.0)
        )
        channel = MagicMock()
        channel.id = 2
        channel.typing.return_value = AsyncTyping()
        message = SimpleNamespace(author=SimpleNamespace(id=123), channel=channel)

        await handler.handle_mention(message, SimpleNamespace(id=999), "gpt-test")

        message_service.send_channel_reply_with_files.assert_awaited_once_with(channel, "here you go", ["file-a"])

    async def test_handle_mention_sends_text_reply_when_response_dict_has_no_files(self):
        handler, _, _, openai_service, message_service = self.make_handler()
        handler._prepare_mention_context = AsyncMock(return_value=([{"role": "user", "content": "hi"}], "system", 0.0))
        openai_service.get_chat_completion = AsyncMock(return_value=({"text": "just text", "files": []}, 0.0))
        channel = MagicMock()
        channel.id = 6
        channel.typing.return_value = AsyncTyping()
        message = SimpleNamespace(author=SimpleNamespace(id=123), channel=channel)

        await handler.handle_mention(message, SimpleNamespace(id=999), "gpt-test")

        message_service.send_channel_reply.assert_awaited_once_with(channel, "just text")

    async def test_handle_mention_handles_none_response(self):
        handler, _, _, openai_service, message_service = self.make_handler()
        handler._prepare_mention_context = AsyncMock(return_value=([{"role": "user", "content": "hi"}], "system", 0.0))
        openai_service.get_chat_completion = AsyncMock(return_value=(None, 0.0))
        channel = MagicMock()
        channel.id = 3
        channel.typing.return_value = AsyncTyping()
        message = SimpleNamespace(author=SimpleNamespace(id=123), channel=channel)

        await handler.handle_mention(message, SimpleNamespace(id=999), "gpt-test")

        sent_message = message_service.send_channel_reply.await_args.args[1]
        self.assertIn("failed to get a response", sent_message.lower())

    async def test_handle_mention_handles_openai_errors_with_fallback_send(self):
        handler, _, _, openai_service, message_service = self.make_handler()
        handler._prepare_mention_context = AsyncMock(return_value=([{"role": "user", "content": "hi"}], "system", 0.0))
        openai_service.get_chat_completion = AsyncMock(side_effect=OpenAIServiceError("bad upstream"))
        message_service.send_channel_reply = AsyncMock(side_effect=RuntimeError("discord fail"))
        channel = MagicMock()
        channel.id = 4
        channel.typing.return_value = AsyncTyping()
        channel.send = AsyncMock()
        message = SimpleNamespace(author=SimpleNamespace(id=123), channel=channel)

        await handler.handle_mention(message, SimpleNamespace(id=999), "gpt-test")

        channel.send.assert_awaited_once()
        self.assertIn("couldn't respond", channel.send.await_args.args[0].lower())

    async def test_handle_mention_logs_when_openai_fallback_send_also_fails(self):
        handler, _, _, openai_service, message_service = self.make_handler()
        handler._prepare_mention_context = AsyncMock(return_value=([{"role": "user", "content": "hi"}], "system", 0.0))
        openai_service.get_chat_completion = AsyncMock(side_effect=OpenAIServiceError("bad upstream"))
        message_service.send_channel_reply = AsyncMock(side_effect=RuntimeError("discord fail"))
        channel = MagicMock()
        channel.id = 7
        channel.typing.return_value = AsyncTyping()
        channel.send = AsyncMock(side_effect=RuntimeError("fallback fail"))
        message = SimpleNamespace(author=SimpleNamespace(id=123), channel=channel)

        with patch("src.bot.handlers.mention.logger.error") as mock_error:
            await handler.handle_mention(message, SimpleNamespace(id=999), "gpt-test")

        self.assertTrue(any("Failed to send fallback error message" in call.args[0] for call in mock_error.call_args_list))

    async def test_handle_mention_handles_unexpected_errors_with_fallback_send(self):
        handler, _, _, openai_service, message_service = self.make_handler()
        handler._prepare_mention_context = AsyncMock(side_effect=RuntimeError("boom"))
        openai_service.get_chat_completion = AsyncMock()
        message_service.send_channel_reply = AsyncMock(side_effect=RuntimeError("discord fail"))
        channel = MagicMock()
        channel.id = 5
        channel.typing.return_value = AsyncTyping()
        channel.send = AsyncMock()
        message = SimpleNamespace(author=SimpleNamespace(id=123), channel=channel)

        await handler.handle_mention(message, SimpleNamespace(id=999), "gpt-test")

        channel.send.assert_awaited_once()
        self.assertIn("couldn't respond", channel.send.await_args.args[0].lower())

    async def test_handle_mention_logs_when_unexpected_fallback_send_also_fails(self):
        handler, _, _, openai_service, message_service = self.make_handler()
        handler._prepare_mention_context = AsyncMock(side_effect=RuntimeError("boom"))
        openai_service.get_chat_completion = AsyncMock()
        message_service.send_channel_reply = AsyncMock(side_effect=RuntimeError("discord fail"))
        channel = MagicMock()
        channel.id = 8
        channel.typing.return_value = AsyncTyping()
        channel.send = AsyncMock(side_effect=RuntimeError("fallback fail"))
        message = SimpleNamespace(author=SimpleNamespace(id=123), channel=channel)

        with patch("src.bot.handlers.mention.logger.error") as mock_error:
            await handler.handle_mention(message, SimpleNamespace(id=999), "gpt-test")

        self.assertTrue(any("Failed to send fallback error message" in call.args[0] for call in mock_error.call_args_list))

    def _simple_msg(self, msg_id, channel, content="hi", author_id=111):
        return SimpleNamespace(
            author=SimpleNamespace(id=author_id),
            id=msg_id,
            content=content,
            channel=channel,
            reference=None,
            attachments=[],
            embeds=[],
            created_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        )

    async def test_mention_replying_to_an_old_thread_gets_its_earlier_messages(self):
        handler, _, _, _, _ = self.make_handler()
        channel = MagicMock()
        channel.id = 44
        channel.name = "threads"
        root = self._simple_msg(40, channel, content="anyone tried the new Zelda?", author_id=222)
        bot_reply = self._simple_msg(50, channel, content="[$0.010] I hear it's great", author_id=999)
        bot_reply.reference = SimpleNamespace(message_id=40, resolved=None)
        mention = self._simple_msg(60, channel, content="<@999> why great?")
        mention.reference = SimpleNamespace(message_id=50, resolved=None)
        channel.fetch_message = AsyncMock(side_effect=lambda mid: {40: root, 50: bot_reply}[mid])

        async def history(limit=None, after=None):
            yield mention

        channel.history = history
        context, _, _ = await handler._prepare_mention_context(mention, SimpleNamespace(id=999))

        block = context[-1]["content"][-1]["text"]
        self.assertTrue(block.startswith("<reply_thread>"))
        self.assertLess(block.index("[40] <@222>: anyone tried"), block.index("[50] <@999>: I hear it's great"))
        self.assertNotIn("$0.010", block)

    async def test_reply_threads_stop_at_the_history_window(self):
        handler, _, _, _, _ = self.make_handler()
        channel = MagicMock()
        channel.id = 45
        channel.name = "threads"
        parent = self._simple_msg(50, channel, content="in the window")
        mention = self._simple_msg(60, channel, content="<@999> right")
        mention.reference = SimpleNamespace(message_id=50, resolved=None)
        channel.fetch_message = AsyncMock()

        async def history(limit=None, after=None):
            for msg in (mention, parent):
                yield msg

        channel.history = history
        context, _, _ = await handler._prepare_mention_context(mention, SimpleNamespace(id=999))

        channel.fetch_message.assert_not_awaited()
        self.assertFalse(any("<reply_thread>" in p.get("text", "") for p in context[-1]["content"]))

    async def test_saved_time_zones_ride_on_the_mention(self):
        handler, _, _, _, _ = self.make_handler()
        handler._timezone_store = MagicMock()
        handler._timezone_store.time_zones_block = AsyncMock(return_value="<time_zones>\n<@111>: America/Denver")
        channel = MagicMock()
        channel.id = 46
        channel.name = "tz"
        mention = self._simple_msg(60, channel, content="<@999> remind me at 8pm")
        mention.guild = SimpleNamespace(id=10)
        mention.mentions = []
        mention.author.bot = False

        async def history(limit=None, after=None):
            yield mention

        channel.history = history
        context, _, _ = await handler._prepare_mention_context(mention, SimpleNamespace(id=999))

        handler._timezone_store.time_zones_block.assert_awaited_once_with(10, [111])
        self.assertIn("America/Denver", context[-1]["content"][-1]["text"])

    async def test_prepare_context_calls_url_fetcher_with_cache_only_flag(self):
        fetcher = AsyncMock(return_value=("", []))
        handler, _, _, _, _ = self.make_handler(url_content_fetcher=fetcher)
        channel = MagicMock()
        channel.id = 42
        channel.name = "links"
        mention_msg = self._simple_msg(1, channel, content="see https://site.example")

        async def history(limit=None, after=None):
            yield mention_msg

        channel.history = history

        await handler._prepare_mention_context(mention_msg, SimpleNamespace(id=999))

        fetcher.assert_awaited_once()
        self.assertIn("see https://site.example", fetcher.await_args.args[0])
        self.assertEqual(fetcher.await_args.kwargs, {"cache_only": False})

    async def test_think_harder_addon_only_on_opus_direct(self):
        handler, _, _, _, _ = self.make_handler()
        channel = MagicMock()
        channel.id = 44
        channel.name = "think"
        mention_msg = self._simple_msg(1, channel)

        async def history(limit=None, after=None):
            yield mention_msg

        channel.history = history

        for model, expected in (("opus-5.5-direct", True), ("sonnet-5.5-direct", False), (None, False)):
            _, system_prompt, _ = await handler._prepare_mention_context(
                mention_msg, SimpleNamespace(id=999), model=model)
            self.assertEqual("## Thinking harder" in system_prompt, expected, model)
            if expected:
                self.assertIn("`think_harder`", system_prompt)

    async def test_direct_models_get_tools_instead_of_injected_memory(self):
        handler, _, _, _, _ = self.make_handler(anthropic_service=MagicMock())
        handler._user_notes_provider = AsyncMock(return_value="<user_notes>n</user_notes>")
        handler._channel_summary_provider = AsyncMock(return_value="## Lately in this channel")
        handler._memory_index_provider = AsyncMock(return_value="People you have memory notes on: <@111>.")
        channel = MagicMock()
        channel.id = 45
        channel.name = "tools"
        mention_msg = self._simple_msg(1, channel)
        mention_msg.author.bot = False
        mention_msg.guild = SimpleNamespace(id=5)
        mention_msg.mentions = []

        async def history(limit=None, after=None):
            yield mention_msg

        channel.history = history

        with patch("src.bot.handlers.mention._load_how_i_work", return_value="## How you work"):
            context, system_prompt, _ = await handler._prepare_mention_context(
                mention_msg, SimpleNamespace(id=999), model="sonnet-5.5-direct")
            self.assertIn("## Your tools", system_prompt)
            self.assertIn("`recall_users(user_ids)`", system_prompt)
            self.assertIn("`how_i_work()`", system_prompt)
            self.assertIn("People you have memory notes on: <@111>.", system_prompt)
            handler._memory_index_provider.assert_awaited_once_with(5, 45)
            self.assertNotIn("## How you work", system_prompt)
            self.assertNotIn("## Lately in this channel", system_prompt)
            self.assertNotIn("user_notes", str(context))
            handler._user_notes_provider.assert_not_awaited()
            handler._channel_summary_provider.assert_not_awaited()

            context, system_prompt, _ = await handler._prepare_mention_context(
                mention_msg, SimpleNamespace(id=999), model="gpt-test")
            self.assertNotIn("## Your tools", system_prompt)
            self.assertIn("## How you work", system_prompt)
            self.assertIn("## Lately in this channel", system_prompt)
            self.assertIn("<user_notes>n</user_notes>", str(context))

    async def test_direct_models_get_raw_links_instead_of_fetched_content(self):
        fetcher = AsyncMock(return_value=("FETCHED", []))
        handler, _, _, _, _ = self.make_handler(url_content_fetcher=fetcher, anthropic_service=MagicMock())
        channel = MagicMock()
        channel.id = 48
        channel.name = "links"
        mention_msg = self._simple_msg(1, channel, content="see https://youtu.be/abc")
        mention_msg.guild = None

        async def history(limit=None, after=None):
            yield mention_msg

        channel.history = history

        context, system_prompt, _ = await handler._prepare_mention_context(
            mention_msg, SimpleNamespace(id=999), model="sonnet-5.5-direct")
        fetcher.assert_not_awaited()
        self.assertIn("https://youtu.be/abc", str(context))
        self.assertNotIn("FETCHED", str(context))
        self.assertIn("`read_urls(urls)`", system_prompt)

        context, _, _ = await handler._prepare_mention_context(mention_msg, SimpleNamespace(id=999), model="gpt-test")
        fetcher.assert_awaited()
        self.assertIn("FETCHED", str(context))

    async def test_direct_models_get_the_day_summary_only_through_recall_today(self):
        handler, _, _, _, _ = self.make_handler(anthropic_service=MagicMock())
        provider = AsyncMock(return_value=("earlier: trucks", 0.05))
        handler._day_summary_provider = provider
        channel = MagicMock()
        channel.id = 49
        channel.name = "today"
        mention_msg = self._simple_msg(1, channel)
        mention_msg.guild = None

        async def history(limit=None, after=None):
            yield mention_msg

        channel.history = history

        _, system_prompt, cost = await handler._prepare_mention_context(
            mention_msg, SimpleNamespace(id=999), model="sonnet-5.5-direct")
        provider.assert_not_awaited()
        self.assertEqual(cost, 0.0)
        self.assertNotIn("<day_summary>", system_prompt)
        self.assertIn("`recall_today()`", system_prompt)

        tool = next(t for t in handler._build_tools(mention_msg) if t.definition["name"] == "recall_today")
        result = await tool.run({})
        provider.assert_awaited_once_with(channel, mention_msg, 999, "sonnet-5.5-direct")
        self.assertAlmostEqual(result.cost, 0.05)

        _, system_prompt, cost = await handler._prepare_mention_context(
            mention_msg, SimpleNamespace(id=999), model="gpt-test")
        self.assertIn("<day_summary>\nearlier: trucks", system_prompt)
        self.assertAlmostEqual(cost, 0.05)

    async def test_prepare_context_reports_what_it_is_doing(self):
        fetcher = AsyncMock(return_value=("", []))
        handler, _, _, _, _ = self.make_handler(url_content_fetcher=fetcher)
        handler._day_summary_provider = AsyncMock(return_value=("", 0.0))
        channel = MagicMock()
        channel.id = 50
        channel.name = "status"
        plain = self._simple_msg(1, channel, content="hi")
        linked = self._simple_msg(2, channel, content="see https://youtu.be/abc")

        async def history(limit=None, after=None):
            yield linked
            yield plain

        channel.history = history
        statuses = []

        await handler._prepare_mention_context(linked, SimpleNamespace(id=999), model="gpt-test",
                                               on_status=statuses.append)

        self.assertEqual(statuses, ["reading the chat...", "catching up on today...", "reading the chat...",
                                    "reading links..."])

    async def test_history_ranges_end_the_irc_log_where_discord_starts(self):
        handler, _, _, _, _ = self.make_handler()
        handler._irc_log = MagicMock()
        handler._irc_log.first_day.return_value = datetime(2014, 1, 3).date()
        handler._irc_log.speakers.return_value = [("Cyc66", 600999, 2014, 2022), ("td", 5, 2015, 2015)]
        first = SimpleNamespace(created_at=datetime(2023, 6, 19, 21, 22, tzinfo=timezone.utc))
        calls = []

        async def history(limit=None, oldest_first=None):
            calls.append((limit, oldest_first))
            yield first

        channel = SimpleNamespace(id=46, history=history)
        note = await handler._history_ranges(channel)
        self.assertIn("the IRC log covers 2014-01-03 to 2023-06-19", note)
        self.assertIn("Discord history starts 2023-06-19", note)
        self.assertIn("before 2023-06-19, search the IRC log", note)
        self.assertIn("line count and years active: Cyc66 600,999 2014-2022; td 5 2015.", note)
        # Found once per channel, so the system prompt stays cacheable.
        self.assertEqual(await handler._history_ranges(channel), note)
        self.assertEqual(calls, [(1, True)])
        handler._irc_log.first_day.assert_called_once()
        handler._irc_log.speakers.assert_called_once_with(60)

    async def test_warm_up_reads_the_irc_log_once_before_any_mention(self):
        handler, _, _, _, _ = self.make_handler()
        handler._irc_log = MagicMock()
        handler._irc_log.first_day.return_value = datetime(2014, 1, 3).date()
        handler._irc_log.speakers.return_value = [("Cyc66", 10, 2014, 2022)]

        handler.warm_up()
        handler.warm_up()
        await handler._irc_facts
        channel = MagicMock(id=7, created_at=datetime(2023, 5, 1, tzinfo=timezone.utc))

        async def history(limit=None, oldest_first=False):
            if False:
                yield None

        channel.history = history
        note = await handler._history_ranges(channel)

        self.assertIn("2014-01-03", note)
        self.assertIn("Cyc66 10 2014-2022", note)
        handler._irc_log.speakers.assert_called_once_with(60)

    async def test_history_ranges_fall_back_to_channel_creation(self):
        handler, _, _, _, _ = self.make_handler()

        async def history(limit=None, oldest_first=None):
            raise discord.HTTPException(MagicMock(status=500), "boom")
            yield

        channel = SimpleNamespace(id=47, history=history, created_at=datetime(2023, 6, 1, tzinfo=timezone.utc))
        self.assertEqual(await handler._history_ranges(channel),
                         "Dates covered: this channel's Discord history starts 2023-06-01.\n")

    async def test_handle_mention_passes_tools_only_to_direct_models(self):
        anthropic_service = MagicMock()
        anthropic_service.get_chat_completion = AsyncMock(return_value=("hi", 0.0))
        handler, _, _, openai_service, _ = self.make_handler(anthropic_service=anthropic_service)
        handler._prepare_mention_context = AsyncMock(return_value=([{"role": "user", "content": "hi"}], "system", 0.0))
        openai_service.get_chat_completion = AsyncMock(return_value=("hi", 0.0))
        channel, _ = self.make_streaming_channel()
        message = SimpleNamespace(author=SimpleNamespace(id=123), channel=channel, guild=None)

        await handler.handle_mention(message, SimpleNamespace(id=999), "sonnet-5.5-direct")
        await handler.handle_mention(message, SimpleNamespace(id=999), "gpt-test")

        tools = anthropic_service.get_chat_completion.await_args.kwargs["tools"]
        self.assertEqual([t.definition["name"] for t in tools], ["read_urls", "transcribe_youtube", "how_i_work"])
        self.assertNotIn("tools", openai_service.get_chat_completion.await_args.kwargs)

    async def test_prepare_context_puts_queued_mention_after_replies_posted_while_waiting(self):
        handler, _, _, _, _ = self.make_handler()
        channel = MagicMock()
        channel.id = 43
        channel.name = "queue"
        first_q = self._simple_msg(1, channel, content="q1")
        second_q = self._simple_msg(2, channel, content="q2", author_id=222)
        first_a = self._simple_msg(3, channel, content="a1", author_id=999)

        async def history(limit=None, after=None):
            for msg in [first_a, second_q, first_q]:
                yield msg

        channel.history = history

        context, _, _ = await handler._prepare_mention_context(second_q, SimpleNamespace(id=999))

        self.assertEqual([c["role"] for c in context], ["user", "assistant", "user"])
        self.assertEqual([c["message_id"] for c in context], [1, 3, 2])
        self.assertIn("a1", context[1]["content"][0]["text"])
        self.assertIn("q2", context[-1]["content"][0]["text"])

    async def test_history_window_reset_clears_gemini_and_anthropic_caches(self):
        gemini_service = MagicMock()
        anthropic_service = MagicMock()
        handler, _, _, _, _ = self.make_handler(
            gemini_service=gemini_service, anthropic_service=anthropic_service
        )
        channel = MagicMock()
        channel.id = 77
        channel.name = "fresh"
        mention_msg = self._simple_msg(1, channel)

        async def history(limit=None, after=None):
            yield mention_msg

        channel.history = history

        await handler._prepare_mention_context(mention_msg, SimpleNamespace(id=999))

        gemini_service.clear_channel_cache.assert_called_once_with(77)
        anthropic_service.clear_channel_cache.assert_called_once_with(77)

    async def test_massive_history_gap_clears_gemini_and_anthropic_caches(self):
        from src.bot.handlers.mention import MAX_HISTORY_GAP

        gemini_service = MagicMock()
        anthropic_service = MagicMock()
        handler, _, _, _, _ = self.make_handler(
            gemini_service=gemini_service, anthropic_service=anthropic_service
        )
        channel = MagicMock()
        channel.id = 78
        channel.name = "busy"
        oldest = self._simple_msg(1, channel)
        mention_msg = self._simple_msg(2, channel)
        gap_msgs = [self._simple_msg(100 + i, channel) for i in range(MAX_HISTORY_GAP)]

        async def history(limit=None, after=None):
            for msg in (gap_msgs if after is not None else [mention_msg]):
                yield msg

        channel.history = history
        handler._history_cache[78] = {"timestamp": 1000, "oldest_message": oldest}

        with patch("src.bot.handlers.mention.time", MagicMock(time=MagicMock(return_value=1001))):
            await handler._prepare_mention_context(mention_msg, SimpleNamespace(id=999))

        gemini_service.clear_channel_cache.assert_called_once_with(78)
        anthropic_service.clear_channel_cache.assert_called_once_with(78)
        self.assertIs(handler._history_cache[78]["oldest_message"], mention_msg)
