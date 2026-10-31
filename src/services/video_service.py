"""
Video Service - Gemini watches a video, picture and sound, and answers about it.

Behind the ``watch_video`` and ``summarize_youtube`` tools. Any video the bot can
transcribe with Groq can be watched instead:

- YouTube: Gemini reads it straight from its URL.
- Twitter/X and Instagram: the video file comes from the same RapidAPI lookups as the
  posts; TikTok, Facebook, Reddit and other pages yt-dlp understands are downloaded with
  yt-dlp (through ``TRANSCRIPT_PROXY`` if a direct download fails); direct video links
  and Discord attachments are downloaded as they are.
- Downloaded videos are re-encoded small (``REENCODE_ARGS``: 480p, 10 fps, mono audio),
  uploaded to Gemini's Files API, watched, and deleted.

Videos under ``AGENTIC_MIN_SECONDS`` are watched statically: Gemini samples the whole
thing at 1 fps, which is best for a short clip or a summary. Longer ones are watched
agentically: Gemini moves around the timeline itself, loading frames, audio and the
transcript where they matter, which Google says costs far fewer tokens on long videos and
does better at finding particular moments. Both run at low media resolution. Answers
are cached per video and focus in ``cmwgpt:cache:video_watches``.
"""

import asyncio
import logging
import os
import re
import shutil
import subprocess  # nosec B404
import tempfile
from typing import Any, Optional, Tuple
from urllib.parse import urlparse

import yt_dlp

from src.config import get_config
from src.services.gemini_service import VIDEO_SUMMARY_PROMPT, GeminiServiceError
from src.utils.cache_utils import PersistentCache
from src.utils.http_client import download_public
from src.utils.instagram_utils import extract_instagram_urls, find_instagram_video_url
from src.utils.media_transcribe import DOWNLOAD_TIMEOUT_SECONDS, _download_deadline_hook
from src.utils.twitter_utils import extract_twitter_urls, find_tweet_video_url
from src.utils.youtube_utils import extract_video_ids, video_url as youtube_url

logger = logging.getLogger(__name__)

# Long enough that moving around the timeline beats watching every second (Google's
# guidance is ~5-10 minutes).
AGENTIC_MIN_SECONDS = 5 * 60
# Gemini handles up to ~3 hours at low resolution.
MAX_VIDEO_SECONDS = 3 * 3600
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024
# Gemini looks at 1 fps (more when agentic zooms in) at low resolution, so this loses
# nothing it would see and keeps uploads small.
REENCODE_ARGS = [
    "-vf", "scale=-2:'min(480,ih)'", "-r", "10",
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
    "-c:a", "aac", "-ac", "1", "-b:a", "48k",
    "-movflags", "+faststart",
]
VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi", ".3gp", ".mpeg", ".mpg", ".flv", ".wmv")

_watch_cache = PersistentCache("video_watches")


class VideoError(Exception):
    """The video couldn't be fetched or watched; the message says why, for the model."""


def watch_prompt(focus: str) -> str:
    prompt = VIDEO_SUMMARY_PROMPT
    if focus.strip():
        prompt += f"\n\nPay special attention to this, and answer it if it's a question: {focus.strip()}"
    return prompt


def is_direct_video_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.path.lower().endswith(VIDEO_EXTENSIONS) or host.endswith(
        ("cdn.discordapp.com", "media.discordapp.net"))


def _run(args: list) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout  # nosec B603


def _probe_seconds(path: str) -> Optional[float]:
    try:
        out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path])
        return float(out.strip())
    except (subprocess.CalledProcessError, ValueError, OSError):
        return None


def _youtube_seconds(url: str) -> Optional[float]:
    """A YouTube video's length from its page, without downloading it."""
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
        return float(info.get("duration")) if info and info.get("duration") else None
    except Exception as e:
        logger.info(f"[video] Couldn't get the length of {url}: {e}")
        return None


def _ytdlp_video(url: str, temp_dir: str, proxy: Optional[str]) -> Optional[str]:
    opts: dict = {
        # Small is fine: it's re-encoded to 480p.
        "format": "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(temp_dir, "video_%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 60,
        "max_filesize": MAX_DOWNLOAD_BYTES,
        "progress_hooks": [_download_deadline_hook()],
    }
    if proxy:
        opts["proxy"] = proxy
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if not info:
            return None
        path = ydl.prepare_filename(info)
    if not os.path.exists(path):
        merged = os.path.splitext(path)[0] + ".mp4"
        path = merged if os.path.exists(merged) else ""
    return path or None


