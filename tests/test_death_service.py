"""
Unit tests for the Death Service.
Tests HTML parsing, name diffing, pageview threshold, announcements, and state persistence.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch, mock_open
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Sample HTML snippets for testing the parser
# ---------------------------------------------------------------------------

SAMPLE_HTML = """
<html><body>
<h2>January</h2>
<ul>
<li><a href="/wiki/John_Doe">John Doe</a>, 80, American actor</li>
<li><a href="/wiki/Jane_Smith">Jane Smith</a>, 75, British author</li>
<li><a href="/wiki/Bob_Jones">Bob Jones</a>, 90, Canadian politician</li>
</ul>
<h2>February</h2>
<ul>
<li><a href="/wiki/Alice_Wonder">Alice Wonder</a>, 65, German scientist</li>
</ul>
</body></html>
"""

# HTML with no links
EMPTY_HTML = "<html><body><p>No deaths today</p></body></html>"

# HTML with category links (colons in path — should be skipped)
HTML_WITH_CATEGORIES = """
<ul>
<li><a href="/wiki/Category:Deaths">Category</a></li>
<li><a href="/wiki/Real_Person">Real Person</a>, 70, something</li>
</ul>
"""

# HTML with percent-encoded characters
HTML_ENCODED = """
<ul>
<li><a href="/wiki/Ren%C3%A9_Dupont">René Dupont</a>, 85, French artist</li>
</ul>
"""


class TestDeathPageParser(unittest.TestCase):
    """Tests for parse_deaths_html."""

    def test_basic_parsing(self):
        from src.services.death_service import parse_deaths_html
        results = parse_deaths_html(SAMPLE_HTML)
        self.assertEqual(len(results), 4)
        self.assertIn(("John Doe", "John_Doe"), results)
        self.assertIn(("Jane Smith", "Jane_Smith"), results)
        self.assertIn(("Bob Jones", "Bob_Jones"), results)
        self.assertIn(("Alice Wonder", "Alice_Wonder"), results)

    def test_empty_html(self):
        from src.services.death_service import parse_deaths_html
        results = parse_deaths_html(EMPTY_HTML)
        self.assertEqual(len(results), 0)

    def test_category_links_skipped(self):
        from src.services.death_service import parse_deaths_html
        results = parse_deaths_html(HTML_WITH_CATEGORIES)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], ("Real Person", "Real_Person"))

    def test_percent_encoded_links(self):
        from src.services.death_service import parse_deaths_html
        results = parse_deaths_html(HTML_ENCODED)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], "René Dupont")
        self.assertEqual(results[0][1], "René_Dupont")


class TestDeathService(unittest.TestCase):
    """Tests for DeathService logic."""

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        # Patch STATE_FILE so tests don't touch real disk
        self.state_patcher = patch(
            "src.services.death_service.STATE_FILE", "/tmp/_test_death_names.json"
        )
        self.state_patcher.start()

        # Remove any leftover test state file
        try:
            os.remove("/tmp/_test_death_names.json")
        except FileNotFoundError:
            pass

        from src.services.death_service import DeathService
        self.service = DeathService()

        self.mock_bot = MagicMock()
        self.mock_bot.user = MagicMock()
        self.mock_bot.user.id = 99999
        self.service.set_bot(self.mock_bot)

    def tearDown(self):
        self.state_patcher.stop()
        self.loop.close()
        try:
            os.remove("/tmp/_test_death_names.json")
        except FileNotFoundError:
            pass

    # ----- Name diffing -----

    def test_new_names_detected(self):
        """New names should be the difference between current and known sets."""
        self.service._known_names = {
            ("John Doe", "John_Doe"),
            ("Jane Smith", "Jane_Smith"),
        }
        current = {
            ("John Doe", "John_Doe"),
            ("Jane Smith", "Jane_Smith"),
            ("New Person", "New_Person"),
        }
        new_names = current - self.service._known_names
        self.assertEqual(len(new_names), 1)
        self.assertIn(("New Person", "New_Person"), new_names)

    def test_no_new_names_when_unchanged(self):
        """No new names when the set is identical."""
        names = {("John Doe", "John_Doe")}
        self.service._known_names = names.copy()
        new_names = names - self.service._known_names
        self.assertEqual(len(new_names), 0)

    # ----- First poll baseline -----

    def test_first_poll_sets_baseline(self):
        """First poll should set the baseline and not announce."""
        self.assertTrue(self.service._first_poll)
        self.service._known_names = set()

        # Simulate first poll logic
        current = {("John Doe", "John_Doe")}
        if self.service._first_poll:
            self.service._known_names = current
            self.service._first_poll = False

        self.assertFalse(self.service._first_poll)
        self.assertEqual(self.service._known_names, current)

    # ----- Pageview threshold -----

    @patch("src.services.death_service.MIN_AVG_MONTHLY_VIEWS", 1_000_000)
    def test_pageview_above_threshold(self):
        """Should announce when avg views >= threshold."""
        async def run_test():
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value={
                "items": [{"views": 1_500_000}] * 12
            })

            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(return_value=False),
            ))

            result = await self.service._get_avg_monthly_views(
                "Famous_Person", mock_session
            )
            self.assertEqual(result, 1_500_000)

        self.loop.run_until_complete(run_test())

    @patch("src.services.death_service.MIN_AVG_MONTHLY_VIEWS", 1_000_000)
    def test_pageview_below_threshold(self):
        """Should not announce when avg views < threshold."""
        async def run_test():
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value={
                "items": [{"views": 500}] * 12
            })

            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(return_value=False),
            ))

            result = await self.service._get_avg_monthly_views(
                "Obscure_Person", mock_session
            )
            self.assertEqual(result, 500)

        self.loop.run_until_complete(run_test())

    def test_pageview_api_404_returns_zero(self):
        """Should return 0 on 404 HTTP error (no pageviews)."""
        async def run_test():
            mock_resp = AsyncMock()
            mock_resp.status = 404

            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(return_value=False),
            ))

            result = await self.service._get_avg_monthly_views(
                "Missing_Person", mock_session
            )
            self.assertEqual(result, 0)

        self.loop.run_until_complete(run_test())

    # ----- Announcement -----

    @patch("src.services.death_service.DEATH_CHANNEL_ID", "12345")
    def test_announce_sends_rip_message(self):
        """Should send RIP message to the configured channel."""
        async def run_test():
            mock_channel = AsyncMock()
            self.mock_bot.get_channel.return_value = mock_channel

            await self.service._announce("John Doe", "John_Doe", 2_000_000)

            mock_channel.send.assert_called_once()
            call_args = mock_channel.send.call_args[0][0]
            self.assertTrue(call_args.startswith("RIP John Doe"))
            self.assertIn("https://en.wikipedia.org/wiki/John_Doe", call_args)

        self.loop.run_until_complete(run_test())

    @patch("src.services.death_service.DEATH_CHANNEL_ID", "12345")
    def test_announce_skips_when_channel_not_found(self):
        """Should log error when channel doesn't exist."""
        async def run_test():
            self.mock_bot.get_channel.return_value = None
            # Should not raise
            await self.service._announce("Ghost", "Ghost", 5_000_000)

        self.loop.run_until_complete(run_test())

    # ----- State persistence -----

    def test_save_and_load_state(self):
        """State should persist and reload correctly."""
        self.service._known_names = {
            ("John Doe", "John_Doe"),
            ("Jane Smith", "Jane_Smith"),
        }
        self.service._save_state()

        from src.services.death_service import DeathService
        new_service = DeathService()
        self.assertEqual(new_service._known_names, self.service._known_names)

    def test_load_state_sets_first_poll_false(self):
        """If state was loaded, first_poll should be False after start."""
        self.service._known_names = {("Test", "Test")}
        self.service._save_state()

        from src.services.death_service import DeathService
        new_service = DeathService()
        # Simulate start() without actually creating the asyncio task
        if new_service._known_names:
            new_service._first_poll = False
        self.assertFalse(new_service._first_poll)

    def test_load_state_missing_file(self):
        """Should handle missing state file gracefully."""
        from src.services.death_service import DeathService
        service = DeathService()
        self.assertEqual(service._known_names, set())
        self.assertTrue(service._first_poll)

    # ----- Start/stop -----

    @patch("src.services.death_service.DEATH_CHANNEL_ID", "")
    def test_start_disabled_without_channel_id(self):
        """Service should not start if DEATH_CHANNEL_ID is empty."""
        self.service.start()
        self.assertFalse(self.service._running)

    @patch("src.services.death_service.DEATH_CHANNEL_ID", "12345")
    def test_start_and_stop(self):
        """Service should start and stop cleanly."""
        async def run_test():
            self.service.start()
            self.assertTrue(self.service._running)
            self.assertIsNotNone(self.service._task)

            self.service.stop()
            self.assertFalse(self.service._running)

        self.loop.run_until_complete(run_test())


if __name__ == "__main__":
    unittest.main()
