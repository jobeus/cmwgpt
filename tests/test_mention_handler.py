"""Tests for MentionHandler context building and queue delegation."""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.openai_service import OpenAIServiceError
from src.bot.handlers.mention import MentionHandler


class AsyncTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TestMentionHandler(unittest.IsolatedAsyncioTestCase):
    def make_handler(self, *, attachment_converter=None, url_converter=None, url_content_fetcher=None):
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
            mention_legend_provider=AsyncMock(return_value="legend text"),
            attachment_converter=attachment_converter or AsyncMock(return_value="data:file/plain;base64,AAA"),
            url_converter=url_converter or AsyncMock(return_value="data:image/png;base64,BBB"),
            url_content_fetcher=url_content_fetcher or AsyncMock(return_value=""),
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
        async def url_content_fetcher(text):
            return "[fetched-url]\n" if "https://site.example" in text else ""

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
            image=SimpleNamespace(url="https://img.example/preview.png"),
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

        context, system_prompt = await handler._prepare_mention_context(mention_msg, bot_user)

        self.assertEqual(len(context), 4)
        self.assertEqual(context[1]["role"], "assistant")
        self.assertEqual(context[2]["role"], "user")
        self.assertEqual(context[2]["content"][1]["type"], "image_url")
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

        context, _ = await handler._prepare_mention_context(mention_msg, bot_user)

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
        attachment = SimpleNamespace(content_type="text/plain")
        embed = SimpleNamespace(
            title="embed title",
            description="embed desc",
            url="https://embed.example",
            image=None,
            thumbnail=SimpleNamespace(url="https://img.example/thumb.png"),
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

        context, _ = await handler._prepare_mention_context(mention_msg, bot_user)

        self.assertIn("[Embeds:", context[0]["content"][0]["text"])
        self.assertEqual(context[0]["content"][1]["type"], "file")
        self.assertEqual(context[0]["content"][2]["type"], "image_url")

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

        with patch("src.bot.handlers.mention.time.time", side_effect=[1000, 1005]):
            context, system_prompt = await handler._prepare_mention_context(mention_msg, bot_user)
            channel.history = second_history
            cached_context, _ = await handler._prepare_mention_context(mention_msg, bot_user)

        self.assertTrue(system_prompt.startswith("channel promptIn the channel you are"))
        self.assertEqual(context[-1]["content"][1]["type"], "image_url")
        self.assertEqual(history_calls[0], (10, None))
        self.assertEqual(history_calls[1], (None, older_msg))
        self.assertEqual(handler._history_cache[channel.id]["timestamp"], 1005)
        self.assertIn("newer", cached_context[-1]["content"][0]["text"])

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
            image=SimpleNamespace(url="https://img.example/preview.png"),
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

        with patch("src.bot.handlers.mention.logger.warning") as mock_warning:
            context, _ = await handler._prepare_mention_context(empty_msg, bot_user)

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
        context, _ = await handler._prepare_mention_context(truly_empty, bot_user)
        self.assertIn("[Empty Message]", context[0]["content"][0]["text"])

    async def test_handle_mention_sends_plain_text_reply(self):
        handler, state_service, _, openai_service, message_service = self.make_handler()
        handler._prepare_mention_context = AsyncMock(return_value=([{"role": "user", "content": "hi"}], "system"))
        openai_service.get_chat_completion = AsyncMock(return_value="hello there")
        channel = MagicMock()
        channel.id = 1
        channel.typing.return_value = AsyncTyping()
        message = SimpleNamespace(author=SimpleNamespace(id=123), channel=channel)
        bot_user = SimpleNamespace(id=999)

        await handler.handle_mention(message, bot_user, "gpt-test")

        state_service.mark_channel_active.assert_called_once_with(1)
        message_service.send_channel_reply.assert_awaited_once_with(channel, "hello there")

    async def test_handle_mention_sends_files_when_response_contains_them(self):
        handler, _, _, openai_service, message_service = self.make_handler()
        handler._prepare_mention_context = AsyncMock(return_value=([{"role": "user", "content": "hi"}], "system"))
        openai_service.get_chat_completion = AsyncMock(
            return_value={"text": "here you go", "files": ["file-a"]}
        )
        channel = MagicMock()
        channel.id = 2
        channel.typing.return_value = AsyncTyping()
        message = SimpleNamespace(author=SimpleNamespace(id=123), channel=channel)

        await handler.handle_mention(message, SimpleNamespace(id=999), "gpt-test")

        message_service.send_channel_reply_with_files.assert_awaited_once_with(channel, "here you go", ["file-a"])

    async def test_handle_mention_sends_text_reply_when_response_dict_has_no_files(self):
        handler, _, _, openai_service, message_service = self.make_handler()
        handler._prepare_mention_context = AsyncMock(return_value=([{"role": "user", "content": "hi"}], "system"))
        openai_service.get_chat_completion = AsyncMock(return_value={"text": "just text", "files": []})
        channel = MagicMock()
        channel.id = 6
        channel.typing.return_value = AsyncTyping()
        message = SimpleNamespace(author=SimpleNamespace(id=123), channel=channel)

        await handler.handle_mention(message, SimpleNamespace(id=999), "gpt-test")

        message_service.send_channel_reply.assert_awaited_once_with(channel, "just text")

    async def test_handle_mention_handles_none_response(self):
        handler, _, _, openai_service, message_service = self.make_handler()
        handler._prepare_mention_context = AsyncMock(return_value=([{"role": "user", "content": "hi"}], "system"))
        openai_service.get_chat_completion = AsyncMock(return_value=None)
        channel = MagicMock()
        channel.id = 3
        channel.typing.return_value = AsyncTyping()
        message = SimpleNamespace(author=SimpleNamespace(id=123), channel=channel)

        await handler.handle_mention(message, SimpleNamespace(id=999), "gpt-test")

        sent_message = message_service.send_channel_reply.await_args.args[1]
        self.assertIn("failed to get a response", sent_message.lower())

    async def test_handle_mention_handles_openai_errors_with_fallback_send(self):
        handler, _, _, openai_service, message_service = self.make_handler()
        handler._prepare_mention_context = AsyncMock(return_value=([{"role": "user", "content": "hi"}], "system"))
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
        handler._prepare_mention_context = AsyncMock(return_value=([{"role": "user", "content": "hi"}], "system"))
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