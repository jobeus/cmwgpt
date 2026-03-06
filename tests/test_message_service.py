"""Tests for MessageService with injected optional dependencies."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.message_service import MessageService


class FakeHTTPException(Exception):
    def __init__(self, status, headers=None):
        super().__init__(f"http {status}")
        self.status = status
        self.response = SimpleNamespace(headers=headers or {})


class TestMessageService(unittest.IsolatedAsyncioTestCase):
    async def test_long_reply_uses_injected_paste_service(self):
        paste_service = MagicMock()
        paste_service.upload_markdown = AsyncMock(return_value="https://paste.rs/long.md")
        channel = MagicMock()
        channel.send = AsyncMock()
        service = MessageService(paste_service_instance=paste_service)

        await service.send_channel_reply(channel, "x" * 2500)

        paste_service.upload_markdown.assert_awaited_once()
        channel.send.assert_awaited_once()
        sent_message = channel.send.await_args.args[0]
        self.assertIn("too long", sent_message)
        self.assertIn("https://paste.rs/long.md", sent_message)

    async def test_long_reply_without_paste_service_falls_back_cleanly(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        service = MessageService(paste_service_instance=None)

        await service.send_channel_reply(channel, "x" * 2500)

        channel.send.assert_awaited_once()
        sent_message = channel.send.await_args.args[0]
        self.assertIn("over 2000 characters", sent_message.lower())

    async def test_send_channel_reply_retries_rate_limit_with_retry_after(self):
        channel = MagicMock()
        channel.send = AsyncMock(side_effect=[FakeHTTPException(429, {"Retry-After": "0"}), None])
        service = MessageService()

        with patch("src.services.message_service.HTTPException", FakeHTTPException), patch(
            "src.services.message_service.asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            await service.send_channel_reply(channel, "hello")

        self.assertEqual(channel.send.await_count, 2)
        mock_sleep.assert_awaited_once_with(0.0)

    async def test_send_channel_reply_retries_unexpected_then_raises_and_handles_http_errors(self):
        channel = MagicMock()
        channel.send = AsyncMock(side_effect=[RuntimeError("boom"), RuntimeError("boom again"), RuntimeError("boom thrice")])
        service = MessageService()

        with patch("src.services.message_service.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            with self.assertRaises(RuntimeError):
                await service.send_channel_reply(channel, "hello")

        self.assertEqual(mock_sleep.await_count, 2)

        channel.send = AsyncMock(side_effect=FakeHTTPException(500))
        with patch("src.services.message_service.HTTPException", FakeHTTPException):
            with self.assertRaises(FakeHTTPException):
                await service.send_channel_reply(channel, "hello")

    async def test_send_channel_reply_retries_rate_limit_without_retry_after_then_raises(self):
        channel = MagicMock()
        channel.send = AsyncMock(side_effect=[FakeHTTPException(429), FakeHTTPException(429), FakeHTTPException(429)])
        service = MessageService()

        with patch("src.services.message_service.HTTPException", FakeHTTPException), patch(
            "src.services.message_service.asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            with self.assertRaises(FakeHTTPException):
                await service.send_channel_reply(channel, "hello")

        self.assertEqual(mock_sleep.await_count, 2)

    async def test_send_channel_reply_handles_forbidden_and_not_found(self):
        service = MessageService()

        forbidden = type("FakeForbidden", (Exception,), {})
        not_found = type("FakeNotFound", (Exception,), {})

        channel = MagicMock()
        channel.send = AsyncMock(side_effect=forbidden("nope"))
        with patch("src.services.message_service.Forbidden", forbidden), patch("src.services.message_service.NotFound", not_found):
            with self.assertRaises(forbidden):
                await service.send_channel_reply(channel, "hello")

        channel.send = AsyncMock(side_effect=not_found("gone"))
        with patch("src.services.message_service.Forbidden", forbidden), patch("src.services.message_service.NotFound", not_found):
            with self.assertRaises(not_found):
                await service.send_channel_reply(channel, "hello")

    async def test_send_interaction_followup_sends_short_content_directly(self):
        interaction = MagicMock()
        interaction.followup.send = AsyncMock()
        service = MessageService()

        await service.send_interaction_followup(interaction, "> prompt", "reply")

        interaction.followup.send.assert_awaited_once_with(
            content="> prompt\nreply", suppress_embeds=True
        )

    async def test_send_interaction_followup_handles_paste_failure_cleanly(self):
        paste_service = MagicMock()
        paste_service.upload_markdown = AsyncMock(side_effect=RuntimeError("boom"))
        interaction = MagicMock()
        interaction.followup.send = AsyncMock()
        service = MessageService(paste_service_instance=paste_service)

        await service.send_interaction_followup(interaction, "> prompt", "x" * 2500)

        interaction.followup.send.assert_awaited_once()
        sent_message = interaction.followup.send.await_args.kwargs["content"]
        self.assertIn("problem uploading", sent_message)

    async def test_send_interaction_followup_retries_rate_limit_with_retry_after(self):
        interaction = MagicMock()
        interaction.followup.send = AsyncMock(side_effect=[FakeHTTPException(429, {"Retry-After": "0"}), None])
        service = MessageService()

        with patch("src.services.message_service.HTTPException", FakeHTTPException), patch(
            "src.services.message_service.asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            await service.send_interaction_followup(interaction, "> prompt", "reply")

        self.assertEqual(interaction.followup.send.await_count, 2)
        mock_sleep.assert_awaited_once_with(0.0)

    async def test_send_interaction_followup_retries_without_retry_after_then_raises(self):
        interaction = MagicMock()
        interaction.followup.send = AsyncMock(side_effect=[FakeHTTPException(429), FakeHTTPException(429), FakeHTTPException(429)])
        service = MessageService()

        with patch("src.services.message_service.HTTPException", FakeHTTPException), patch(
            "src.services.message_service.asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            with self.assertRaises(FakeHTTPException):
                await service.send_interaction_followup(interaction, "> prompt", "reply")

        self.assertEqual(mock_sleep.await_count, 2)

    async def test_send_interaction_followup_handles_non_429_http_and_other_retries(self):
        interaction = MagicMock()
        interaction.followup.send = AsyncMock(side_effect=FakeHTTPException(500))
        service = MessageService()

        with patch("src.services.message_service.HTTPException", FakeHTTPException):
            with self.assertRaises(FakeHTTPException):
                await service.send_interaction_followup(interaction, "> prompt", "reply")

        interaction.followup.send = AsyncMock(side_effect=[RuntimeError("boom"), RuntimeError("boom2"), RuntimeError("boom3")])
        with patch("src.services.message_service.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            with self.assertRaises(RuntimeError):
                await service.send_interaction_followup(interaction, "> prompt", "reply")
        self.assertEqual(mock_sleep.await_count, 2)

    async def test_send_interaction_followup_handles_forbidden_and_not_found(self):
        service = MessageService()
        forbidden = type("FakeForbidden", (Exception,), {})
        not_found = type("FakeNotFound", (Exception,), {})
        interaction = MagicMock()

        interaction.followup.send = AsyncMock(side_effect=forbidden("nope"))
        with patch("src.services.message_service.Forbidden", forbidden), patch("src.services.message_service.NotFound", not_found):
            with self.assertRaises(forbidden):
                await service.send_interaction_followup(interaction, "> prompt", "reply")

        interaction.followup.send = AsyncMock(side_effect=not_found("gone"))
        with patch("src.services.message_service.Forbidden", forbidden), patch("src.services.message_service.NotFound", not_found):
            with self.assertRaises(not_found):
                await service.send_interaction_followup(interaction, "> prompt", "reply")

    async def test_send_interaction_followup_with_files_retries_rate_limit(self):
        interaction = MagicMock()
        interaction.followup.send = AsyncMock(side_effect=[FakeHTTPException(429), None])
        service = MessageService()

        with patch("src.services.message_service.HTTPException", FakeHTTPException), patch(
            "src.services.message_service.asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            await service.send_interaction_followup_with_files(
                interaction,
                "> prompt",
                "reply",
                files=["file-a"],
            )

        self.assertEqual(interaction.followup.send.await_count, 2)
        mock_sleep.assert_awaited_once_with(1.0)

    async def test_send_interaction_followup_with_files_handles_paste_failure_and_rate_limit_exhaustion(self):
        paste_service = MagicMock()
        paste_service.upload_markdown = AsyncMock(side_effect=RuntimeError("boom"))
        interaction = MagicMock()
        interaction.followup.send = AsyncMock()
        service = MessageService(paste_service_instance=paste_service)

        await service.send_interaction_followup_with_files(interaction, "> prompt", "x" * 2500, files=["file-a"])
        self.assertIn("problem uploading", interaction.followup.send.await_args.kwargs["content"])

        interaction.followup.send = AsyncMock(side_effect=[FakeHTTPException(429), FakeHTTPException(429), FakeHTTPException(429)])
        with patch("src.services.message_service.HTTPException", FakeHTTPException), patch(
            "src.services.message_service.asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            with self.assertRaises(FakeHTTPException):
                await service.send_interaction_followup_with_files(interaction, "> prompt", "reply", files=["file-a"])

        self.assertEqual(mock_sleep.await_count, 2)

    async def test_send_interaction_followup_with_files_uses_empty_files_and_handles_non_413_errors(self):
        interaction = MagicMock()
        interaction.followup.send = AsyncMock()
        service = MessageService()

        await service.send_interaction_followup_with_files(interaction, "> prompt", "reply")
        self.assertEqual(interaction.followup.send.await_args.kwargs["files"], [])

        interaction.followup.send = AsyncMock(side_effect=FakeHTTPException(500))
        with patch("src.services.message_service.HTTPException", FakeHTTPException):
            with self.assertRaises(FakeHTTPException):
                await service.send_interaction_followup_with_files(interaction, "> prompt", "reply")

    async def test_send_interaction_followup_with_files_handles_forbidden_not_found_and_unexpected_raise(self):
        service = MessageService()
        forbidden = type("FakeForbidden", (Exception,), {})
        not_found = type("FakeNotFound", (Exception,), {})

        interaction = MagicMock()
        interaction.followup.send = AsyncMock(side_effect=forbidden("nope"))
        with patch("src.services.message_service.Forbidden", forbidden), patch("src.services.message_service.NotFound", not_found):
            with self.assertRaises(forbidden):
                await service.send_interaction_followup_with_files(interaction, "> prompt", "reply", files=["file-a"])

        interaction.followup.send = AsyncMock(side_effect=not_found("gone"))
        with patch("src.services.message_service.Forbidden", forbidden), patch("src.services.message_service.NotFound", not_found):
            with self.assertRaises(not_found):
                await service.send_interaction_followup_with_files(interaction, "> prompt", "reply", files=["file-a"])

        interaction.followup.send = AsyncMock(side_effect=[RuntimeError("boom"), RuntimeError("boom2"), RuntimeError("boom3")])
        with patch("src.services.message_service.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            with self.assertRaises(RuntimeError):
                await service.send_interaction_followup_with_files(interaction, "> prompt", "reply", files=["file-a"])
        self.assertEqual(mock_sleep.await_count, 2)

    async def test_send_interaction_followup_with_files_handles_payload_too_large(self):
        interaction = MagicMock()
        interaction.followup.send = AsyncMock(side_effect=[FakeHTTPException(413), None])
        service = MessageService()

        with patch("src.services.message_service.HTTPException", FakeHTTPException):
            await service.send_interaction_followup_with_files(
                interaction,
                "> prompt",
                "reply",
                files=["file-a"],
            )

        self.assertEqual(interaction.followup.send.await_count, 2)
        error_call = interaction.followup.send.await_args_list[-1]
        self.assertIn("too large", error_call.kwargs["content"].lower())

    async def test_send_channel_reply_with_files_handles_payload_too_large(self):
        channel = MagicMock()
        channel.send = AsyncMock(side_effect=[FakeHTTPException(413), None])
        service = MessageService()

        with patch("src.services.message_service.HTTPException", FakeHTTPException):
            await service.send_channel_reply_with_files(channel, "reply", files=["file-a"])

        self.assertEqual(channel.send.await_count, 2)
        error_call = channel.send.await_args_list[-1]
        self.assertIn("too large", error_call.kwargs["content"].lower())

    async def test_send_channel_reply_with_files_retries_rate_limit(self):
        channel = MagicMock()
        channel.send = AsyncMock(side_effect=[FakeHTTPException(429), None])
        service = MessageService()

        with patch("src.services.message_service.HTTPException", FakeHTTPException), patch(
            "src.services.message_service.asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            await service.send_channel_reply_with_files(channel, "reply", files=["file-a"])

        self.assertEqual(channel.send.await_count, 2)
        mock_sleep.assert_awaited_once_with(1.0)

    async def test_send_channel_reply_with_files_uses_empty_files_and_handles_non_413_errors(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        service = MessageService()

        await service.send_channel_reply_with_files(channel, "reply")
        self.assertEqual(channel.send.await_args.kwargs["files"], [])

        channel.send = AsyncMock(side_effect=FakeHTTPException(500))
        with patch("src.services.message_service.HTTPException", FakeHTTPException):
            with self.assertRaises(FakeHTTPException):
                await service.send_channel_reply_with_files(channel, "reply", files=["file-a"])

    async def test_send_channel_reply_with_files_handles_forbidden_not_found_and_unexpected_raise(self):
        service = MessageService()
        forbidden = type("FakeForbidden", (Exception,), {})
        not_found = type("FakeNotFound", (Exception,), {})

        channel = MagicMock()
        channel.send = AsyncMock(side_effect=forbidden("nope"))
        with patch("src.services.message_service.Forbidden", forbidden), patch("src.services.message_service.NotFound", not_found):
            with self.assertRaises(forbidden):
                await service.send_channel_reply_with_files(channel, "reply", files=["file-a"])

        channel.send = AsyncMock(side_effect=not_found("gone"))
        with patch("src.services.message_service.Forbidden", forbidden), patch("src.services.message_service.NotFound", not_found):
            with self.assertRaises(not_found):
                await service.send_channel_reply_with_files(channel, "reply", files=["file-a"])

        channel.send = AsyncMock(side_effect=[RuntimeError("boom"), RuntimeError("boom2"), RuntimeError("boom3")])
        with patch("src.services.message_service.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            with self.assertRaises(RuntimeError):
                await service.send_channel_reply_with_files(channel, "reply", files=["file-a"])
        self.assertEqual(mock_sleep.await_count, 2)

    async def test_send_interaction_followup_with_files_retries_unexpected_errors(self):
        interaction = MagicMock()
        interaction.followup.send = AsyncMock(side_effect=[RuntimeError("boom"), None])
        service = MessageService()

        with patch("src.services.message_service.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            await service.send_interaction_followup_with_files(
                interaction,
                "> prompt",
                "reply",
                files=["file-a"],
            )

        self.assertEqual(interaction.followup.send.await_count, 2)
        mock_sleep.assert_awaited_once_with(1.0)

    async def test_send_channel_reply_with_files_uses_paste_service_for_long_messages(self):
        paste_service = MagicMock()
        paste_service.upload_markdown = AsyncMock(return_value="https://paste.rs/files.md")
        channel = MagicMock()
        channel.send = AsyncMock()
        service = MessageService(paste_service_instance=paste_service)

        await service.send_channel_reply_with_files(channel, "x" * 2500, files=["file-a"])

        paste_service.upload_markdown.assert_awaited_once()
        channel.send.assert_awaited_once()
        sent_message = channel.send.await_args.kwargs["content"]
        self.assertIn("https://paste.rs/files.md", sent_message)

    async def test_send_channel_reply_with_files_handles_paste_failure_and_rate_limit_exhaustion(self):
        paste_service = MagicMock()
        paste_service.upload_markdown = AsyncMock(side_effect=RuntimeError("boom"))
        channel = MagicMock()
        channel.send = AsyncMock()
        service = MessageService(paste_service_instance=paste_service)

        await service.send_channel_reply_with_files(channel, "x" * 2500, files=["file-a"])
        self.assertIn("problem uploading", channel.send.await_args.kwargs["content"])

        channel.send = AsyncMock(side_effect=[FakeHTTPException(429), FakeHTTPException(429), FakeHTTPException(429)])
        with patch("src.services.message_service.HTTPException", FakeHTTPException), patch(
            "src.services.message_service.asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            with self.assertRaises(FakeHTTPException):
                await service.send_channel_reply_with_files(channel, "reply", files=["file-a"])

        self.assertEqual(mock_sleep.await_count, 2)

    async def test_send_interaction_followup_with_files_uses_paste_service_for_long_messages(self):
        paste_service = MagicMock()
        paste_service.upload_markdown = AsyncMock(return_value="https://paste.rs/followup.md")
        interaction = MagicMock()
        interaction.followup.send = AsyncMock()
        service = MessageService(paste_service_instance=paste_service)

        await service.send_interaction_followup_with_files(
            interaction,
            "> prompt",
            "x" * 2500,
            files=["file-a"],
        )

        paste_service.upload_markdown.assert_awaited_once()
        sent_message = interaction.followup.send.await_args.kwargs["content"]
        self.assertIn("https://paste.rs/followup.md", sent_message)

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