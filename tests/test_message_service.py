"""Tests for MessageService with injected optional dependencies."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.message_service import SEND_ATTEMPTS, MessageService


class FakeHTTPException(Exception):
    def __init__(self, status, headers=None):
        super().__init__(f"http {status}")
        self.status = status
        self.response = SimpleNamespace(headers=headers or {})


def _patch_http():
    return patch("src.services.message_service.HTTPException", FakeHTTPException)


def _patch_sleep():
    return patch("src.services.message_service.asyncio.sleep", new=AsyncMock())


def _fake_file():
    f = MagicMock()
    f.reset = MagicMock()
    return f


class TestMessageService(unittest.IsolatedAsyncioTestCase):
    async def test_short_reply_sent_with_content_kwarg_and_suppressed_embeds(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        service = MessageService()

        await service.send_channel_reply(channel, "hello")

        channel.send.assert_awaited_once_with(content="hello", files=[], suppress_embeds=True)

    async def test_long_reply_uses_injected_paste_service(self):
        paste_service = MagicMock()
        paste_service.upload_markdown = AsyncMock(return_value="https://paste.jobe.wtf/long.md")
        channel = MagicMock()
        channel.send = AsyncMock()
        service = MessageService(paste_service_instance=paste_service)

        await service.send_channel_reply(channel, "x" * 2500)

        paste_service.upload_markdown.assert_awaited_once_with("x" * 2500)
        channel.send.assert_awaited_once()
        sent_message = channel.send.await_args.kwargs["content"]
        self.assertIn("too long", sent_message)
        self.assertIn("https://paste.jobe.wtf/long.md", sent_message)

    async def test_long_reply_without_paste_service_falls_back_cleanly(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        service = MessageService(paste_service_instance=None)

        await service.send_channel_reply(channel, "x" * 2500)

        channel.send.assert_awaited_once()
        sent_message = channel.send.await_args.kwargs["content"]
        self.assertIn("over 2000 characters", sent_message.lower())

    async def test_paste_upload_happens_once_even_when_send_is_retried(self):
        paste_service = MagicMock()
        paste_service.upload_markdown = AsyncMock(return_value="https://paste.jobe.wtf/x.md")
        channel = MagicMock()
        channel.send = AsyncMock(side_effect=[FakeHTTPException(503), None])
        service = MessageService(paste_service_instance=paste_service)

        with _patch_http(), _patch_sleep():
            await service.send_channel_reply(channel, "x" * 2500)

        paste_service.upload_markdown.assert_awaited_once()
        self.assertEqual(channel.send.await_count, 2)

    async def test_rate_limit_is_not_retried_here(self):
        channel = MagicMock()
        channel.send = AsyncMock(side_effect=FakeHTTPException(429))
        service = MessageService()

        with _patch_http(), _patch_sleep() as mock_sleep:
            with self.assertRaises(FakeHTTPException):
                await service.send_channel_reply(channel, "hello")

        channel.send.assert_awaited_once()
        mock_sleep.assert_not_awaited()

    async def test_other_4xx_raises_without_retry(self):
        channel = MagicMock()
        channel.send = AsyncMock(side_effect=FakeHTTPException(400))
        service = MessageService()

        with _patch_http(), _patch_sleep() as mock_sleep:
            with self.assertRaises(FakeHTTPException):
                await service.send_channel_reply(channel, "hello")

        channel.send.assert_awaited_once()
        mock_sleep.assert_not_awaited()

    async def test_server_error_retried_with_backoff_then_succeeds(self):
        channel = MagicMock()
        channel.send = AsyncMock(side_effect=[FakeHTTPException(500), FakeHTTPException(502), None])
        service = MessageService()

        with _patch_http(), _patch_sleep() as mock_sleep:
            await service.send_channel_reply(channel, "hello")

        self.assertEqual(channel.send.await_count, 3)
        self.assertEqual([c.args[0] for c in mock_sleep.await_args_list], [1.0, 2.0])

    async def test_server_error_exhausts_attempts_then_raises(self):
        channel = MagicMock()
        channel.send = AsyncMock(side_effect=FakeHTTPException(500))
        service = MessageService()

        with _patch_http(), _patch_sleep() as mock_sleep:
            with self.assertRaises(FakeHTTPException):
                await service.send_channel_reply(channel, "hello")

        self.assertEqual(channel.send.await_count, SEND_ATTEMPTS)
        self.assertEqual(mock_sleep.await_count, SEND_ATTEMPTS - 1)

    async def test_unexpected_errors_retried_then_raise(self):
        channel = MagicMock()
        channel.send = AsyncMock(side_effect=[RuntimeError("a"), RuntimeError("b"), RuntimeError("c")])
        service = MessageService()

        with _patch_sleep() as mock_sleep:
            with self.assertRaises(RuntimeError):
                await service.send_channel_reply(channel, "hello")

        self.assertEqual(mock_sleep.await_count, 2)

        channel.send = AsyncMock(side_effect=[RuntimeError("boom"), None])
        with _patch_sleep() as mock_sleep:
            await service.send_channel_reply(channel, "hello")
        mock_sleep.assert_awaited_once_with(1.0)

    async def test_forbidden_and_not_found_raise_immediately(self):
        service = MessageService()
        forbidden = type("FakeForbidden", (Exception,), {})
        not_found = type("FakeNotFound", (Exception,), {})

        for exc in (forbidden("nope"), not_found("gone")):
            channel = MagicMock()
            channel.send = AsyncMock(side_effect=exc)
            with patch("src.services.message_service.Forbidden", forbidden), \
                    patch("src.services.message_service.NotFound", not_found), _patch_sleep() as mock_sleep:
                with self.assertRaises(type(exc)):
                    await service.send_channel_reply_with_files(channel, "reply", files=[_fake_file()])
            channel.send.assert_awaited_once()
            mock_sleep.assert_not_awaited()

    async def test_with_files_defaults_to_empty_files(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        service = MessageService()

        await service.send_channel_reply_with_files(channel, "reply")

        channel.send.assert_awaited_once_with(content="reply", files=[], suppress_embeds=True)

    async def test_with_files_resets_files_before_each_attempt(self):
        channel = MagicMock()
        channel.send = AsyncMock(side_effect=[FakeHTTPException(500), None])
        service = MessageService()
        f = _fake_file()

        with _patch_http(), _patch_sleep():
            await service.send_channel_reply_with_files(channel, "reply", files=[f])

        self.assertEqual(f.reset.call_count, 2)
        self.assertEqual(channel.send.await_args.kwargs["files"], [f])

    async def test_with_files_handles_payload_too_large(self):
        channel = MagicMock()
        channel.send = AsyncMock(side_effect=[FakeHTTPException(413), None])
        service = MessageService()

        with _patch_http():
            await service.send_channel_reply_with_files(channel, "reply", files=[_fake_file()])

        self.assertEqual(channel.send.await_count, 2)
        error_call = channel.send.await_args_list[-1]
        self.assertIn("too large", error_call.kwargs["content"].lower())
        self.assertNotIn("files", error_call.kwargs)

    async def test_413_without_files_raises(self):
        channel = MagicMock()
        channel.send = AsyncMock(side_effect=FakeHTTPException(413))
        service = MessageService()

        with _patch_http():
            with self.assertRaises(FakeHTTPException):
                await service.send_channel_reply(channel, "reply")

    async def test_with_files_uses_paste_service_for_long_messages(self):
        paste_service = MagicMock()
        paste_service.upload_markdown = AsyncMock(return_value="https://paste.jobe.wtf/files.md")
        channel = MagicMock()
        channel.send = AsyncMock()
        service = MessageService(paste_service_instance=paste_service)

        await service.send_channel_reply_with_files(channel, "x" * 2500, files=[_fake_file()])

        paste_service.upload_markdown.assert_awaited_once()
        self.assertIn("https://paste.jobe.wtf/files.md", channel.send.await_args.kwargs["content"])

    async def test_with_files_handles_paste_failure(self):
        paste_service = MagicMock()
        paste_service.upload_markdown = AsyncMock(side_effect=RuntimeError("boom"))
        channel = MagicMock()
        channel.send = AsyncMock()
        service = MessageService(paste_service_instance=paste_service)

        await service.send_channel_reply_with_files(channel, "x" * 2500, files=[_fake_file()])
        self.assertIn("problem uploading", channel.send.await_args.kwargs["content"])

    async def test_interaction_followup_sends_text_with_ephemeral_flag(self):
        interaction = MagicMock()
        interaction.followup.send = AsyncMock()
        service = MessageService()

        await service.send_interaction_followup(interaction, "reply")
        interaction.followup.send.assert_awaited_once_with(
            content="reply", files=[], suppress_embeds=True, ephemeral=False
        )

        interaction.followup.send = AsyncMock()
        await service.send_interaction_followup(interaction, "secret", ephemeral=True)
        interaction.followup.send.assert_awaited_once_with(
            content="secret", files=[], suppress_embeds=True, ephemeral=True
        )

    async def test_interaction_followup_uses_paste_and_retries(self):
        paste_service = MagicMock()
        paste_service.upload_markdown = AsyncMock(return_value="https://paste.jobe.wtf/followup.md")
        interaction = MagicMock()
        interaction.followup.send = AsyncMock(side_effect=[FakeHTTPException(500), None])
        service = MessageService(paste_service_instance=paste_service)

        with _patch_http(), _patch_sleep():
            await service.send_interaction_followup(interaction, "x" * 2500)

        paste_service.upload_markdown.assert_awaited_once()
        self.assertEqual(interaction.followup.send.await_count, 2)
        self.assertIn("https://paste.jobe.wtf/followup.md", interaction.followup.send.await_args.kwargs["content"])

    async def test_set_paste_service_and_format_prompt_message_delegate(self):
        service = MessageService()
        paste_service = MagicMock()
        attachment = SimpleNamespace(url="https://cdn.example/file.png")

        service.set_paste_service(paste_service)

        self.assertIs(service._paste_service, paste_service)
        self.assertEqual(service.format_prompt_message("hello"), "> hello")
        self.assertEqual(
            service.format_attachment_message(attachment, "hello"),
            "> https://cdn.example/file.png\n> hello",
        )


if __name__ == "__main__":
    unittest.main()
