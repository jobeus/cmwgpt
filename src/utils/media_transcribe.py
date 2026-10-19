"""Shared audio download + Groq transcription for every media source.

- :func:`transcribe_audio` sends audio bytes to Groq Whisper.
- :func:`transcribe_video_url` downloads a direct video URL (tweet/Instagram
  media), converts it to mp3 with ffmpeg, and transcribes it.
- :func:`download_and_transcribe_video` does the same for pages yt-dlp
  understands (TikTok, Facebook), with a proxy fallback and a persistent cache.
"""

import asyncio
import logging
import os
import shutil
import subprocess  # nosec B404
import tempfile
from typing import Optional

import httpx
import yt_dlp

from src.config import get_config
from src.utils.http_client import download_public

logger = logging.getLogger(__name__)

GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3-turbo"

# Groq rejects uploads over 25 MB; at 64 kbps mp3 that is ~50 minutes, so skip
# anything longer before spending the download and ffmpeg time on it.
MAX_MEDIA_DURATION_SECONDS = 45 * 60
MAX_VIDEO_DOWNLOAD_BYTES = 200 * 1024 * 1024
FFMPEG_TIMEOUT_SECONDS = 300


async def transcribe_audio(audio_bytes: bytes, filename: str, mime_type: str) -> Optional[str]:
    """Transcribe audio with Groq Whisper. Returns ``None`` if unavailable or empty."""
    api_key = get_config().groq_api_key
    if not api_key:
        logger.warning("GROQ_API_KEY is not set. Cannot transcribe audio.")
        return None

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        response = await client.post(
            GROQ_TRANSCRIBE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            data={"model": GROQ_MODEL, "temperature": "0", "response_format": "text"},
            files={"file": (filename, audio_bytes, mime_type)},
        )
        response.raise_for_status()
    transcript = response.text.strip()
    if not transcript:
        logger.warning(f"Groq returned an empty transcript for {filename}")
        return None
    return transcript


async def transcribe_video_url(video_url: str) -> Optional[str]:
    """Download a direct video URL, extract its audio, and transcribe it."""
    if not get_config().groq_api_key:
        return None

    temp_dir = tempfile.mkdtemp(prefix="video_")
    try:
        mp4_path = os.path.join(temp_dir, "video.mp4")
        mp3_path = os.path.join(temp_dir, "audio.mp3")
        logger.info(f"Downloading video: {video_url}")
        await download_public(video_url, mp4_path, max_bytes=MAX_VIDEO_DOWNLOAD_BYTES)

        await asyncio.to_thread(
            subprocess.run,
            [
                "ffmpeg", "-y", "-i", mp4_path,
                "-t", str(MAX_MEDIA_DURATION_SECONDS),
                "-vn", "-ar", "44100", "-ac", "2", "-b:a", "64k",
                mp3_path,
            ],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
        with open(mp3_path, "rb") as f:
            audio_bytes = f.read()
        return await transcribe_audio(audio_bytes, "audio.mp3", "audio/mpeg")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _reject_long_media(info: dict, *, incomplete: bool = False) -> Optional[str]:
    duration = info.get("duration")
    if duration and duration > MAX_MEDIA_DURATION_SECONDS:
        return f"media is {int(duration)}s, longer than {MAX_MEDIA_DURATION_SECONDS}s"
    return None


def _build_ydl_opts(file_prefix: str, temp_dir: str) -> dict:
    return {
        "format": "bestaudio/best[vcodec=h264]/best",
        "outtmpl": os.path.join(temp_dir, f"{file_prefix}_%(id)s_%(format_id)s.%(ext)s"),
        "overwrites": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": MAX_VIDEO_DOWNLOAD_BYTES,
        "match_filter": _reject_long_media,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "64",
        }],
    }


def _run_ydl(url: str, ydl_opts: dict) -> Optional[str]:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if not info:
            return None
        audio_file = os.path.splitext(ydl.prepare_filename(info))[0] + ".mp3"
    return audio_file if os.path.exists(audio_file) else None


def _download_audio(url: str, ydl_opts: dict) -> Optional[str]:
    """Run yt-dlp and return the mp3 path, or ``None`` if nothing was downloaded."""
    try:
        return _run_ydl(url, ydl_opts)
    except Exception as e:
        # TikTok sometimes misreports H265 streams as carrying AAC audio, so
        # ffprobe finds no audio codec; retry forcing a format that has audio.
        if "unable to obtain file audio codec" not in str(e):
            raise
        logger.warning(f"Audio extraction failed for format. Retrying with strict h264 fallback for {url}")
        return _run_ydl(url, {**ydl_opts, "format": "best[vcodec=h264]"})


async def download_and_transcribe_video(
    url: str, cache, *, label: str, file_prefix: str, cache_only: bool = False
) -> Optional[str]:
    """Download a video page's audio with yt-dlp and transcribe it with Groq.

    Args:
        url: The video URL.
        cache: A persistent cache (mapping of url -> transcript text).
        label: Human-readable platform name for log messages (e.g. "TikTok").
        file_prefix: Temp-file name prefix for the downloaded audio.
        cache_only: Only return a cached transcript; never download.

    Returns the transcript, or ``None`` on any failure.
    """
    cached = cache.get(url)
    if cached is not None or cache_only:
        return cached

    cfg = get_config()
    if not cfg.groq_api_key:
        logger.error(f"GROQ_API_KEY is not set. Cannot transcribe {label} videos.")
        return None

    logger.info(f"Fetching {label} transcript for URL: {url}")
    # A private per-call temp directory, so concurrent requests for the same
    # video never clobber each other's files.
    temp_dir = tempfile.mkdtemp(prefix=f"{file_prefix}_")
    try:
        ydl_opts = _build_ydl_opts(file_prefix, temp_dir)
        try:
            audio_file = await asyncio.to_thread(_download_audio, url, ydl_opts)
        except Exception as e:
            if not cfg.transcript_proxy:
                logger.warning(f"Download failed and no proxy configured for {url}: {e}")
                return None
            logger.warning(f"Direct download failed for {url}: {e}. Falling back to proxy.")
            try:
                audio_file = await asyncio.to_thread(
                    _download_audio, url, {**ydl_opts, "proxy": cfg.transcript_proxy})
            except Exception as e2:
                logger.warning(f"Proxy download also failed for {url}: {e2}. URL may not be a video.")
                return None

        if not audio_file:
            logger.warning(f"No audio downloaded for {label} URL (filtered or not a video): {url}")
            return None

        with open(audio_file, "rb") as f:
            audio_bytes = f.read()
        transcript = await transcribe_audio(audio_bytes, os.path.basename(audio_file), "audio/mpeg")
        if transcript:
            cache[url] = transcript
        return transcript
    except Exception as e:
        logger.error(f"Unexpected error processing {label} video {url}: {e}")
        return None
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
