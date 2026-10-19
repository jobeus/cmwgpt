"""Tests for article URL extraction and fetching helpers."""

import unittest
from unittest.mock import AsyncMock, patch

import httpx

from src.utils import url_utils
from tests.config_helpers import cfg

_RealAsyncClient = httpx.AsyncClient


class TestExtractTargetUrls(unittest.TestCase):
    def test_extract_target_urls_filters_excluded_domains_and_deduplicates(self):
        text = (
            "See https://example.com/post and www.example.com/post and "
            "https://twitter.com/a/status/1 and https://example.com/post"
        )

        urls = url_utils.extract_target_urls(text)

        self.assertEqual(urls, ["https://example.com/post", "https://www.example.com/post"])

    def test_extract_target_urls_skips_discord_cdn_and_direct_media_urls(self):
        text = (
            "https://cdn.discordapp.com/ephemeral-attachments/1/2/image.png?ex=abc "
            "https://media.discordapp.net/attachments/1/2/photo.jpg "
            "https://example.com/image.webp?width=1024&height=768 "
            "https://example.com/article "
            "https://example.com/post.html"
        )

        urls = url_utils.extract_target_urls(text)

        self.assertEqual(urls, ["https://example.com/article", "https://example.com/post.html"])

    def test_extract_target_urls_strips_trailing_punctuation(self):
        text = (
            "(see https://example.org/a). Also https://example.org/b, "
            "\"https://example.org/c\"! [https://example.org/d] <https://example.org/e>? "
            "https://example.org/f;: https://example.org/g'"
        )

        urls = url_utils.extract_target_urls(text)

        self.assertEqual(urls, [f"https://example.org/{c}" for c in "abcdefg"])

    def test_extract_target_urls_keeps_balanced_closing_paren(self):
        text = "Read https://en.wikipedia.org/wiki/Python_(programming_language). Or (https://example.org/x)"

        urls = url_utils.extract_target_urls(text)

        self.assertEqual(urls, [
            "https://en.wikipedia.org/wiki/Python_(programming_language)",
            "https://example.org/x",
        ])

    def test_extract_target_urls_adds_scheme_to_www_links(self):
        self.assertEqual(url_utils.extract_target_urls("go to www.example.org/page."),
                         ["https://www.example.org/page"])

    def test_extract_target_urls_excludes_subdomains_of_excluded_domains(self):
        text = "https://m.youtube.com/watch?v=x https://old.example.com/ok"
        self.assertEqual(url_utils.extract_target_urls(text), ["https://old.example.com/ok"])

    def test_extract_target_urls_empty(self):
        self.assertEqual(url_utils.extract_target_urls(""), [])
        self.assertEqual(url_utils.extract_target_urls(None), [])


class TestExtractText(unittest.TestCase):
    def test_uses_precision_mode_first(self):
        with patch("src.utils.url_utils.trafilatura.extract", return_value="main text") as mock_extract:
            self.assertEqual(url_utils._extract_text("<html/>"), "main text")
        mock_extract.assert_called_once_with("<html/>")

    def test_falls_back_to_favor_recall(self):
        with patch("src.utils.url_utils.trafilatura.extract", side_effect=[None, "recall text"]) as mock_extract:
            self.assertEqual(url_utils._extract_text("<html/>"), "recall text")
        self.assertEqual(mock_extract.call_args_list[1].kwargs, {"favor_recall": True})

    def test_real_trafilatura_extracts_article(self):
        html = (
            "<html><body><article><h1>Title</h1>"
            + "<p>This is a reasonably long paragraph of article text used for extraction tests.</p>" * 5
            + "</article></body></html>"
        )
        text = url_utils._extract_text(html)
        self.assertIn("reasonably long paragraph", text)


