"""Tests for the IRC log search and day reader."""

import os
import tempfile
import unittest
from datetime import date

from src.services.irc_log_service import IrcLog, NickMap, load_irc_log

LOG = """--- Log opened Tue May 19 20:00:00 2020
22:58 <@Iluvitar> but his podcast is pretty good?
23:19 <@Mem0ut> https://www.reddit.com/r/truegaming/comments/gmwd9v/why_is_physx_never_used_anymore/
23:23 <@jobez> https://twitter.com/mikesbloggity/status/1262823591961260037?s=19
--- Day changed Wed May 20 2020
06:48 <@Cyc66> joe rogan is a gateway
06:48 <@Cyc66> to the far right
06:49  * jobez sighs at joe rogan
06:50 <+Mem0ut> joe rogan podcast again
"""


# Lines from the real irssinicks file, including ones that are risky as regexes.
NICKS = r"""Kno \^Kno
memout ^[mM]em
memout LIkver
Memout Mem0ut
Cyc66 ^[Cc]yc
[Soul| ^\[Soul
ChewyJen \[AxIe\]
Sxan |-_-|
<NULL> *>
<NULL> ^cmw
jobe ^jobe
"""


class TestNickMap(unittest.TestCase):
    def setUp(self):
        self.nicks = NickMap([tuple(line.split()) for line in NICKS.splitlines()])

    def test_first_matching_rule_wins(self):
        self.assertEqual(self.nicks.canonical("Mem0ut"), "memout")  # ^[mM]em comes before Mem0ut
        self.assertEqual(self.nicks.canonical("LIkver"), "memout")
        self.assertEqual(self.nicks.canonical("cyc66_"), "Cyc66")
        self.assertEqual(self.nicks.canonical("jobez"), "jobe")

    def test_escaped_and_bracket_patterns(self):
        self.assertEqual(self.nicks.canonical("^Kno"), "Kno")
        self.assertEqual(self.nicks.canonical("Kno"), "Kno")  # no rule matches, so it stays as is
        self.assertEqual(self.nicks.canonical("[Soul|away"), "[Soul|")
        self.assertEqual(self.nicks.canonical("[AxIe]"), "ChewyJen")

    def test_patterns_that_would_match_everything_or_fail_are_literal(self):
        self.assertEqual(self.nicks.canonical("Iluvitar"), "Iluvitar")  # not Sxan via |-_-|
        self.assertEqual(self.nicks.canonical("|-_-|"), "Sxan")
        self.assertIsNone(self.nicks.canonical("*>"))

    def test_null_drops_the_nick(self):
        self.assertIsNone(self.nicks.canonical("cmwbot"))