async def download_video(url: str, temp_dir: str) -> str:
    """Download the video behind ``url`` into ``temp_dir``. Raises ``VideoError``."""
    source = url
    if extract_twitter_urls(url):
        source = await find_tweet_video_url(url) or ""
        if not source:
            raise VideoError("That tweet has no video I can get.")
    elif extract_instagram_urls(url):
        source = await find_instagram_video_url(url) or ""
        if not source:
            raise VideoError("That post has no video I can get.")
    if source != url or is_direct_video_url(source):
        path = os.path.join(temp_dir, "download")
        try:
            await download_public(source, path, max_bytes=MAX_DOWNLOAD_BYTES, timeout=DOWNLOAD_TIMEOUT_SECONDS)
        except Exception as e:
            raise VideoError(f"Couldn't download the video: {e}") from e
        return path
    found: Optional[str]
    try:
        found = await asyncio.to_thread(_ytdlp_video, url, temp_dir, None)
    except Exception as e:
        proxy = get_config().transcript_proxy
        if not proxy or "took longer than" in str(e):
            raise VideoError(f"Couldn't download a video from that link: {e}") from e
        logger.warning(f"[video] Direct download of {url} failed ({e}); trying the proxy")
        try:
            found = await asyncio.to_thread(_ytdlp_video, url, temp_dir, proxy)
        except Exception as e2:
            raise VideoError(f"Couldn't download a video from that link: {e2}") from e2
    if not found:
        raise VideoError("There's no video at that link.")
    return found


class VideoWatcher:
    """Has Gemini watch videos, statically or agentically by length."""

    def __init__(self, gemini_service: Any):
        self._gemini = gemini_service

    async def watch(self, url: str, focus: str = "") -> Tuple[str, float]:
        """Watch the video at ``url``, with ``focus`` as what to look for. Returns ``(answer, cost)``.

        Raises ``VideoError`` with a reason the model can pass on.
        """
        key = f"{url}:{' '.join(focus.lower().split())}"
        cached = await _watch_cache.get(key)
        if cached is not None:
            return cached, 0.0
        ids = extract_video_ids(url)
        if ids:
            answer, cost = await self._watch_youtube(ids[0], focus)
        else:
            answer, cost = await self._watch_file(url, focus)
        await _watch_cache.set(key, answer)
        return answer, cost

    async def summarize_youtube(self, video_url: str, focus: str = "") -> Tuple[str, float]:
        """The ``summarize_youtube`` summarizer: like :meth:`watch` for a YouTube link (uncached here)."""
        ids = extract_video_ids(video_url)
        if not ids:
            raise VideoError("That isn't a YouTube link.")
        return await self._watch_youtube(ids[0], focus)

    async def _watch_youtube(self, video_id: str, focus: str) -> Tuple[str, float]:
        url = youtube_url(video_id)
        seconds = await asyncio.to_thread(_youtube_seconds, url)
        if seconds and seconds > MAX_VIDEO_SECONDS:
            raise VideoError(f"That video is {seconds / 3600:.1f} hours long; I can watch up to 3.")
        if seconds and seconds >= AGENTIC_MIN_SECONDS:
            try:
                return await self._gemini.watch_video(url, watch_prompt(focus), agentic=True)
            except GeminiServiceError as e:
                logger.warning(f"[video] Agentic watch of {url} failed ({e}); watching it statically")
        try:
            return await self._gemini.summarize_youtube(url, focus)
        except GeminiServiceError as e:
            raise VideoError(str(e)) from e

    async def _watch_file(self, url: str, focus: str) -> Tuple[str, float]:
        temp_dir = tempfile.mkdtemp(prefix="watch_")
        uploaded = None
        try:
            downloaded = await download_video(url, temp_dir)
            seconds = await asyncio.to_thread(_probe_seconds, downloaded)
            if seconds is None:
                raise VideoError("That file isn't a video I can read.")
            if seconds > MAX_VIDEO_SECONDS:
                raise VideoError(f"That video is {seconds / 3600:.1f} hours long; I can watch up to 3.")
            small = os.path.join(temp_dir, "watch.mp4")
            try:
                await asyncio.to_thread(_run, ["ffmpeg", "-y", "-i", downloaded, *REENCODE_ARGS, small])
            except subprocess.CalledProcessError as e:
                raise VideoError("Couldn't re-encode that video.") from e
            logger.info(
                f"[video] {url}: {seconds:.0f}s, re-encoded {os.path.getsize(downloaded) // 1024} KB -> "
                f"{os.path.getsize(small) // 1024} KB")
            try:
                uploaded = await self._gemini.upload_video(small)
                return await self._gemini.watch_video(
                    uploaded.uri, watch_prompt(focus), agentic=seconds >= AGENTIC_MIN_SECONDS,
                    mime_type="video/mp4")
            except GeminiServiceError as e:
                raise VideoError(str(e)) from e
        finally:
            if uploaded is not None:
                await self._gemini.delete_file(uploaded.name)
            shutil.rmtree(temp_dir, ignore_errors=True)


def video_attachment_url(message: Any) -> Optional[str]:
    """The URL of the first video attached to a Discord message, if any."""
    for attachment in getattr(message, "attachments", None) or []:
        content_type = (getattr(attachment, "content_type", None) or "").lower()
        name = (getattr(attachment, "filename", None) or "").lower()
        if content_type.startswith("video/") or name.endswith(VIDEO_EXTENSIONS):
            return attachment.url
    return None


_MESSAGE_ID = re.compile(r"^\d{15,25}$")


def looks_like_message_id(value: str) -> bool:
    return bool(_MESSAGE_ID.match(value.strip()))
