"""Tests for the civicAPI lookups and the ``election_results`` chat tool."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from src.services import election_service as es
from src.services.chat_tools import build_mention_tools


def _candidate(name, votes, percent, party="Party", **extra):
    return {"name": name, "party": party, "votes": votes, "percent": percent, "winner": False, **extra}


def _race(race_id, name, votes, date="2026-11-03T05:00:00.000Z", **extra):
    candidates = [_candidate(f"{name} A", votes, 60), _candidate(f"{name} B", votes // 2, 40)]
    return {"id": race_id, "election_name": name, "election_date": date, "country": "US", "type": "Governor",
            "percent_reporting": 80, "candidates": candidates, **extra}


FULL_RACE = {
    "election_name": "Colorado Governor", "election_type": "Governor", "election_scope": "Statewide",
    "election_date": "2026-11-03T05:00:00.000Z", "country": "US", "province": "CO", "percent_reporting": 83.6,
    "called_at": "2026-11-04T02:18:31.025Z", "last_updated": "2026-11-05T19:41:55.630Z", "round": 1,
    "candidates": [
        _candidate("Victor Marx", 806000, 33.99, party="Republican"),
        _candidate("Phil Weiser", 1434982, 60.46, party="Democratic", winner=True),
    ],
    "region_results": {
        "adams": {"name": "Adams", "type": "County", "percent_reporting": 80.2, "candidates": [
            _candidate("Phil Weiser", "100", "60"), _candidate("Victor Marx", "50", "30")]},
        "boulder": {"name": "Boulder", "type": "County", "percent_reporting": 95, "candidates": [
            _candidate("Phil Weiser", "900", "80"), _candidate("Victor Marx", "90", "8")]},
    },
}


def _response(status, body=None):
    return httpx.Response(status, json=body if body is not None else {})


class TestGet(unittest.IsolatedAsyncioTestCase):
    async def _get(self, *responses):
        client = MagicMock()
        client.get = AsyncMock(side_effect=list(responses))
        with patch.object(es.asyncio, "sleep", AsyncMock()) as sleep:
            try:
                return await es._get(client, "race/1"), client.get.await_count, sleep.await_count
            except es.ElectionDataError as e:
                return e, client.get.await_count, sleep.await_count

    async def test_an_overload_is_retried_until_it_clears(self):
        result, calls, sleeps = await self._get(_response(503), _response(200, {"ok": 1}))
        self.assertEqual((result, calls, sleeps), ({"ok": 1}, 2, 1))

    async def test_a_lasting_overload_gives_up_and_says_so(self):
        result, calls, sleeps = await self._get(_response(503), _response(503), _response(503))
        self.assertIsInstance(result, es.ElectionDataError)
        self.assertIn("overloaded", str(result))
        self.assertEqual((calls, sleeps), (3, 2))

    async def test_a_missing_race_is_none_without_a_retry(self):
        self.assertEqual(await self._get(_response(404)), (None, 1, 0))

    async def test_other_errors_raise_without_a_retry(self):
        result, calls, _ = await self._get(_response(400))
        self.assertIn("HTTP 400", str(result))
        self.assertEqual(calls, 1)

    async def test_an_unreachable_api_raises(self):
        result, _, _ = await self._get(httpx.ConnectError("boom"))
        self.assertIn("ConnectError", str(result))

    async def test_non_json_raises(self):
        result, _, _ = await self._get(httpx.Response(200, text="<html>"))
        self.assertIn("isn't JSON", str(result))


class TestSearchRaces(unittest.IsolatedAsyncioTestCase):
    async def _search(self, races, **kwargs):
        with patch.object(es, "_get", AsyncMock(return_value={"races": races})) as get:
            return await es.search_races(**kwargs), get

    async def test_most_votes_come_first_so_a_nation_beats_its_regions(self):
        text, _ = await self._search([_race(1, "Overseas", 10), _race(2, "National", 1000), _race(3, "State", 100)])
        self.assertLess(text.index("National"), text.index("State"))
        self.assertLess(text.index("State"), text.index("Overseas"))

    async def test_races_without_votes_go_newest_first_and_say_no_results(self):
        old, new = _race(1, "Old", 0, date="2020-01-01"), _race(2, "New", 0, date="2026-01-01")
        text, _ = await self._search([old, new])
        self.assertLess(text.index("New ("), text.index("Old ("))
        self.assertIn("no results reported", text)
        self.assertNotIn("0 votes", text)

    async def test_filters_are_sent_upper_cased_and_empty_ones_left_out(self):
        _, get = await self._search([_race(1, "A", 1)], query="governor", country="us", province="co")
        self.assertEqual(get.await_args.args[2], {"query": "governor", "country": "US", "province": "CO",
                                                  "limit": es.SEARCH_FETCH})

    async def test_rows_have_the_link_and_credit_civicapi(self):
        text, _ = await self._search([_race(87018, "Colorado Governor", 1000)])
        self.assertIn("race 87018", text)
        self.assertIn("https://civicapi.org/results/elections/87018", text)
        self.assertIn("80% reporting", text)
        self.assertTrue(text.endswith(es.SOURCE))

    async def test_no_winner_is_claimed_unless_flagged(self):
        text, _ = await self._search([_race(1, "A", 1000)])
        self.assertNotIn("WINNER", text)
        race = _race(1, "A", 1000)
        race["candidates"][1]["winner"] = True
        text, _ = await self._search([race])
        self.assertIn("A B (Party): 40% · 500 votes · WINNER", text)

    async def test_a_first_rounds_pair_is_not_called_two_winners(self):
        # Search rows have no ``advanced`` field, and civicAPI flags both of a runoff's candidates as winners.
        race = _race(1, "Brazil President", 1000)
        race["candidates"][0]["winner"] = race["candidates"][1]["winner"] = True
        text, _ = await self._search([race])
        self.assertEqual(text.count("WINNER or advanced to a runoff"), 2)
        self.assertNotIn("WINNER\n", text)

    async def test_pages_through_results(self):
        races = [_race(i, f"Race{i:02}", 1000 - i) for i in range(es.SEARCH_PAGE + 2)]
        text, _ = await self._search(races)
        self.assertIn(f"Use skip={es.SEARCH_PAGE} for more.", text)
        text, _ = await self._search(races, skip=es.SEARCH_PAGE)
        self.assertIn(f"Showing {es.SEARCH_PAGE + 1}-{es.SEARCH_PAGE + 2}", text)
        text, _ = await self._search(races, skip=99)
        self.assertIn("past the end", text)

    async def test_a_full_batch_warns_that_the_biggest_may_be_missing(self):
        text, _ = await self._search([_race(i, "A", 1) for i in range(es.SEARCH_FETCH)])
        self.assertIn(f"At least {es.SEARCH_FETCH}", text)
        self.assertIn("add filters", text)

    async def test_many_candidates_are_cut_off(self):
        race = _race(1, "A", 1)
        race["candidates"] = [_candidate(f"C{i}", 10 - i, 1) for i in range(es.MAX_SEARCH_CANDIDATES + 2)]
        text, _ = await self._search([race])
        self.assertIn("...and 2 more", text)

    async def test_seats_show_for_chamber_races(self):
        race = _race(1, "State Senate", 100, seats=33)
        race["candidates"][0].update(seats=18)
        text, _ = await self._search([race])
        self.assertIn("33 seats", text)
        self.assertIn("18 seats", text)

    async def test_nothing_found_is_empty(self):
        with patch.object(es, "_get", AsyncMock(return_value={"count": 0})):
            self.assertEqual(await es.search_races(query="zzqq"), "")


class TestRaceResults(unittest.IsolatedAsyncioTestCase):
    async def _race(self, race=FULL_RACE, region=""):
        with patch.object(es, "_get", AsyncMock(return_value=race)):
            return await es.race_results("87018", region)

    async def test_full_race(self):
        text = await self._race()
        self.assertIn("Colorado Governor (race 87018)", text)
        self.assertIn("2026-11-03 · US / CO · Governor, Statewide · 83.6% reporting", text)
        self.assertIn("Called: 2026-11-04T02:18:31.025Z", text)
        self.assertIn("Phil Weiser (Democratic): 60.46% · 1,434,982 votes · WINNER", text)
        self.assertLess(text.index("Phil Weiser (Democratic)"), text.index("Victor Marx (Republican)"))
        self.assertTrue(text.endswith(es.SOURCE))

    async def test_an_uncalled_race_says_so(self):
        race = {**FULL_RACE, "called_at": None, "candidates": [_candidate("Phil Weiser", 10, 60)]}
        text = await self._race(race)
        self.assertIn("Not called by civicAPI.", text)
        self.assertNotIn("WINNER", text)

    async def test_a_winner_without_a_called_time_is_still_called(self):
        # Brazil's runoff (race 162819) has a flagged winner and an empty called_at.
        text = await self._race({**FULL_RACE, "called_at": None})
        self.assertIn("Called by civicAPI; it doesn't say when.", text)
        self.assertNotIn("Not called", text)

    async def test_candidates_who_advanced_are_not_winners(self):
        # Brazil's first round (race 130870) flags its runoff pair with both winner and advanced.
        race = {**FULL_RACE, "called_at": None, "candidates": [
            _candidate("Bolsonaro", 56, 47, winner=True, advanced=True),
            _candidate("Lula", 53, 45, winner=True, advanced=True),
            _candidate("Cury", 3, 2),
        ]}
        text = await self._race(race)
        self.assertEqual(text.count("advanced to the next round"), 2)
        self.assertNotIn("WINNER", text)
        self.assertIn("No outright winner", text)

    async def test_regions_are_biggest_first(self):
        text = await self._race()
        self.assertIn("Biggest of 2 regions", text)
        self.assertLess(text.index("Boulder County"), text.index("Adams County"))
        self.assertIn("Boulder County (95% reporting): Phil Weiser 80% (900); Victor Marx 8% (90)", text)

    async def test_region_filter_picks_matching_regions(self):
        text = await self._race(region="boul")
        self.assertIn('Regions matching "boul"', text)
        self.assertNotIn("Adams", text)

    async def test_region_filter_without_a_match_says_so_and_lists_the_biggest(self):
        text = await self._race(region="Denver")
        self.assertIn('No region matches "Denver"', text)
        self.assertIn("Boulder County", text)

    async def test_flags_disputed_and_test_data(self):
        text = await self._race({**FULL_RACE, "is_disputed": True, "test_data": True})
        self.assertIn("disputed", text)
        self.assertIn("test data", text)

    async def test_race_without_regions(self):
        race = {k: v for k, v in FULL_RACE.items() if k != "region_results"}
        self.assertNotIn("region", await self._race(race))
        self.assertIn("No breakdown by region.", await self._race(race, region="Boulder"))

    async def test_missing_race_is_empty(self):
        self.assertEqual(await self._race(None), "")


class TestParseRaceId(unittest.TestCase):
    def test_ids_and_links(self):
        self.assertEqual(es.parse_race_id(" 87018 "), "87018")
        self.assertEqual(es.parse_race_id("https://civicapi.org/results/elections/87018?x=1"), "87018")
        self.assertIsNone(es.parse_race_id("colorado governor"))
        self.assertIsNone(es.parse_race_id("https://example.com/87018"))


class TestElectionResultsTool(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tools = build_mention_tools(
            guild_id=1, channel_id=2, user_id=3, user_notes_provider=None, how_i_work_loader=lambda: "",
            state_service=None, gemini_service=None, default_image_model=None)
        self.tool = next(t for t in tools if t.definition["name"] == "election_results")

    async def _run(self, **args):
        with patch("src.services.chat_tools.search_races", AsyncMock(return_value="RACES")) as search, \
                patch("src.services.chat_tools.race_results", AsyncMock(return_value="RACE")) as race:
            return await self.tool.run(args), search, race

    async def test_search_passes_its_filters(self):
        result, search, race = await self._run(query=" governor ", country="US", start_date="2026-11-03", skip=12)
        self.assertEqual(result.content[0]["text"], "RACES")
        search.assert_awaited_once_with(query="governor", country="US", province="", district="",
                                        start_date="2026-11-03", end_date="", skip=12)
        race.assert_not_awaited()

    async def test_a_date_alone_is_enough(self):
        result, search, _ = await self._run(end_date="2026-11-03")
        self.assertFalse(result.is_error)
        search.assert_awaited_once()

    async def test_race_takes_an_id_or_a_link_and_wins_over_search(self):
        result, search, race = await self._run(race="https://civicapi.org/results/elections/87018", region="Boulder",
                                               query="ignored")
        self.assertEqual(result.content[0]["text"], "RACE")
        race.assert_awaited_once_with("87018", "Boulder")
        search.assert_not_awaited()

    async def test_bad_arguments_are_errors_without_a_lookup(self):
        for args in ({}, {"race": "colorado"}, {"query": "x", "start_date": "Nov 3"}):
            result, search, race = await self._run(**args)
            self.assertTrue(result.is_error, args)
            search.assert_not_awaited()
            race.assert_not_awaited()

    async def test_api_failures_and_empty_results_are_errors(self):
        with patch("src.services.chat_tools.search_races", AsyncMock(side_effect=es.ElectionDataError("overloaded"))):
            result = await self.tool.run({"query": "x"})
        self.assertTrue(result.is_error)
        self.assertEqual(result.content[0]["text"], "overloaded")
        with patch("src.services.chat_tools.search_races", AsyncMock(return_value="")):
            result = await self.tool.run({"query": "x"})
        self.assertTrue(result.is_error)
        self.assertIn("No races match", result.content[0]["text"])
        with patch("src.services.chat_tools.race_results", AsyncMock(return_value="")):
            result = await self.tool.run({"race": "1"})
        self.assertTrue(result.is_error)
        self.assertIn("no race 1", result.content[0]["text"])


if __name__ == "__main__":
    unittest.main()