class TestIrcLog(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w") as f:
            f.write(LOG)
        self.log = IrcLog(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_first_day(self):
        self.assertEqual(self.log.first_day(), date(2020, 5, 19))

    def test_coverage_ends_on_the_last_day_anything_was_said(self):
        with open(self.path, "a") as f:
            f.write("--- Day changed Thu May 21 2020\n--- Day changed Fri May 22 2020\n")
        self.assertEqual(self.log.coverage(), "2020-05-19 (Tue) to 2020-05-20 (Wed)")

    def append(self, text):
        with open(self.path, "a") as f:
            f.write(text)
        os.utime(self.path, (5, 5))

    def test_count_counts_occurrences_in_what_people_said(self):
        self.append("--- Day changed Fri Jan 01 2021\n"
                    "07:00 <@Cyc66> rogan rogan ROGAN\n"
                    "07:01 -!- rogan [~r@host] has joined #cmw\n"
                    "07:02 <rogan> hi\n")
        out = self.log.count("rogan")
        # 3 in 2020 plus 3 on one 2021 line; the join line and the nick don't count.
        self.assertTrue(out.startswith("6 occurrences of 'rogan' on 4 lines"), out)
        self.assertTrue(self.log.count("iluvitar|pretty").startswith("1 occurrence of 'iluvitar|pretty' on 1 line "))

    def test_count_groups_by_nick_and_year(self):
        self.append("--- Day changed Fri Jan 01 2021\n07:00 <@Cyc66> rogan rogan\n")
        out = self.log.count("rogan", group_by=["nick"])
        self.assertIn("Top 3 of 3 nicks:", out)
        self.assertLess(out.index("Cyc66: 3 (60.0%)"), out.index("jobez: 1"))

        out = self.log.count("rogan", group_by=["year", "nick"], top=1)
        self.assertIn("2020: 3", out)
        self.assertIn("2021: 2\n    Cyc66: 2 (100.0%)", out)
        self.assertIn("2 others: 2", out)

    def test_count_filters_by_nick_and_rejects_empty_patterns(self):
        self.assertTrue(self.log.count("rogan", nick="MEM0UT").startswith("1 occurrence "))
        self.assertIn("No occurrences", self.log.count("rogan", nick="nobody"))
        self.assertIn("matches empty text", self.log.count("x*"))

    def with_nicks(self):
        fd, nicks_path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w") as f:
            f.write(NICKS)
        self.addCleanup(os.unlink, nicks_path)
        self.append("--- Day changed Fri Jan 01 2021\n"
                    "07:00 <@LIkver> rogan again\n"
                    "07:01 <cmwbot> rogan rogan rogan\n")
        return load_irc_log(self.path, nicks_path)

    def test_counts_merge_aliases_and_drop_null_nicks(self):
        log = self.with_nicks()
        out = log.count("rogan", group_by=["nick"])
        # Mem0ut and LIkver are both memout; cmwbot is <NULL>, so its 3 aren't counted.
        self.assertTrue(out.startswith("4 occurrences"), out)
        self.assertIn("memout: 2 (50.0%)", out)
        self.assertNotIn("cmwbot", out)
        self.assertTrue(log.count("rogan", nick="Mem0ut").startswith("2 occurrences of 'rogan' by memout"))

    def test_search_nick_filter_covers_aliases(self):
        out = self.with_nicks().search("rogan", nick="memout")
        self.assertIn("2 matching lines", out)
        self.assertIn("<+Mem0ut> joe rogan podcast again", out)
        self.assertIn("<@LIkver> rogan again", out)

    def test_speakers_rank_merged_nicks_with_years(self):
        log = self.with_nicks()
        self.append("07:02 <***> Buffer Playback...\n")
        speakers = log.speakers()
        # memout: Mem0ut's two 2020 lines plus LIkver's 2021 line; cmwbot and *** aren't people.
        self.assertEqual(speakers[0], ("memout", 3, 2020, 2021))
        self.assertIn(("Cyc66", 2, 2020, 2020), speakers)
        self.assertNotIn("cmwbot", [s[0] for s in speakers])
        self.assertNotIn("***", [s[0] for s in speakers])
        self.assertEqual(len(log.speakers(top=1)), 1)

    def test_unreadable_nicks_file_still_loads_the_log(self):
        log = load_irc_log(self.path, self.path + ".missing")
        self.assertIsNotNone(log)
        self.assertIsNone(log.nicks)

    def test_hits_are_dated_by_the_last_day_marker(self):
        out = self.log.search("podcast")
        self.assertIn("2 matching lines", out)
        self.assertLess(out.index("[2020-05-19 (Tue)]"), out.index("pretty good"))
        self.assertLess(out.index("[2020-05-20 (Wed)]"), out.index("joe rogan podcast again"))

    def test_nick_filter_handles_mode_prefixes_and_actions(self):
        out = self.log.search("rogan", nick="jobez")
        self.assertIn("1 matching lines", out)
        self.assertIn("* jobez sighs", out)
        self.assertIn("1 matching lines", self.log.search("rogan", nick="cyc66"))
        self.assertIn("podcast again", self.log.search("rogan", nick="mem0ut"))

    def test_newest_first_and_paging(self):
        out = self.log.search("rogan", newest_first=True, limit=1, skip=1)
        self.assertIn("showing 2-2, newest first", out)
        self.assertIn("jobez sighs", out)
        self.assertIn("Use skip=2", out)

    def test_invalid_regex_is_searched_literally(self):
        self.assertIn("No matches", self.log.search("[unclosed"))
        self.assertIn("1 matching lines", self.log.search("pretty good?"))

    def test_context_includes_surrounding_lines(self):
        out = self.log.search("gateway", context=1)
        self.assertIn("to the far right", out)
        self.assertIn("Day changed", out)

    def test_read_day(self):
        out = self.log.read_day(date(2020, 5, 20))
        self.assertIn("4 lines", out)
        self.assertNotIn("pretty good", out)
        self.assertIn("covers 2020-05-19 (Tue) to 2020-05-20 (Wed)", self.log.read_day(date(2001, 1, 1)))

    def test_load_irc_log_without_a_file(self):
        self.assertIsNone(load_irc_log(""))
        self.assertIsNone(load_irc_log("/nonexistent/cmw.txt"))


if __name__ == "__main__":
    unittest.main()
