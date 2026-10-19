"""
Unit tests for the Death Service.
Tests HTML parsing, name diffing, pageview threshold, announcements, and state persistence.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch, mock_open
import asyncio
import os
import sys

import httpx

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

    def test_write_limerick_without_model_configured(self):
        async def run_test():
            ok, message = await self.service.write_limerick("Kevin Keegan")
            self.assertFalse(ok)
            self.assertIn("No chat model", message)

        self.loop.run_until_complete(run_test())

    def test_write_limerick_rejects_empty_input(self):
        async def run_test():
            self.service._openai_service = MagicMock()
            self.service._model = "anthropic/claude-sonnet-5.5"

            ok, message = await self.service.write_limerick("   ")
            self.assertFalse(ok)
            self.assertIn("Could not determine a person", message)

        self.loop.run_until_complete(run_test())

    def test_write_limerick_survives_model_failure(self):
        async def run_test():
            mock_openai = MagicMock()
            mock_openai.get_chat_completion = AsyncMock(side_effect=RuntimeError("boom"))
            self.service._openai_service = mock_openai
            self.service._model = "anthropic/claude-sonnet-5.5"

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
            self.service._model = "anthropic/claude-sonnet-5.5"

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

    # ----- Pageviews (httpx) -----

    def _fake_http(self, *responses):
        """Install a fake shared httpx client whose get() yields ``responses`` in order."""
        http = MagicMock()
        if len(responses) == 1 and not isinstance(responses[0], BaseException):
            http.get = AsyncMock(return_value=responses[0])
        else:
            http.get = AsyncMock(side_effect=list(responses))
        http.aclose = AsyncMock()
        self.service._http = http
        return http

    def test_pageview_average_is_computed(self):
        async def run_test():
            self._fake_http(httpx.Response(200, json={"items": [{"views": 1_500_000}] * 12}))
            self.assertEqual(await self.service.get_avg_monthly_views("Famous_Person"), 1_500_000)

            self._fake_http(httpx.Response(200, json={"items": [{"views": 500}] * 12}))
            self.assertEqual(await self.service.get_avg_monthly_views("Obscure_Person"), 500)

        self.loop.run_until_complete(run_test())

    def test_pageview_url_is_encoded(self):
        async def run_test():
            http = self._fake_http(httpx.Response(200, json={"items": [{"views": 1}]}))
            await self.service.get_avg_monthly_views("René Dupont")
            url = http.get.await_args.args[0]
            self.assertIn("/Ren%C3%A9_Dupont/monthly/", url)

        self.loop.run_until_complete(run_test())

    def test_pageview_api_404_returns_zero(self):
        async def run_test():
            self._fake_http(httpx.Response(404))
            self.assertEqual(await self.service.get_avg_monthly_views("Missing_Person"), 0)

        self.loop.run_until_complete(run_test())

    def test_pageview_api_504_is_retried_until_it_succeeds(self):
        async def run_test():
            http = self._fake_http(
                httpx.Response(504, text='{"httpCode":504,"httpReason":"upstream request timeout"}'),
                httpx.Response(200, json={"items": [{"views": 300}] * 3}),
            )
            with patch("src.services.death_service.asyncio.sleep", new=AsyncMock()):
                result = await self.service.get_avg_monthly_views("Dolly_Parton")
            self.assertEqual(result, 300)
            self.assertEqual(http.get.await_count, 2)

        self.loop.run_until_complete(run_test())

    def test_pageview_network_error_is_retried(self):
        async def run_test():
            self._fake_http(
                httpx.ConnectError("connection reset"),
                httpx.Response(200, json={"items": [{"views": 90}]}),
            )
            with patch("src.services.death_service.asyncio.sleep", new=AsyncMock()):
                self.assertEqual(await self.service.get_avg_monthly_views("Flaky"), 90)

            # Never-recovering network errors give up as unknown, not as zero.
            http = self._fake_http(asyncio.TimeoutError(), asyncio.TimeoutError(), asyncio.TimeoutError())
            with patch("src.services.death_service.asyncio.sleep", new=AsyncMock()):
                self.assertIsNone(await self.service.get_avg_monthly_views("Dead"))
            self.assertEqual(http.get.await_count, 3)

        self.loop.run_until_complete(run_test())

    def test_get_avg_monthly_views_handles_empty_items_429_and_other_errors(self):
        async def run_test():
            self._fake_http(httpx.Response(200, json={"items": []}))
            self.assertEqual(await self.service.get_avg_monthly_views("Missing"), 0)

            http = self._fake_http(*[httpx.Response(429, text="rate limited") for _ in range(3)])
            with patch("src.services.death_service.asyncio.sleep", new=AsyncMock()) as mock_sleep:
                self.assertIsNone(await self.service.get_avg_monthly_views("Rate Limited"))
            # Backoff happens between attempts, not after the last one.
            self.assertEqual(mock_sleep.await_count, 2)
            self.assertEqual(http.get.await_count, 3)

            # 5xx is transient too: retry the full budget, then give up as unknown.
            http = self._fake_http(httpx.Response(500, text="oops"))
            with patch("src.services.death_service.asyncio.sleep", new=AsyncMock()):
                self.assertIsNone(await self.service.get_avg_monthly_views("Oops"))
            self.assertEqual(http.get.await_count, 3)

            # A non-transient 4xx is not worth retrying.
            http = self._fake_http(httpx.Response(400, text="bad title"))
            self.assertIsNone(await self.service.get_avg_monthly_views("Bad"))
            self.assertEqual(http.get.await_count, 1)

        self.loop.run_until_complete(run_test())

    # ----- Polling -----

    def test_unchecked_name_is_announced_on_a_later_poll(self):
        """A name whose views could not be fetched is retried and then announced."""
        async def run_test():
            self.service._first_poll = False
            self.service._known_names = set()
            self._fake_http(httpx.Response(200, text=SAMPLE_HTML))

            with patch.object(self.service, "_save_state"), \
                patch.object(self.service, "_announce", AsyncMock()) as mock_announce, \
                patch.object(
                    self.service,
                    "get_avg_monthly_views",
                    AsyncMock(side_effect=[None, 5_000_000]),
                ), \
                patch(
                    "src.services.death_service.parse_deaths_html",
                    return_value=[("Famous One", "Famous_One")],
                ):
                # First poll: the pageviews API is down.
                await self.service._poll_once()
                mock_announce.assert_not_awaited()
                self.assertEqual(self.service._known_names, set())

                # Second poll: same name is still "new", the API recovers.
                await self.service._poll_once()
                mock_announce.assert_awaited_once_with("Famous One", "Famous_One", 5_000_000)
                self.assertEqual(
                    self.service._known_names, {("Famous One", "Famous_One")}
                )

        self.loop.run_until_complete(run_test())

    def test_poll_once_sets_baseline_and_handles_status_and_new_names(self):
        async def run_test():
            async def fake_views(article_title):
                if article_title == "New_One":
                    return 200000
                if article_title == "Low_Views":
                    return 1000
                if article_title == "No_Data":
                    return None
                if article_title == "Boom":
                    raise RuntimeError("boom")
                raise AssertionError(f"unexpected article {article_title}")

            self._fake_http(
                httpx.Response(500),
                httpx.Response(200, text=SAMPLE_HTML),
                httpx.Response(200, text=SAMPLE_HTML),
            )

            with patch.object(self.service, "_save_state") as mock_save, patch.object(
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

            # One save covering the known-names and announced-set updates.
            self.assertEqual(mock_save.call_count, 1)
            self.assertEqual(mock_views.await_count, 4)
            mock_announce.assert_awaited_once_with("New One", "New_One", 200000)
            self.assertIn("New_One", self.service._announced_titles)
            # "No Data" (views unavailable) and "Boom" (lookup raised) must not
            # be recorded as seen, so the next poll retries them.
            self.assertEqual(
                self.service._known_names,
                {
                    ("John Doe", "John_Doe"),
                    ("New One", "New_One"),
                    ("Low Views", "Low_Views"),
                },
            )

        self.loop.run_until_complete(run_test())

    def test_poll_once_sends_etag_and_skips_parse_on_304(self):
        async def run_test():
            http = self._fake_http(
                httpx.Response(200, text=SAMPLE_HTML, headers={"ETag": '"v1"'}),
                httpx.Response(304),
            )
            with patch("src.services.death_service.parse_deaths_html",
                       return_value=[("John Doe", "John_Doe")]) as mock_parse:
                await self.service._poll_once()
                self.assertEqual(self.service._deaths_page_etag, '"v1"')
                self.assertEqual(http.get.await_args_list[0].kwargs["headers"], {})

                await self.service._poll_once()
                self.assertEqual(
                    http.get.await_args_list[1].kwargs["headers"], {"If-None-Match": '"v1"'}
                )
            mock_parse.assert_called_once_with(SAMPLE_HTML)
            self.assertEqual(self.service._known_names, {("John Doe", "John_Doe")})

        self.loop.run_until_complete(run_test())

    def test_poll_once_parses_off_the_event_loop(self):
        async def run_test():
            self._fake_http(httpx.Response(200, text=SAMPLE_HTML))

            async def fake_to_thread(fn, *args):
                return fn(*args)

            with patch("src.services.death_service.asyncio.to_thread",
                       side_effect=fake_to_thread) as mock_to_thread:
                await self.service._poll_once()

            from src.services.death_service import parse_deaths_html
            mock_to_thread.assert_called_once_with(parse_deaths_html, SAMPLE_HTML)
            self.assertEqual(len(self.service._known_names), 4)

        self.loop.run_until_complete(run_test())

    def test_already_announced_name_not_reannounced(self):
        """A name that reappears after being announced is never announced again."""
        async def run_test():
            self.service._first_poll = False
            self.service._known_names = set()
            self.service._announced_titles = {"Famous_One"}
            self._fake_http(httpx.Response(200, text=SAMPLE_HTML))

            with patch.object(self.service, "_save_state"), \
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

    def test_poll_loop_survives_errors(self):
        async def run_test():
            self.service._running = True
            calls = []

            async def fake_poll_once():
                calls.append(1)
                self.service._running = False
                raise RuntimeError("boom")

            self.service._poll_once = fake_poll_once

            with patch("src.services.death_service.asyncio.sleep", new=AsyncMock()) as mock_sleep:
                await self.service._poll_loop()

            self.assertEqual(len(calls), 1)
            mock_sleep.assert_awaited_once()

        self.loop.run_until_complete(run_test())

    # ----- HTTP client lifecycle -----

    def test_get_setting_and_client_reuse(self):
        self.state_service.get_death_settings.return_value = {"interval": 9}
        self.assertEqual(self.service._get_setting("interval", 15), 9)
        self.assertEqual(self.service._get_setting("missing", 15), 15)

        async def run_test():
            client_one = self.service._client()
            client_two = self.service._client()
            self.assertIsInstance(client_one, httpx.AsyncClient)
            self.assertIs(client_one, client_two)
            self.assertIn("cmwgpt-bot", client_one.headers["User-Agent"])
            await self.service.close()
            self.assertTrue(client_one.is_closed)
            self.assertIsNone(self.service._http)

        self.loop.run_until_complete(run_test())

    def test_stop_does_not_close_client(self):
        http = self._fake_http(httpx.Response(200))
        self.service.stop()
        http.aclose.assert_not_awaited()
        self.assertIs(self.service._http, http)

    def test_close_stops_and_closes_client(self):
        async def run_test():
            http = self._fake_http(httpx.Response(200))
            with patch.object(self.service, "stop") as mock_stop:
                await self.service.close()
            mock_stop.assert_called_once_with()
            http.aclose.assert_awaited_once_with()
            self.assertIsNone(self.service._http)

            # Closing again with no client is a no-op.
            await self.service.close()

        self.loop.run_until_complete(run_test())

    # ----- Limericks -----

    def test_force_announce_calls_announce(self):
        """force_announce bypasses the threshold and announces, without touching dedup state."""
        async def run_test():
            with patch.object(self.service, "get_avg_monthly_views", AsyncMock(return_value=123)) as mock_views, \
                patch.object(self.service, "_announce", AsyncMock()) as mock_announce:
                ok, msg = await self.service.force_announce(
                    "https://en.wikipedia.org/wiki/Kevin_Keegan"
                )

            self.assertTrue(ok)
            mock_views.assert_awaited_once_with("Kevin_Keegan")
            mock_announce.assert_awaited_once_with("Kevin Keegan", "Kevin_Keegan", 123)
            # Force testing must not pollute the real dedup set.
            self.assertNotIn("Kevin_Keegan", self.service._announced_titles)

        self.loop.run_until_complete(run_test())

    def test_announce_appends_model_summary(self):
        """The announcement includes the model's 'known for' summary when available."""
        async def run_test():
            mock_openai = MagicMock()
            mock_openai.get_chat_completion = AsyncMock(
                return_value=("Legendary footballer and manager.", 0.0)
            )
            self.service._openai_service = mock_openai
            self.service._model = "openrouter/some-model"

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
            prompt = mock_openai.get_chat_completion.call_args.kwargs["messages"][0]["content"][0]["text"]
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
            self.service._model = "openrouter/some-model"

            ok, message = await self.service.write_limerick("Kevin Keegan")

            self.assertTrue(ok)
            self.assertEqual(message, "There once was a keeper named Kev...")
            kwargs = mock_openai.get_chat_completion.call_args.kwargs
            prompt = kwargs["messages"][0]["content"][0]["text"]
            self.assertIn("Kevin Keegan", prompt)
            self.assertIn("https://en.wikipedia.org/wiki/Kevin_Keegan", prompt)
            self.assertIn("limerick", prompt.lower())
            self.assertFalse(kwargs["search"])

        self.loop.run_until_complete(run_test())

    def test_write_limerick_routes_gemini_model_to_gemini_service(self):
        async def run_test():
            from src.services.death_service import DeathService
            gemini = MagicMock()
            gemini.get_chat_completion = AsyncMock(return_value=({"text": "  Gemini verse  "}, 0.0))
            openai = MagicMock()
            openai.get_chat_completion = AsyncMock()
            svc = DeathService(
                state_service=self.state_service,
                state_file="/tmp/_test_death_names.json",
                death_channel_id="12345",
                openai_service=openai,
                gemini_service=gemini,
                model="gemini-model",
            )
            with patch("src.services.completion_dispatch.is_gemini_model", return_value=True), \
                    patch("src.services.completion_dispatch.get_thinking_level", return_value=None):
                ok, message = await svc.write_limerick("Kevin Keegan")

            self.assertTrue(ok)
            self.assertEqual(message, "Gemini verse")
            gemini.get_chat_completion.assert_awaited_once()
            openai.get_chat_completion.assert_not_awaited()

        self.loop.run_until_complete(run_test())

    def test_write_limerick_routes_direct_claude_model_to_anthropic_service(self):
        async def run_test():
            from src.services.death_service import DeathService
            anthropic = MagicMock()
            anthropic.get_chat_completion = AsyncMock(return_value=("Claude verse", 0.0))
            openai = MagicMock()
            openai.get_chat_completion = AsyncMock()
            svc = DeathService(
                state_service=self.state_service,
                state_file="/tmp/_test_death_names.json",
                death_channel_id="12345",
                openai_service=openai,
                anthropic_service=anthropic,
                model="direct-claude",
            )
            with patch("src.services.completion_dispatch.is_gemini_model", return_value=False), \
                    patch("src.services.completion_dispatch.is_direct_anthropic_model", return_value=True):
                ok, message = await svc.write_limerick("Kevin Keegan")

            self.assertTrue(ok)
            self.assertEqual(message, "Claude verse")
            self.assertFalse(anthropic.get_chat_completion.await_args.kwargs["search"])
            openai.get_chat_completion.assert_not_awaited()

        self.loop.run_until_complete(run_test())


if __name__ == "__main__":
    unittest.main()
