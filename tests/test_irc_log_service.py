"""Tests for the IRC log search and day reader."""

import os
import tempfile
import unittest
from datetime import date

from src.services.irc_log_service import IrcLog, load_irc_log

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
