"""Shared audio download + Groq transcription for every media source.

- :func:`transcribe_audio` sends audio bytes to Groq Whisper;
  :func:`transcribe_audio_file` handles any length by splitting into chunks.
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
import time
from typing import Callable, List, Optional

import httpx
import yt_dlp

from src.config import get_config
from src.utils.http_client import download_public

logger = logging.getLogger(__name__)

GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3-turbo"

# Whisper resamples everything to 16 kHz mono, so encoding to exactly that at
# 32 kbps costs no accuracy: ~14 MB per hour, i.e. ~1h50m per Groq upload.
SPEECH_AUDIO_ARGS = ["-vn", "-ar", "16000", "-ac", "1", "-b:a", "32k"]

# Groq rejects uploads over 25 MB (free tier). Anything bigger is split into
# chunks of this length (~7 MB each), transcribed in order, and joined, so
# media of any length still gets a full transcript.
GROQ_MAX_UPLOAD_BYTES = 24 * 1024 * 1024
CHUNK_SECONDS = 30 * 60

# Downloads that take longer than this are abandoned (transcription itself has
# no limit, so a long video still gets a full transcript once it's downloaded).
DOWNLOAD_TIMEOUT_SECONDS = 180


class DownloadTimeout(Exception):
    """A media download ran past DOWNLOAD_TIMEOUT_SECONDS."""


async def transcribe_audio(audio_bytes: bytes, filename: str, mime_type: str) -> Optional[str]:
    """Transcribe audio with Groq Whisper. Returns ``None`` if unavailable or empty."""
    api_key = get_config().groq_api_key
    if not api_key:
        logger.warning("GROQ_API_KEY is not set. Cannot transcribe audio.")
        return None

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
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


def _run_ffmpeg(args: List[str]) -> None:
    subprocess.run(["ffmpeg", "-y", *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def transcribe_audio_file(path: str, mime_type: str = "audio/mpeg") -> Optional[str]:
    """Transcribe an audio (or video) file of any length.

    Files Groq accepts as-is go up in one request; larger ones are re-encoded
    with ``SPEECH_AUDIO_ARGS``, split into ``CHUNK_SECONDS`` pieces, and transcribed in order.
    """
    if os.path.getsize(path) <= GROQ_MAX_UPLOAD_BYTES:
        with open(path, "rb") as f:
            return await transcribe_audio(f.read(), os.path.basename(path), mime_type)

    chunk_dir = tempfile.mkdtemp(prefix="chunks_")
    try:
        await asyncio.to_thread(_run_ffmpeg, [
            "-i", path, *SPEECH_AUDIO_ARGS,
            "-f", "segment", "-segment_time", str(CHUNK_SECONDS),
            os.path.join(chunk_dir, "chunk_%04d.mp3"),
        ])
        chunks = sorted(os.listdir(chunk_dir))
        logger.info(f"Transcribing {os.path.basename(path)} in {len(chunks)} chunks")
        parts = []
        for name in chunks:
            with open(os.path.join(chunk_dir, name), "rb") as f:
                text = await transcribe_audio(f.read(), name, "audio/mpeg")
            if text:
                parts.append(text)
        return " ".join(parts) or None
    finally:
        shutil.rmtree(chunk_dir, ignore_errors=True)


async def transcribe_video_url(video_url: str) -> Optional[str]:
    """Download a direct video URL (any length), extract its audio, and transcribe it."""
    if not get_config().groq_api_key:
        return None

    temp_dir = tempfile.mkdtemp(prefix="video_")
    try:
        mp4_path = os.path.join(temp_dir, "video.mp4")
        mp3_path = os.path.join(temp_dir, "audio.mp3")
        logger.info(f"Downloading video: {video_url}")
        await download_public(video_url, mp4_path, timeout=DOWNLOAD_TIMEOUT_SECONDS)
        await asyncio.to_thread(
            _run_ffmpeg, ["-i", mp4_path, *SPEECH_AUDIO_ARGS, mp3_path])
        return await transcribe_audio_file(mp3_path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _download_deadline_hook() -> Callable[[dict], None]:
    """A yt-dlp progress hook that aborts the download after DOWNLOAD_TIMEOUT_SECONDS."""
    started = time.monotonic()

    def hook(_progress: dict) -> None:
        if time.monotonic() - started > DOWNLOAD_TIMEOUT_SECONDS:
            raise DownloadTimeout(f"download took longer than {DOWNLOAD_TIMEOUT_SECONDS}s")
    return hook


def _build_ydl_opts(file_prefix: str, temp_dir: str) -> dict:
    return {
        "format": "bestaudio/best[vcodec=h264]/best",
        "outtmpl": os.path.join(temp_dir, f"{file_prefix}_%(id)s_%(format_id)s.%(ext)s"),
        "overwrites": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 60,
        "progress_hooks": [_download_deadline_hook()],
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "32",
        }],
        # Same speech-sized encoding as SPEECH_AUDIO_ARGS (16 kHz mono).
        "postprocessor_args": {"extractaudio": ["-ar", "16000", "-ac", "1"]},
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
            if "took longer than" in str(e):
                logger.warning(f"Gave up downloading {label} video {url}: {e}")
                return None
            if not cfg.transcript_proxy:
                logger.warning(f"Download failed and no proxy configured for {url}: {e}")
                return None
            logger.warning(f"Direct download failed for {url}: {e}. Falling back to proxy.")
            try:
                audio_file = await asyncio.to_thread(
                    _download_audio, url,
                    {**ydl_opts, "proxy": cfg.transcript_proxy, "progress_hooks": [_download_deadline_hook()]})
            except Exception as e2:
                logger.warning(f"Proxy download also failed for {url}: {e2}. URL may not be a video.")
                return None

        if not audio_file:
            logger.warning(f"No audio downloaded for {label} URL (not a video?): {url}")
            return None

        transcript = await transcribe_audio_file(audio_file)
        if transcript:
            cache[url] = transcript
        return transcript
    except Exception as e:
        logger.error(f"Unexpected error processing {label} video {url}: {e}")
        return None
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