class TestGetArticleText(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cache = {}
        self.requests = []
        self.routes = {}

        def handler(request):
            self.requests.append(request)
            route = self.routes.get(request.url.host)
            if route is None:
                return httpx.Response(404, text="no route")
            return route(request)

        def factory(*args, **kwargs):
            kwargs.pop("proxy", None)
            return _RealAsyncClient(*args, transport=httpx.MockTransport(handler), **kwargs)

        patches = [
            patch("src.utils.url_utils._article_cache", self.cache),
            patch("src.utils.cache_utils._failure_store", return_value=None),
            patch("src.config._cached_config", cfg(transcript_proxy="")),
            # httpx is shared, so this also covers fetch_public in http_client.
            patch("src.utils.url_utils.httpx.AsyncClient", factory),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def public_dns(self):
        p = patch("src.utils.http_client.assert_public_url", new=AsyncMock())
        p.start()
        self.addCleanup(p.stop)

    async def test_returns_cached_value_without_fetching(self):
        self.cache["https://ok"] = "cached"
        self.assertEqual(await url_utils.get_article_text("https://ok"), "cached")
        self.assertEqual(self.requests, [])

    async def test_cache_only_miss_returns_none_without_fetching(self):
        self.assertIsNone(await url_utils.get_article_text("https://example.com/x", cache_only=True))
        self.assertEqual(self.requests, [])

    async def test_fetches_extracts_and_caches(self):
        self.public_dns()
        self.routes["example.com"] = lambda r: httpx.Response(200, text="<html>content</html>")

        with patch("src.utils.url_utils.trafilatura.extract", return_value="parsed text"):
            result = await url_utils.get_article_text("https://example.com/page")

        self.assertEqual(result, "parsed text")
        self.assertEqual(self.cache["https://example.com/page"], "parsed text")
        self.assertIn("Mozilla", self.requests[0].headers["user-agent"])

    async def test_uses_fetch_public_with_html_cap(self):
        response = httpx.Response(200, text="<html/>", request=httpx.Request("GET", "https://example.com/"))
        with patch("src.utils.url_utils.fetch_public", new=AsyncMock(return_value=response)) as mock_fetch, patch(
            "src.utils.url_utils.trafilatura.extract", return_value="t"
        ):
            await url_utils.get_article_text("https://example.com/")

        self.assertEqual(mock_fetch.await_args.kwargs["max_bytes"], url_utils.MAX_HTML_BYTES)
        self.assertEqual(url_utils.MAX_HTML_BYTES, 5 * 1024 * 1024)

    async def test_no_extractable_text_returns_none(self):
        self.public_dns()
        self.routes["example.com"] = lambda r: httpx.Response(200, text="<html></html>")

        with patch("src.utils.url_utils.trafilatura.extract", return_value=None):
            self.assertIsNone(await url_utils.get_article_text("https://example.com/empty"))
        self.assertEqual(self.cache, {})

    async def test_http_error_returns_none(self):
        self.public_dns()
        self.routes["example.com"] = lambda r: httpx.Response(403, text="forbidden")

        self.assertIsNone(await url_utils.get_article_text("https://example.com/blocked"))

    async def test_oversized_page_returns_none(self):
        self.public_dns()
        self.routes["example.com"] = lambda r: httpx.Response(
            200, text="x", headers={"content-length": str(url_utils.MAX_HTML_BYTES + 1)})

        self.assertIsNone(await url_utils.get_article_text("https://example.com/huge"))

    async def test_fetches_old_reddit_but_caches_original_url(self):
        self.public_dns()
        original_url = "https://www.reddit.com/r/test/comments/1/reddit.com_in_title/"
        self.routes["old.reddit.com"] = lambda r: httpx.Response(200, text="<html>reddit</html>")

        with patch("src.utils.url_utils.trafilatura.extract", return_value="reddit text"):
            result = await url_utils.get_article_text(original_url)

        self.assertEqual(result, "reddit text")
        # Only the host is swapped to old.reddit.com; the path stays intact.
        self.assertEqual(str(self.requests[0].url),
                         "https://old.reddit.com/r/test/comments/1/reddit.com_in_title/")
        self.assertEqual(self.cache[original_url], "reddit text")

    async def test_vreddit_redirect_is_resolved_then_fetched_via_old_reddit(self):
        self.public_dns()
        self.routes["v.redd.it"] = lambda r: httpx.Response(
            301, headers={"location": "https://www.reddit.com/r/vids/comments/9/clip/"})
        self.routes["old.reddit.com"] = lambda r: httpx.Response(200, text="<html>clip</html>")

        with patch("src.utils.url_utils.trafilatura.extract", return_value="clip text"):
            result = await url_utils.get_article_text("https://v.redd.it/abc123")

        self.assertEqual(result, "clip text")
        self.assertEqual([str(r.url) for r in self.requests], [
            "https://v.redd.it/abc123",
            "https://old.reddit.com/r/vids/comments/9/clip/",
        ])
        self.assertEqual(self.cache["https://v.redd.it/abc123"], "clip text")

    async def test_resolve_vreddit_returns_original_on_error(self):
        def boom(request):
            raise httpx.ConnectError("down")
        self.routes["v.redd.it"] = boom

        self.assertEqual(await url_utils._resolve_vreddit("https://v.redd.it/x", None), "https://v.redd.it/x")

    async def test_wikipedia_uses_api(self):
        def api(request):
            self.assertEqual(request.url.path, "/w/api.php")
            self.assertEqual(request.url.params["titles"], "Python_(programming_language)")
            return httpx.Response(200, json={"query": {"pages": {"1": {"extract": "Python is a language."}}}})
        self.routes["en.wikipedia.org"] = api

        result = await url_utils.get_article_text("https://en.wikipedia.org/wiki/Python_%28programming_language%29")

        self.assertEqual(result, "Python is a language.")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(
            self.cache["https://en.wikipedia.org/wiki/Python_%28programming_language%29"], "Python is a language.")

    async def test_wikipedia_api_failure_falls_back_to_scraping(self):
        self.public_dns()
        calls = []

        def route(request):
            calls.append(request.url.path)
            if request.url.path == "/w/api.php":
                return httpx.Response(500)
            return httpx.Response(200, text="<html>wiki page</html>")
        self.routes["en.wikipedia.org"] = route

        with patch("src.utils.url_utils.trafilatura.extract", return_value="scraped"):
            result = await url_utils.get_article_text("https://en.wikipedia.org/wiki/Thing")

        self.assertEqual(result, "scraped")
        self.assertEqual(calls, ["/w/api.php", "/wiki/Thing"])

    async def test_blocks_non_public_urls(self):
        self.routes["127.0.0.1"] = lambda r: httpx.Response(200, text="<html>internal</html>")

        result = await url_utils.get_article_text("http://127.0.0.1:8080/admin")

        self.assertIsNone(result)
        self.assertEqual(self.requests, [])

    async def test_blocks_redirect_to_metadata_endpoint(self):
        async def guard(url):
            if "169.254" in url:
                raise ValueError("non-public")

        self.routes["example.com"] = lambda r: httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        self.routes["169.254.169.254"] = lambda r: httpx.Response(200, text="secrets")

        with patch("src.utils.http_client.assert_public_url", side_effect=guard):
            self.assertIsNone(await url_utils.get_article_text("https://example.com/redirect"))

        self.assertEqual([r.url.host for r in self.requests], ["example.com"])


class TestInjectArticleCache(unittest.TestCase):
    def test_inject_article_cache_writes_directly(self):
        fake_cache = {}

        with patch("src.utils.url_utils._article_cache", fake_cache):
            url_utils.inject_article_cache("https://example.com", "hello")

        self.assertEqual(fake_cache["https://example.com"], "hello")



if __name__ == "__main__":
    unittest.main()
