"""
Unit tests for the Death Service.
Tests HTML parsing, name diffing, pageview threshold, announcements, and state persistence.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch, mock_open
import asyncio
import os
import sys
from types import SimpleNamespace

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

        # Remove any leftover test state file
        try:
            os.remove("/tmp/_test_death_names.json")
        except FileNotFoundError:
            pass

        from src.services.death_service import DeathService
        self.state_service = MagicMock()
        self.state_service.get_death_settings.return_value = None
        self.service = DeathService(
            state_service=self.state_service,
            state_file="/tmp/_test_death_names.json",
            death_channel_id="12345",
        )

        self.mock_bot = MagicMock()
        self.mock_bot.user = MagicMock()
        self.mock_bot.user.id = 99999
        self.service.set_bot(self.mock_bot)

    def tearDown(self):
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

            result = await self.service.get_avg_monthly_views(
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

            result = await self.service.get_avg_monthly_views(
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

            result = await self.service.get_avg_monthly_views(
                "Missing_Person", mock_session
            )
            self.assertEqual(result, 0)

        self.loop.run_until_complete(run_test())

    # ----- Announcement -----

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

    def test_announce_skips_when_channel_not_found(self):
        """Should log error when channel doesn't exist."""
        async def run_test():
            self.mock_bot.get_channel.return_value = None
            # Should not raise
            await self.service._announce("Ghost", "Ghost", 5_000_000)

        self.loop.run_until_complete(run_test())

    # ----- Force announce -----

    def test_parse_article_title(self):
        from src.services.death_service import parse_article_title
        self.assertEqual(
            parse_article_title("https://en.wikipedia.org/wiki/Kevin_Keegan"),
            "Kevin_Keegan",
        )
        self.assertEqual(
            parse_article_title("https://en.wikipedia.org/wiki/Ren%C3%A9_Dupont"),
            "René_Dupont",
        )
        self.assertEqual(parse_article_title("kevin keegan"), "Kevin_keegan")
        self.assertEqual(parse_article_title("  "), "")
        self.assertEqual(parse_article_title(""), "")

    def test_force_announce_calls_announce(self):
        """force_announce bypasses the threshold and announces, without touching dedup state."""
        async def run_test():
            with patch.object(self.service, "_get_session", AsyncMock(return_value=MagicMock())), \
                patch.object(self.service, "get_avg_monthly_views", AsyncMock(return_value=123)), \
                patch.object(self.service, "_announce", AsyncMock()) as mock_announce:
                ok, msg = await self.service.force_announce(
                    "https://en.wikipedia.org/wiki/Kevin_Keegan"
                )

            self.assertTrue(ok)
            mock_announce.assert_awaited_once_with("Kevin Keegan", "Kevin_Keegan", 123)
            # Force testing must not pollute the real dedup set.
            self.assertNotIn("Kevin_Keegan", self.service._announced_titles)

        self.loop.run_until_complete(run_test())

    def test_force_announce_rejects_bad_input(self):
        async def run_test():
            with patch.object(self.service, "_announce", AsyncMock()) as mock_announce:
                ok, msg = await self.service.force_announce("   ")
            self.assertFalse(ok)
            mock_announce.assert_not_awaited()

        self.loop.run_until_complete(run_test())

    def test_force_announce_requires_channel(self):
        async def run_test():
            from src.services.death_service import DeathService
            svc = DeathService(
                state_service=self.state_service,
                state_file="/tmp/_test_death_names.json",
                death_channel_id="",
            )
            ok, msg = await svc.force_announce("https://en.wikipedia.org/wiki/Kevin_Keegan")
            self.assertFalse(ok)
            self.assertIn("DEATH_CHANNEL_ID", msg)

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
        new_service = DeathService(
            state_service=self.state_service,
            state_file="/tmp/_test_death_names.json",
            death_channel_id="12345",
        )
        self.assertEqual(new_service._known_names, self.service._known_names)

    def test_load_state_sets_first_poll_false(self):
        """If state was loaded, first_poll should be False after start."""
        self.service._known_names = {("Test", "Test")}
        self.service._save_state()

        from src.services.death_service import DeathService
        new_service = DeathService(
            state_service=self.state_service,
            state_file="/tmp/_test_death_names.json",
            death_channel_id="12345",
        )
        # Simulate start() without actually creating the asyncio task
        if new_service._known_names:
            new_service._first_poll = False
        self.assertFalse(new_service._first_poll)

    def test_load_state_missing_file(self):
        """Should handle missing state file gracefully."""
        from src.services.death_service import DeathService
        service = DeathService(
            state_service=self.state_service,
            state_file="/tmp/_missing_death_names.json",
            death_channel_id="12345",
        )
        self.assertEqual(service._known_names, set())
        self.assertTrue(service._first_poll)

    # ----- Start/stop -----

    def test_start_disabled_without_channel_id(self):
        """Service should not start if DEATH_CHANNEL_ID is empty."""
        from src.services.death_service import DeathService

        service = DeathService(
            state_service=self.state_service,
            state_file="/tmp/_test_death_names.json",
            death_channel_id="",
        )
        service.start()
        self.assertFalse(service._running)

    def test_start_and_stop(self):
        """Service should start and stop cleanly."""
        async def run_test():
            self.service.start()
            self.assertTrue(self.service._running)
            self.assertIsNotNone(self.service._task)

            self.service.stop()
            self.assertFalse(self.service._running)

        self.loop.run_until_complete(run_test())

    def test_get_setting_and_get_session_reuse(self):
        self.state_service.get_death_settings.return_value = {"interval": 9}
        self.assertEqual(self.service._get_setting("interval", 15), 9)
        self.assertEqual(self.service._get_setting("missing", 15), 15)

        async def run_test():
            session_one = await self.service._get_session()
            session_two = await self.service._get_session()
            self.assertIs(session_one, session_two)
            await session_one.close()

        self.loop.run_until_complete(run_test())

    def test_public_get_session_returns_shared_session(self):
        async def run_test():
            session_public = await self.service.get_session()
            session_private = await self.service._get_session()
            self.assertIs(session_public, session_private)
            await session_public.close()

        self.loop.run_until_complete(run_test())

    def test_stop_closes_session_even_when_never_started(self):
        """Sessions created outside the poll loop (e.g. /wikicount) are closed by stop()."""
        async def run_test():
            session = await self.service.get_session()
            self.assertFalse(session.closed)

            # Service was never started (like when DEATH_CHANNEL_ID is unset)
            self.assertFalse(self.service._running)
            self.service.stop()
            self.assertIsNone(self.service._session)

            # Let the scheduled close task run
            await asyncio.sleep(0.01)
            self.assertTrue(session.closed)

        self.loop.run_until_complete(run_test())

    def test_stop_closes_session_without_running_loop(self):
        fake_session = SimpleNamespace(closed=False, close=AsyncMock())
        self.service._session = fake_session

        self.service.stop()

        fake_session.close.assert_awaited_once_with()
        self.assertIsNone(self.service._session)

    def test_start_already_running_and_stop_when_not_running(self):
        def fake_create_task(coro):
            coro.close()
            task = MagicMock()
            task.done.return_value = False
            return task

        with patch("src.services.death_service.asyncio.create_task", side_effect=fake_create_task):
            self.service.start()
            first_task = self.service._task
            self.service.start()
        self.assertIs(self.service._task, first_task)

        self.service.stop()
        self.service.stop()

    def test_save_and_load_state_handle_errors(self):
        self.service._known_names = {("John Doe", "John_Doe")}

        with patch("builtins.open", side_effect=OSError("denied")):
            self.service._save_state()

        with patch("os.path.exists", return_value=True), patch(
            "builtins.open", mock_open(read_data="{bad json")
        ):
            self.service._known_names = set()
            self.service._load_state()
            self.assertEqual(self.service._known_names, set())

    def test_poll_once_sets_baseline_and_handles_status_and_new_names(self):
        async def run_test():
            async def fake_views(article_title, session):
                if article_title == "New_One":
                    return 200000
                if article_title == "Low_Views":
                    return 1000
                if article_title == "No_Data":
                    return None
                if article_title == "Boom":
                    raise RuntimeError("boom")
                raise AssertionError(f"unexpected article {article_title}")

            bad_resp = AsyncMock()
            bad_resp.status = 500
            ok_resp = AsyncMock()
            ok_resp.status = 200
            ok_resp.text = AsyncMock(return_value=SAMPLE_HTML)

            session = MagicMock()
            session.get = MagicMock(side_effect=[
                AsyncMock(__aenter__=AsyncMock(return_value=bad_resp), __aexit__=AsyncMock(return_value=False)),
                AsyncMock(__aenter__=AsyncMock(return_value=ok_resp), __aexit__=AsyncMock(return_value=False)),
                AsyncMock(__aenter__=AsyncMock(return_value=ok_resp), __aexit__=AsyncMock(return_value=False)),
            ])

            with patch.object(self.service, "_get_session", AsyncMock(return_value=session)), patch.object(
                self.service, "_save_state"
            ) as mock_save, patch.object(
                self.service, "get_avg_monthly_views", AsyncMock(side_effect=fake_views)
            ) as mock_views, patch.object(
                self.service, "_announce", AsyncMock()
            ) as mock_announce, patch(
                "src.services.death_service.parse_deaths_html",
                side_effect=[
                    [("John Doe", "John_Doe")],
                    [
                        ("John Doe", "John_Doe"),
                        ("New One", "New_One"),
                        ("Low Views", "Low_Views"),
                        ("No Data", "No_Data"),
                        ("Boom", "Boom"),
                    ],
                ],
            ):
                await self.service._poll_once()
                self.assertTrue(self.service._first_poll)

                await self.service._poll_once()
                self.assertFalse(self.service._first_poll)
                self.assertEqual(self.service._known_names, {("John Doe", "John_Doe")})

                await self.service._poll_once()

            # One save for the known-names baseline update, one for the
            # announced-set after "New One" was announced.
            self.assertEqual(mock_save.call_count, 2)
            self.assertEqual(mock_views.await_count, 4)
            mock_announce.assert_awaited_once_with("New One", "New_One", 200000)
            self.assertIn("New_One", self.service._announced_titles)

        self.loop.run_until_complete(run_test())

    def test_already_announced_name_not_reannounced(self):
        """A name that reappears after being announced is never announced again."""
        async def run_test():
            self.service._first_poll = False
            self.service._known_names = set()
            self.service._announced_titles = {"Famous_One"}

            session = MagicMock()
            ok_resp = AsyncMock()
            ok_resp.status = 200
            ok_resp.text = AsyncMock(return_value=SAMPLE_HTML)
            session.get = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=ok_resp),
                __aexit__=AsyncMock(return_value=False),
            ))

            with patch.object(self.service, "_get_session", AsyncMock(return_value=session)), \
                patch.object(self.service, "_save_state"), \
                patch.object(self.service, "get_avg_monthly_views", AsyncMock(return_value=5_000_000)) as mock_views, \
                patch.object(self.service, "_announce", AsyncMock()) as mock_announce, \
                patch(
                    "src.services.death_service.parse_deaths_html",
                    return_value=[("Famous One", "Famous_One")],
                ):
                await self.service._poll_once()

            # Already announced → we never even fetch views or announce again.
            mock_views.assert_not_awaited()
            mock_announce.assert_not_awaited()

        self.loop.run_until_complete(run_test())

    def test_announce_appends_model_summary(self):
        """The announcement includes the model's 'known for' summary when available."""
        async def run_test():
            mock_openai = MagicMock()
            mock_openai.get_chat_completion = AsyncMock(
                return_value=("Legendary footballer and manager.", 0.0)
            )
            self.service._openai_service = mock_openai
            self.service._model = "anthropic/claude-sonnet-5"

            mock_channel = AsyncMock()
            self.mock_bot.get_channel.return_value = mock_channel

            await self.service._announce("Kevin Keegan", "Kevin_Keegan", 2_000_000)

            # Two separate sends: the RIP line with the URL as-is, then the
            # AI summary as a follow-up.
            self.assertEqual(mock_channel.send.call_count, 2)
            first = mock_channel.send.call_args_list[0][0][0]
            second = mock_channel.send.call_args_list[1][0][0]
            self.assertEqual(
                first, "RIP Kevin Keegan - https://en.wikipedia.org/wiki/Kevin_Keegan"
            )
            self.assertEqual(second, "Legendary footballer and manager.")

            # The model was prompted with the name and wiki link.
            prompt = mock_openai.get_chat_completion.call_args.kwargs["messages"][0]["content"]
            self.assertIn("Kevin Keegan", prompt)
            self.assertIn("https://en.wikipedia.org/wiki/Kevin_Keegan", prompt)

        self.loop.run_until_complete(run_test())

    def test_write_limerick_returns_model_output(self):
        """write_limerick prompts with the name and wiki link, returns the verse."""
        async def run_test():
            mock_openai = MagicMock()
            mock_openai.get_chat_completion = AsyncMock(
                return_value=("There once was a keeper named Kev...", 0.0)
            )
            self.service._openai_service = mock_openai
            self.service._model = "anthropic/claude-sonnet-5"

            ok, message = await self.service.write_limerick("Kevin Keegan")

            self.assertTrue(ok)
            self.assertEqual(message, "There once was a keeper named Kev...")
            prompt = mock_openai.get_chat_completion.call_args.kwargs["messages"][0]["content"]
            self.assertIn("Kevin Keegan", prompt)
            self.assertIn("https://en.wikipedia.org/wiki/Kevin_Keegan", prompt)
            self.assertIn("limerick", prompt.lower())

        self.loop.run_until_complete(run_test())

    def test_write_limerick_without_model_configured(self):
        async def run_test():
            ok, message = await self.service.write_limerick("Kevin Keegan")
            self.assertFalse(ok)
            self.assertIn("No chat model", message)

        self.loop.run_until_complete(run_test())

    def test_write_limerick_rejects_empty_input(self):
        async def run_test():
            self.service._openai_service = MagicMock()
            self.service._model = "anthropic/claude-sonnet-5"

            ok, message = await self.service.write_limerick("   ")
            self.assertFalse(ok)
            self.assertIn("Could not determine a person", message)

        self.loop.run_until_complete(run_test())

    def test_write_limerick_survives_model_failure(self):
        async def run_test():
            mock_openai = MagicMock()
            mock_openai.get_chat_completion = AsyncMock(side_effect=RuntimeError("boom"))
            self.service._openai_service = mock_openai
            self.service._model = "anthropic/claude-sonnet-5"

            ok, message = await self.service.write_limerick("Kevin Keegan")
            self.assertFalse(ok)
            self.assertIn("failed to produce a limerick", message)

        self.loop.run_until_complete(run_test())

    def test_announce_survives_summary_failure(self):
        """A failing summary call still sends the plain RIP message."""
        async def run_test():
            mock_openai = MagicMock()
            mock_openai.get_chat_completion = AsyncMock(side_effect=RuntimeError("boom"))
            self.service._openai_service = mock_openai
            self.service._model = "anthropic/claude-sonnet-5"

            mock_channel = AsyncMock()
            self.mock_bot.get_channel.return_value = mock_channel

            await self.service._announce("Kevin Keegan", "Kevin_Keegan", 2_000_000)

            sent = mock_channel.send.call_args[0][0]
            self.assertEqual(sent, "RIP Kevin Keegan - https://en.wikipedia.org/wiki/Kevin_Keegan")

        self.loop.run_until_complete(run_test())

    def test_legacy_list_state_seeds_announced(self):
        """Old list-format state files migrate: known names seed the announced-set."""
        import json as _json
        with open("/tmp/_test_death_names.json", "w", encoding="utf-8") as f:
            _json.dump([["John Doe", "John_Doe"], ["Jane Smith", "Jane_Smith"]], f)

        from src.services.death_service import DeathService
        service = DeathService(
            state_service=self.state_service,
            state_file="/tmp/_test_death_names.json",
            death_channel_id="12345",
        )
        self.assertEqual(
            service._known_names,
            {("John Doe", "John_Doe"), ("Jane Smith", "Jane_Smith")},
        )
        self.assertEqual(service._announced_titles, {"John_Doe", "Jane_Smith"})

    def test_announced_titles_round_trip(self):
        """Announced titles persist and reload alongside known names."""
        self.service._known_names = {("John Doe", "John_Doe")}
        self.service._announced_titles = {"John_Doe"}
        self.service._save_state()

        from src.services.death_service import DeathService
        reloaded = DeathService(
            state_service=self.state_service,
            state_file="/tmp/_test_death_names.json",
            death_channel_id="12345",
        )
        self.assertEqual(reloaded._announced_titles, {"John_Doe"})
        self.assertEqual(reloaded._known_names, {("John Doe", "John_Doe")})

    def testget_avg_monthly_views_handles_empty_items_429_and_other_errors(self):
        async def run_test():
            empty_resp = AsyncMock()
            empty_resp.status = 200
            empty_resp.json = AsyncMock(return_value={"items": []})
            session = MagicMock()
            session.get = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=empty_resp), __aexit__=AsyncMock(return_value=False)))
            self.assertEqual(await self.service.get_avg_monthly_views("Missing", session), 0)

            rate_resp = AsyncMock()
            rate_resp.status = 429
            other_resp = AsyncMock()
            other_resp.status = 500
            other_resp.text = AsyncMock(return_value="oops")
            session = MagicMock()
            session.get = MagicMock(side_effect=[
                AsyncMock(__aenter__=AsyncMock(return_value=rate_resp), __aexit__=AsyncMock(return_value=False)),
                AsyncMock(__aenter__=AsyncMock(return_value=rate_resp), __aexit__=AsyncMock(return_value=False)),
                AsyncMock(__aenter__=AsyncMock(return_value=rate_resp), __aexit__=AsyncMock(return_value=False)),
            ])
            with patch("src.services.death_service.asyncio.sleep", new=AsyncMock()) as mock_sleep:
                self.assertIsNone(await self.service.get_avg_monthly_views("Rate Limited", session))
            self.assertEqual(mock_sleep.await_count, 3)

            session = MagicMock()
            session.get = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=other_resp), __aexit__=AsyncMock(return_value=False)))
            self.assertIsNone(await self.service.get_avg_monthly_views("Oops", session))

        self.loop.run_until_complete(run_test())

    def test_announce_handles_missing_bot_and_http_error(self):
        async def run_test():
            self.service._bot = None
            await self.service._announce("Ghost", "Ghost", 1)

            channel = AsyncMock()
            channel.send = AsyncMock(side_effect=Exception("send failed"))
            self.service._bot = MagicMock(get_channel=MagicMock(return_value=channel))
            with patch("src.services.death_service.discord.HTTPException", Exception):
                await self.service._announce("Ghost", "Ghost", 1)

        self.loop.run_until_complete(run_test())

    def test_poll_loop_handles_errors_and_closes_session(self):
        async def run_test():
            self.service._running = True
            self.service._session = SimpleNamespace(closed=False, close=AsyncMock())

            async def fake_poll_once():
                self.service._running = False
                raise RuntimeError("boom")

            self.service._poll_once = fake_poll_once

            with patch("src.services.death_service.asyncio.sleep", new=AsyncMock()):
                await self.service._poll_loop()

            self.service._session.close.assert_awaited_once()

        self.loop.run_until_complete(run_test())


if __name__ == "__main__":
    unittest.main()
