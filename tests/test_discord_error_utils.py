"""Tests for Discord error-handling helpers."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from src.utils.discord_error_utils import (
    best_effort_typing,
    concise_error,
    is_discord_server_error,
    safe_defer,
)


def make_http_exception(status, message="", exc_class=discord.HTTPException):
    response = SimpleNamespace(status=status, reason="Internal Server Error")
    return exc_class(response, message)


CLOUDFLARE_HTML = (
    "<html>\n  <head>\n    <title>Internal Server Error</title>\n  </head>\n"
    "  <body>\n    <h1><p>Internal Server Error</p></h1>\n"
    "  <script>(function(){var a=document.createElement('iframe');})();</script></body>\n</html>"
)


class TestConciseError(unittest.TestCase):
    def test_strips_html_error_page(self):
        error = make_http_exception(500, CLOUDFLARE_HTML, discord.DiscordServerError)
        result = concise_error(error)
        self.assertIn("DiscordServerError", result)
        self.assertIn("500 Internal Server Error", result)
        self.assertIn("[HTML error page omitted]", result)
        self.assertNotIn("<html", result.lower())
        self.assertNotIn("script", result)

    def test_strips_doctype_pages_case_insensitively(self):
        error = ValueError("bad response: <!DOCTYPE html><html><body>nope</body></html>")
        result = concise_error(error)
        self.assertNotIn("<html", result.lower())
        self.assertIn("[HTML error page omitted]", result)

    def test_truncates_long_messages(self):
        error = ValueError("x" * 1000)
        result = concise_error(error)
        self.assertLess(len(result), 250)
        self.assertTrue(result.endswith("…"))

    def test_collapses_whitespace(self):
        error = ValueError("line one\n\n   line two")
        self.assertEqual(concise_error(error), "ValueError: line one line two")

    def test_empty_message_falls_back_to_class_name(self):
        self.assertEqual(concise_error(ValueError()), "ValueError")


class TestIsDiscordServerError(unittest.TestCase):
    def test_true_for_5xx(self):
        error = make_http_exception(500, "boom", discord.DiscordServerError)
        self.assertTrue(is_discord_server_error(error))

    def test_false_for_rate_limit(self):
        error = make_http_exception(429, "slow down")
        self.assertFalse(is_discord_server_error(error))

    def test_false_for_non_discord_errors(self):
        self.assertFalse(is_discord_server_error(ValueError("boom")))


class TestSafeDefer(unittest.IsolatedAsyncioTestCase):
    def make_interaction(self):
        interaction = MagicMock()
        interaction.response = AsyncMock()
        interaction.command.name = "limerick"
        return interaction

    async def test_returns_true_on_success(self):
        interaction = self.make_interaction()

        result = await safe_defer(interaction, ephemeral=True, thinking=True)

        self.assertTrue(result)
        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)

    async def test_returns_false_when_interaction_expired(self):
        interaction = self.make_interaction()
        interaction.response.defer.side_effect = make_http_exception(
            404, "Unknown interaction", discord.NotFound
        )

        result = await safe_defer(interaction)

        self.assertFalse(result)

    async def test_returns_false_on_other_http_errors(self):
        interaction = self.make_interaction()
        interaction.response.defer.side_effect = make_http_exception(
            500, CLOUDFLARE_HTML, discord.DiscordServerError
        )

        result = await safe_defer(interaction)

        self.assertFalse(result)

    async def test_returns_true_when_already_responded(self):
        interaction = self.make_interaction()
        interaction.response.defer.side_effect = discord.InteractionResponded(interaction)

        result = await safe_defer(interaction)

        self.assertTrue(result)


class TestBestEffortTyping(unittest.IsolatedAsyncioTestCase):
    async def test_enters_and_exits_typing_normally(self):
        typing_ctx = MagicMock()
        typing_ctx.__aenter__ = AsyncMock()
        typing_ctx.__aexit__ = AsyncMock()
        channel = MagicMock()
        channel.typing.return_value = typing_ctx

        ran = False
        async with best_effort_typing(channel):
            ran = True

        self.assertTrue(ran)
        typing_ctx.__aenter__.assert_awaited_once()
        typing_ctx.__aexit__.assert_awaited_once()

    async def test_body_still_runs_when_typing_fails(self):
        typing_ctx = MagicMock()
        typing_ctx.__aenter__ = AsyncMock(
            side_effect=make_http_exception(500, CLOUDFLARE_HTML, discord.DiscordServerError)
        )
        typing_ctx.__aexit__ = AsyncMock()
        channel = MagicMock()
        channel.typing.return_value = typing_ctx

        ran = False
        async with best_effort_typing(channel):
            ran = True

        self.assertTrue(ran)
        typing_ctx.__aexit__.assert_not_awaited()

    async def test_body_exception_still_propagates(self):
        typing_ctx = MagicMock()
        typing_ctx.__aenter__ = AsyncMock()
        typing_ctx.__aexit__ = AsyncMock()
        channel = MagicMock()
        channel.typing.return_value = typing_ctx

        with self.assertRaises(RuntimeError):
            async with best_effort_typing(channel):
                raise RuntimeError("handler blew up")

        typing_ctx.__aexit__.assert_awaited_once()

    async def test_exit_failure_is_swallowed(self):
        typing_ctx = MagicMock()
        typing_ctx.__aenter__ = AsyncMock()
        typing_ctx.__aexit__ = AsyncMock(
            side_effect=make_http_exception(500, "boom", discord.DiscordServerError)
        )
        channel = MagicMock()
        channel.typing.return_value = typing_ctx

        async with best_effort_typing(channel):
            pass

        typing_ctx.__aexit__.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
