"""Tests for searching the IRC log by meaning."""

import os
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np

from src.services import irc_meaning_index
from src.services.cost_service import cost_scope
from src.services.irc_log_service import IrcLog, NickMap
from src.services.irc_meaning_index import IrcMeaningIndex

LOG = """--- Log opened Tue May 19 20:00:00 2020
20:00 <@jobeus> my truck got hauled off from outside the bar
20:01 <sam> brutal, impound is like 300 bucks
21:30 <sam> anyone watching firefly
21:31 <realjobe> best show ever
--- Day changed Wed Jun 10 2021
07:00 <Bot9> beep boop
07:01 <sam> they towed my car again
"""

# Each text becomes a vector of which topics it mentions, so closeness is predictable.
TOPICS = ("tow|haul|impound", "firefly|show", "beep")


def fake_vector(text):
    import re
    v = np.array([1.0 if re.search(t, text, re.I) else 0.0 for t in TOPICS] + [0.01])
    return (v / np.linalg.norm(v)).tolist()


def fake_gemini(calls):
    async def embed_content(*, model, contents, config):
        calls.append(list(contents))
        return SimpleNamespace(embeddings=[SimpleNamespace(values=fake_vector(t)) for t in contents])

    client = MagicMock()
    client.aio.models.embed_content = embed_content
    gemini = MagicMock()
    gemini._get_client.return_value = client
    return gemini


class TestIrcMeaningIndex(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "cmw.txt")
        with open(self.path, "w") as f:
            f.write(LOG)
        self.nicks = NickMap([("Jobe", "jobe"), ("<NULL>", "Bot9")])
        patcher = patch.object(irc_meaning_index, "EMBED_DIMS", len(TOPICS) + 1)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def build(self, nicks=None):
        calls = []
        index = IrcMeaningIndex(IrcLog(self.path, nicks or self.nicks), fake_gemini(calls))
        with cost_scope() as ledger:
            index.start()
            await index._task
        return index, calls, ledger

    async def test_builds_with_canonical_nicks_and_without_bots(self):
        index, calls, ledger = await self.build()

        self.assertTrue(index.ready)
        self.assertEqual(index.total, 3)
        texts = calls[0]
        self.assertIn("<Jobe> my truck got hauled off", texts[0])
        self.assertIn("<Jobe> best show ever", texts[1])
        self.assertNotIn("Bot9", texts[2])
        self.assertTrue(texts[0].startswith("title: IRC chat on 2020-05-19 (Tue) | text: "))
        self.assertGreater(ledger.by_provider["gemini-direct"], 0)
        self.assertTrue(os.path.exists(index.path))

    async def test_finds_conversations_by_meaning_with_filters(self):
        index, _, _ = await self.build()

        result = await index.search("someone's car got towed", limit=2)
        self.assertLess(result.index("hauled off"), result.index("towed my car again"))
        self.assertNotIn("firefly", result)

        only_2021 = await index.search("car got towed", after=date(2021, 1, 1))
        self.assertNotIn("hauled off", only_2021)
        self.assertIn("towed my car again", only_2021)

        # Any of Jobe's aliases counts as Jobe.
        with_jobe = await index.search("car got towed", nick="jobeus")
        self.assertIn("hauled off", with_jobe)
        self.assertNotIn("towed my car again", with_jobe)

    async def test_a_restart_loads_the_saved_index_without_embedding(self):
        await self.build()
        _, calls, _ = await self.build()
        self.assertEqual(calls, [])

    async def test_a_restart_resumes_a_partial_build(self):
        index, _, _ = await self.build()
        index._built = 1
        index._save(index._signature())
        resumed, calls, _ = await self.build()
        self.assertTrue(resumed.ready)
        self.assertEqual(len(calls[0]), 2)

    async def test_changing_the_nicks_file_rebuilds(self):
        await self.build()
        _, calls, _ = await self.build(NickMap([("Jobe", "jobe")]))
        self.assertEqual(len(calls[0]), 3)

    async def test_searching_before_its_built_says_so(self):
        index = IrcMeaningIndex(IrcLog(self.path, self.nicks), fake_gemini([]))
        self.assertIn("still being built", await index.search("anything"))


class TestTool(unittest.IsolatedAsyncioTestCase):
    async def test_tool_passes_filters_and_rejects_bad_dates(self):
        from src.services.chat_tools import build_mention_tools

        index = MagicMock()
        index.search = AsyncMock(return_value="results")
        tools = {t.definition["name"]: t for t in build_mention_tools(
            guild_id=None, channel_id=20, user_id=30, user_notes_provider=None, channel_summary_provider=None,
            how_i_work_loader=lambda: "", state_service=MagicMock(), gemini_service=MagicMock(),
            runpod_service=MagicMock(), default_draw_model=None, irc_log=MagicMock(), irc_meaning_index=index,
        )}
        tool = tools["search_irc_by_meaning"]
        result = await tool.run({"query": "truck towed", "nick": "@jobe", "after": "2020-01-01", "limit": 3})
        self.assertEqual(result.content[0]["text"], "results")
        index.search.assert_awaited_once_with(
            "truck towed", nick="jobe", after=date(2020, 1, 1), before=None, limit=3)
        self.assertTrue((await tool.run({"query": "x", "after": "last spring"})).is_error)
        self.assertTrue((await tool.run({"query": " "})).is_error)


if __name__ == "__main__":
    unittest.main()
