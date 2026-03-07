"""Tests for async fire-and-forget safety helpers."""

import unittest
from unittest.mock import AsyncMock

from src.utils.async_utils import safe_run


class TestAsyncUtils(unittest.IsolatedAsyncioTestCase):
    async def test_safe_run_executes_handler_successfully(self):
        interaction = AsyncMock()
        handler = AsyncMock()

        await safe_run(interaction, handler, 1, named="value")

        handler.assert_awaited_once_with(1, named="value")
        interaction.followup.send.assert_not_awaited()

    async def test_safe_run_reports_handler_failures_via_followup(self):
        interaction = AsyncMock()
        handler = AsyncMock(side_effect=RuntimeError("boom"))

        await safe_run(interaction, handler)

        interaction.followup.send.assert_awaited_once()

        sent_message = interaction.followup.send.await_args.kwargs["content"]
        self.assertIn("unexpected error", sent_message.lower())

    async def test_safe_run_swallows_followup_send_failures(self):
        interaction = AsyncMock()
        interaction.followup.send = AsyncMock(side_effect=RuntimeError("discord down"))
        handler = AsyncMock(side_effect=RuntimeError("boom"))

        await safe_run(interaction, handler)

        interaction.followup.send.assert_awaited_once()
