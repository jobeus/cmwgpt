"""Tests for watching videos with Gemini: fetching, re-encoding, and static vs. agentic."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from src.services import video_service
from src.services.chat_tools import build_mention_tools
from src.services.cost_service import cost_scope
from src.services.gemini_service import GeminiService, GeminiServiceError
from src.services.video_service import (
    AGENTIC_MIN_SECONDS, VideoError, VideoWatcher, download_video, is_direct_video_url, video_attachment_url,
)
from tests.config_helpers import AsyncDictCache, cfg


def _gemini():
    gemini = MagicMock()
    gemini.upload_video = AsyncMock(return_value=SimpleNamespace(uri="files/abc-uri", name="files/abc"))
    gemini.watch_video = AsyncMock(return_value=("A dog does a backflip at [0:12].", 0.02))
    gemini.summarize_youtube = AsyncMock(return_value=("static summary", 0.01))
    return gemini


class TestWatchFile(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for name in ("_watch_cache", "_upload_cache"):
            patcher = patch(f"src.services.video_service.{name}", AsyncDictCache())
            patcher.start()
            self.addCleanup(patcher.stop)

    async def watch(self, seconds, gemini=None, *, name="download.mkv", size=2048, focus="the trick"):
        gemini = gemini or _gemini()

        async def fake_download(url, temp_dir):
            path = os.path.join(temp_dir, name)
            with open(path, "wb") as f:
                f.truncate(size)
            return path

        def fake_ffmpeg(args):
            with open(args[-1], "wb") as f:
                f.write(b"y" * 1024)
            return ""

        with patch("src.services.video_service.download_video", side_effect=fake_download), patch(
            "src.services.video_service._probe_seconds", return_value=seconds
        ), patch("src.services.video_service._run", side_effect=fake_ffmpeg) as ffmpeg:
            answer = await VideoWatcher(gemini).watch("https://www.tiktok.com/@a/video/1", focus)
        return answer, gemini, ffmpeg

    async def test_short_videos_are_reencoded_uploaded_and_watched_statically(self):
        answer, gemini, ffmpeg = await self.watch(42.0)

        self.assertEqual(answer, ("A dog does a backflip at [0:12].", 0.02))
        args = ffmpeg.call_args.args[0]
        self.assertEqual(args[0], "ffmpeg")
        self.assertIn("scale=-2:'min(480,ih)'", args)
        self.assertEqual(gemini.upload_video.await_args.args[1], "video/mp4")
        uri, prompt = gemini.watch_video.await_args.args
        self.assertEqual(uri, "files/abc-uri")
        self.assertIn("the trick", prompt)
        self.assertFalse(gemini.watch_video.await_args.kwargs["agentic"])

    async def test_small_videos_gemini_takes_are_uploaded_as_they_are(self):
        _, gemini, ffmpeg = await self.watch(42.0, name="download.mov", size=50 * 1024 * 1024)
        ffmpeg.assert_not_called()
        path, mime_type = gemini.upload_video.await_args.args
        self.assertTrue(path.endswith("download.mov"))
        self.assertEqual(mime_type, "video/mov")
        self.assertEqual(gemini.watch_video.await_args.kwargs["mime_type"], "video/mov")

    async def test_videos_of_100mb_or_more_are_reencoded(self):
        _, gemini, ffmpeg = await self.watch(42.0, name="download.mp4", size=100 * 1024 * 1024)
        ffmpeg.assert_called_once()
        self.assertTrue(gemini.upload_video.await_args.args[0].endswith("watch.mp4"))

    async def test_long_videos_are_watched_agentically(self):
        _, gemini, _ = await self.watch(AGENTIC_MIN_SECONDS + 1)
        self.assertTrue(gemini.watch_video.await_args.kwargs["agentic"])

    async def test_a_new_question_reuses_the_upload(self):
        gemini = _gemini()
        await self.watch(30.0, gemini, focus="the trick")
        _, _, ffmpeg = await self.watch(30.0, gemini, focus="the dog's name")
        gemini.upload_video.assert_awaited_once()
        ffmpeg.assert_not_called()
        self.assertEqual(gemini.watch_video.await_count, 2)
        self.assertIn("the dog's name", gemini.watch_video.await_args.args[1])

    async def test_an_expired_upload_is_uploaded_again(self):
        gemini = _gemini()
        gemini.watch_video.side_effect = [GeminiServiceError("file not found"), ("fresh answer", 0.02)]
        await video_service._upload_cache.set("https://www.tiktok.com/@a/video/1", {
            "uri": "files/old", "mime_type": "video/mp4", "seconds": 30.0, "expires_at": 9e12})
        answer, _, _ = await self.watch(30.0, gemini)
        self.assertEqual(answer[0], "fresh answer")
        gemini.upload_video.assert_awaited_once()

    async def test_a_watch_failure_is_a_video_error(self):
        gemini = _gemini()
        gemini.watch_video.side_effect = GeminiServiceError("failed")
        with self.assertRaises(VideoError):
            await self.watch(30.0, gemini)

    async def test_too_long_and_unreadable_videos_are_refused(self):
        with self.assertRaises(VideoError):
            await self.watch(4 * 3600)
        with self.assertRaises(VideoError):
            await self.watch(None)

    async def test_answers_are_cached_per_video_and_focus(self):
        gemini = _gemini()
        await self.watch(30.0, gemini)
        again, _, _ = await self.watch(30.0, gemini)
        self.assertEqual(again[1], 0.0)
        self.assertEqual(gemini.watch_video.await_count, 1)


class TestWatchYoutube(unittest.IsolatedAsyncioTestCase):
    async def test_long_youtube_videos_go_agentic_and_fall_back_to_static(self):
        gemini = _gemini()
        gemini.watch_video.side_effect = GeminiServiceError("agentic not supported for this URI")
        with patch("src.services.video_service._youtube_seconds", return_value=1800.0):
            answer = await VideoWatcher(gemini).summarize_youtube("https://youtu.be/dQw4w9WgXcQ", "")
        self.assertTrue(gemini.watch_video.await_args.kwargs["agentic"])
        self.assertEqual(answer, ("static summary", 0.01))

    async def test_short_youtube_videos_stay_static(self):
        gemini = _gemini()
        with patch("src.services.video_service._youtube_seconds", return_value=120.0):
            await VideoWatcher(gemini).summarize_youtube("https://youtu.be/dQw4w9WgXcQ", "")
        gemini.watch_video.assert_not_awaited()
        gemini.summarize_youtube.assert_awaited_once()


class TestDownloadVideo(unittest.IsolatedAsyncioTestCase):
    async def test_tweets_use_the_rapidapi_video_url(self):
        with patch("src.services.video_service.find_tweet_video_url",
                   new=AsyncMock(return_value="https://video.twimg.com/a.mp4")), patch(
            "src.services.video_service.download_public", new=AsyncMock()
        ) as download:
            await download_video("https://x.com/a/status/123", "/tmp/x")
        self.assertEqual(download.await_args.args[0], "https://video.twimg.com/a.mp4")

    async def test_a_tweet_without_video_is_an_error(self):
        with patch("src.services.video_service.find_tweet_video_url", new=AsyncMock(return_value=None)):
            with self.assertRaises(VideoError):
                await download_video("https://x.com/a/status/123", "/tmp/x")

    async def test_other_pages_go_through_ytdlp_then_the_proxy(self):
        calls = []

        def fake_ytdlp(url, temp_dir, proxy):
            calls.append(proxy)
            if proxy is None:
                raise RuntimeError("blocked")
            return "/tmp/x/video.mp4"

        with patch("src.services.video_service._ytdlp_video", side_effect=fake_ytdlp), patch(
            "src.services.video_service.get_config", return_value=cfg(transcript_proxy="http://proxy")
        ):
            path = await download_video("https://www.tiktok.com/@a/video/1", "/tmp/x")
        self.assertEqual(path, "/tmp/x/video.mp4")
        self.assertEqual(calls, [None, "http://proxy"])

    def test_direct_files_and_discord_attachments(self):
        self.assertTrue(is_direct_video_url("https://example.com/clip.MP4?x=1"))
        self.assertTrue(is_direct_video_url("https://cdn.discordapp.com/attachments/1/2/thing"))
        self.assertFalse(is_direct_video_url("https://www.tiktok.com/@a/video/1"))


class TestWatchVideoTool(unittest.IsolatedAsyncioTestCase):
    def tools(self, watcher, message):
        return {t.definition["name"]: t for t in build_mention_tools(
            guild_id=None, channel_id=20, user_id=30, user_notes_provider=None, channel_summary_provider=None,
            how_i_work_loader=lambda: "", state_service=MagicMock(), gemini_service=MagicMock(),
            runpod_service=MagicMock(), default_draw_model=None, message=message, video_watcher=watcher,
        )}

    async def test_a_message_id_watches_its_video_attachment(self):
        attached = SimpleNamespace(attachments=[
            SimpleNamespace(content_type="image/png", filename="a.png", url="https://cdn/a.png"),
            SimpleNamespace(content_type="video/quicktime", filename="clip.mov", url="https://cdn/clip.mov"),
        ])
        message = SimpleNamespace(id=1, guild=None, author=SimpleNamespace(id=30),
                                  channel=SimpleNamespace(fetch_message=AsyncMock(return_value=attached)))
        watcher = AsyncMock(return_value=("a clip of a cat", 0.01))
        result = await self.tools(watcher, message)["watch_video"].run(
            {"url": "1565918944898846812", "focus": ""})
        watcher.assert_awaited_once_with("https://cdn/clip.mov", "")
        self.assertAlmostEqual(result.cost, 0.01)

    async def test_errors_come_back_as_results(self):
        message = SimpleNamespace(id=1, guild=None, author=SimpleNamespace(id=30), channel=SimpleNamespace(
            fetch_message=AsyncMock(side_effect=discord.NotFound(SimpleNamespace(status=404, reason="x"), "x"))))
        watcher = AsyncMock(side_effect=VideoError("no video there"))
        tools = self.tools(watcher, message)
        self.assertTrue((await tools["watch_video"].run({"url": "1565918944898846812"})).is_error)
        self.assertTrue((await tools["watch_video"].run({"url": "not a link"})).is_error)
        result = await tools["watch_video"].run({"url": "https://www.tiktok.com/@a/video/1"})
        self.assertIn("no video there", result.content[0]["text"])

    def test_attachment_lookup(self):
        self.assertIsNone(video_attachment_url(SimpleNamespace(attachments=[])))


class TestGeminiInteractionWatching(unittest.IsolatedAsyncioTestCase):
    async def test_runs_in_the_background_polls_and_prices_tool_use(self):
        service = GeminiService(api_key="key")
        usage = SimpleNamespace(total_input_tokens=20_000, total_cached_tokens=0, total_tool_use_tokens=30_000,
                                total_output_tokens=500, total_thought_tokens=1_500)
        done = MagicMock(status="completed", usage=usage)
        done.model_dump.return_value = {"outputs": [{"type": "text", "text": "Found it at [41:02]."}]}
        client = MagicMock()
        client.aio.interactions.create = AsyncMock(return_value=SimpleNamespace(id="i1", status="in_progress"))
        client.aio.interactions.get = AsyncMock(return_value=done)
        service._client = client

        with patch("src.services.gemini_service.INTERACTION_POLL_SECONDS", 0), cost_scope() as ledger:
            text, cost = await service.watch_video("files/abc", "when do they score?", agentic=True,
                                                   mime_type="video/mp4")

        self.assertEqual(text, "Found it at [41:02].")
        self.assertAlmostEqual(cost, (50_000 * 1.50 + 2_000 * 9.00) / 1_000_000)
        self.assertAlmostEqual(ledger.by_provider["gemini-direct"], cost)
        kwargs = client.aio.interactions.create.await_args.kwargs
        self.assertTrue(kwargs["background"])
        video = kwargs["input"][0]
        self.assertEqual((video["processing"], video["resolution"], video["mime_type"]), ("agentic", "low", "video/mp4"))
        client.aio.interactions.get.assert_awaited_once_with("i1")

    async def test_a_failed_interaction_is_an_error(self):
        service = GeminiService(api_key="key")
        client = MagicMock()
        client.aio.interactions.create = AsyncMock(return_value=SimpleNamespace(id="i1", status="failed", usage=None))
        service._client = client
        with self.assertRaises(GeminiServiceError):
            await service.watch_video("files/abc", "x", agentic=False)

    async def test_upload_waits_until_the_file_is_active(self):
        service = GeminiService(api_key="key")
        client = MagicMock()
        client.aio.files.upload = AsyncMock(return_value=SimpleNamespace(name="files/a", state=SimpleNamespace(name="PROCESSING")))
        client.aio.files.get = AsyncMock(return_value=SimpleNamespace(name="files/a", uri="u", state=SimpleNamespace(name="ACTIVE")))
        service._client = client
        with patch("src.services.gemini_service.FILE_POLL_SECONDS", 0):
            file = await service.upload_video("/tmp/v.mp4")
        self.assertEqual(file.uri, "u")
        self.assertEqual(client.aio.files.upload.await_args.kwargs["config"], {"mime_type": "video/mp4"})


if __name__ == "__main__":
    unittest.main()
