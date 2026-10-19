"""Tests for aggregated downloader utility behavior."""

import asyncio
import dataclasses
import unittest
from unittest.mock import AsyncMock, patch

from src.utils import downloader_utils


def fake_sources(**overrides):
    """Copy SOURCES, swapping in ``name=(keys, fetch)`` and finding nothing for the rest.

    The real headers and renderers are kept so the output format is exercised.
    """
    sources = []
    for source in downloader_utils.SOURCES:
        keys, fetch = overrides.get(source.name, ([], AsyncMock(return_value=None)))
        sources.append(dataclasses.replace(
            source, extract=lambda text, keys=keys: list(keys), fetch=fetch))
    return sources


class TestDownloaderUtils(unittest.IsolatedAsyncioTestCase):
    async def test_returns_empty_for_no_text(self):
        self.assertEqual(await downloader_utils.fetch_all_url_content(""), ("", []))

    async def test_returns_empty_when_no_urls(self):
        with patch.object(downloader_utils, "SOURCES", fake_sources()):
            self.assertEqual(await downloader_utils.fetch_all_url_content("no links here"), ("", []))

    def test_sources_are_in_prompt_order(self):
        self.assertEqual([s.name for s in downloader_utils.SOURCES],
                         ["youtube", "tiktok", "twitter", "facebook", "instagram", "article"])

    async def test_aggregates_all_supported_sources_in_order(self):
        sources = fake_sources(
            youtube=(["abc"], AsyncMock(return_value="youtube transcript")),
            tiktok=(["https://vt.tiktok.com/1"], AsyncMock(return_value="tiktok transcript")),
            twitter=(["https://x.com/a/status/1"], AsyncMock(
                return_value=("tweet context", ["data:image/png;base64,TWEET"]))),
            facebook=(["https://fb.watch/1"], AsyncMock(return_value="facebook transcript")),
            instagram=(["https://instagram.com/p/1"], AsyncMock(
                return_value=("instagram context", "data:image/jpeg;base64,IG"))),
            article=(["https://example.com/a", "https://example.com/b"],
                     AsyncMock(side_effect=["article A", "article B"])),
        )

        with patch.object(downloader_utils, "SOURCES", sources):
            text, images = await downloader_utils.fetch_all_url_content("look at these links")

        expected = (
            "------\nIncluded youtube link transcript follows:\n\n"
            "Target Video ID abc Transcript:\nyoutube transcript"
            "\n\n------\nIncluded TikTok video transcript follows:\n\n"
            "Target TikTok Video https://vt.tiktok.com/1 Transcript:\ntiktok transcript"
            "\n\n------\nIncluded Twitter post context follows:\n\n"
            "Target Tweet https://x.com/a/status/1 Context:\ntweet context"
            "\n\n------\nIncluded Facebook video transcript follows:\n\n"
            "Target Facebook Video https://fb.watch/1 Transcript:\nfacebook transcript"
            "\n\n------\nIncluded Instagram/Threads posts follow:\n\n"
            "Instagram/Threads Post https://instagram.com/p/1:\ninstagram context"
            "\n\n------\nIncluded article content follows:\n\n"
            "URL content:\narticle A\n\nURL content:\narticle B"
            "\n\n"
        )
        self.assertEqual(text, expected)
        self.assertEqual(images, [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,TWEET"}},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,IG"}},
        ])

    async def test_instagram_without_image_adds_no_image_part(self):
        sources = fake_sources(instagram=(["https://instagram.com/p/1"], AsyncMock(return_value=("ctx", None))))
        with patch.object(downloader_utils, "SOURCES", sources):
            text, images = await downloader_utils.fetch_all_url_content("x")
        self.assertIn("ctx", text)
        self.assertEqual(images, [])

    async def test_continues_when_some_sources_fail(self):
        sources = fake_sources(
            youtube=(["abc"], AsyncMock(side_effect=RuntimeError("yt fail"))),
            twitter=(["https://x.com/a/status/1"], AsyncMock(return_value=("tweet ok", []))),
            article=(["https://example.com/article"], AsyncMock(return_value=None)),
        )

        with patch.object(downloader_utils, "SOURCES", sources):
            text, images = await downloader_utils.fetch_all_url_content("mixed")

        self.assertIn("tweet ok", text)
        self.assertNotIn("youtube", text)
        self.assertNotIn("article content", text)
        self.assertEqual(images, [])

    async def test_all_failures_return_empty_text(self):
        sources = fake_sources(youtube=(["abc"], AsyncMock(return_value=None)))
        with patch.object(downloader_utils, "SOURCES", sources):
            self.assertEqual(await downloader_utils.fetch_all_url_content("x"), ("", []))

    async def test_cache_only_is_forwarded_to_every_fetch(self):
        yt = AsyncMock(return_value="t")
        art = AsyncMock(return_value="a")
        sources = fake_sources(youtube=(["abc"], yt), article=(["https://e.com"], art))

        with patch.object(downloader_utils, "SOURCES", sources):
            await downloader_utils.fetch_all_url_content("x", cache_only=True)
            await downloader_utils.fetch_all_url_content("x")

        self.assertEqual([c.kwargs for c in yt.await_args_list], [{"cache_only": True}, {"cache_only": False}])
        art.assert_any_await("https://e.com", cache_only=True)

    async def test_fetches_run_concurrently_up_to_limit(self):
        running = 0
        peak = 0

        async def slow(key, *, cache_only=False):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1
            return f"text {key}"

        keys = [f"https://e.com/{i}" for i in range(10)]
        sources = fake_sources(article=(keys, slow))

        with patch.object(downloader_utils, "SOURCES", sources):
            text, _ = await downloader_utils.fetch_all_url_content("x")

        self.assertEqual(peak, downloader_utils.MAX_CONCURRENT_FETCHES)
        # Order of results follows the order links were found, not completion order.
        positions = [text.index(f"text {k}") for k in keys]
        self.assertEqual(positions, sorted(positions))

    async def test_slow_item_is_dropped_after_timeout(self):
        async def hang(key, *, cache_only=False):
            await asyncio.sleep(10)

        sources = [
            dataclasses.replace(s, timeout=0.01) if s.name == "article" else s
            for s in fake_sources(
                article=(["https://slow.example"], hang),
                youtube=(["dQw4w9WgXcQ"], AsyncMock(return_value="fast transcript")),
            )
        ]

        with patch.object(downloader_utils, "SOURCES", sources):
            text, _ = await downloader_utils.fetch_all_url_content("x")

        self.assertIn("fast transcript", text)
        self.assertNotIn("URL content", text)

    def test_only_quick_sources_have_a_timeout(self):
        timeouts = {s.name: s.timeout for s in downloader_utils.SOURCES}
        self.assertEqual(timeouts["youtube"], downloader_utils.QUICK_FETCH_TIMEOUT_SECONDS)
        self.assertEqual(timeouts["article"], downloader_utils.QUICK_FETCH_TIMEOUT_SECONDS)
        # Video sources: only their download step is time-limited.
        for name in ("tiktok", "twitter", "facebook", "instagram"):
            self.assertIsNone(timeouts[name], name)


if __name__ == "__main__":
    unittest.main()
