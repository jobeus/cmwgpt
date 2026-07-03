"""Shared audio-download + Groq transcription pipeline for video platforms.

TikTok and Facebook (and any other yt-dlp based source) share an identical
flow: check a persistent cache, download bestaudio via yt-dlp with a proxy
fallback, transcribe the resulting mp3 with Groq Whisper, and return a uniform
result dict. This module is the single source of truth for that flow.
"""

import os
import logging
import shutil
import tempfile
from typing import Optional

import httpx
import yt_dlp

from src.config import get_config
from src.db.logger import build_artifact
from src.utils.http_client import create_sync_client

logger = logging.getLogger(__name__)

GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


def _delete_cache_entry(cache, key: str) -> None:
    if hasattr(cache, "delete"):
        cache.delete(key)
    elif key in cache:
        del cache[key]


def _build_ydl_opts(file_prefix: str, temp_dir: str) -> dict:
    return {
        "format": "bestaudio/best[vcodec=h264]/best",
        "outtmpl": os.path.join(temp_dir, f"{file_prefix}_%(id)s_%(format_id)s.%(ext)s"),
        "overwrites": True,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "64",
        }],
    }


def _download_audio(url: str, ydl_opts: dict) -> tuple[Optional[str], Optional[dict]]:
    """Run yt-dlp and return (audio_file_path, info). Raises on failure."""
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            audio_file = ydl.prepare_filename(info)
            if not audio_file.endswith(".mp3"):
                audio_file = os.path.splitext(audio_file)[0] + ".mp3"
        return audio_file, info
    except Exception as e:
        # yt-dlp wraps all post-processing exceptions in a DownloadError.
        # If ffprobe fails to find an audio codec, the format selected genuinely has no audio.
        # This often happens on TikTok where H265 streams are misreported as having AAC.
        # We retry explicitly forcing a fallback format.
        if "unable to obtain file audio codec" in str(e):
            logger.warning(f"Audio extraction failed for format. Retrying with strict h264 fallback for {url}")
            fallback_opts = dict(ydl_opts)
            # Force standard video formats which reliably contain audio streams
            fallback_opts["format"] = "best[vcodec=h264]"
            with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                audio_file = ydl.prepare_filename(info)
                if not audio_file.endswith(".mp3"):
                    audio_file = os.path.splitext(audio_file)[0] + ".mp3"
            return audio_file, info
        raise


def download_and_transcribe_video(url: str, cache, *, label: str, file_prefix: str) -> Optional[dict]:
    """Download a video's audio and transcribe it with Groq.

    Args:
        url: The video URL.
        cache: A persistent cache (mapping of url -> transcript text).
        label: Human-readable platform name for log messages (e.g. "TikTok").
        file_prefix: Temp-file name prefix for the downloaded audio.

    Returns a uniform result dict, or ``None`` on any failure.
    """
    if url in cache:
        cached_result = cache[url]
        if cached_result is None:
            logger.debug(f"Discarding legacy {label} transcript failure sentinel for: {url}")
            _delete_cache_entry(cache, url)
        else:
            logger.debug(f"Cache hit for {label} transcript: {url}")
            return {
                "transcript_text": cached_result,
                "groq_response": None,
                "source_url": url,
                "download_strategy": "cache",
                "from_cache": True,
            }

    cfg = get_config()
    if not cfg.groq_api_key:
        logger.error(f"GROQ_API_KEY is not set. Cannot transcribe {label} videos.")
        return None

    logger.info(f"Fetching {label} transcript for URL: {url}")

    # Use a private per-call temp directory so concurrent requests for the same
    # video never clobber each other's files (yt-dlp overwrites shared paths).
    temp_dir = tempfile.mkdtemp(prefix=f"{file_prefix}_")
    try:
        ydl_opts = _build_ydl_opts(file_prefix, temp_dir)
        audio_file = None
        info = None
        download_strategy = "direct"
        try:
            audio_file, info = _download_audio(url, ydl_opts)
        except Exception as e:
            logger.warning(f"Direct download failed for {url}: {e}. Falling back to proxy.")
            if not cfg.transcript_proxy:
                logger.warning(f"Download failed and no proxy configured for {url}. URL may not be a video.")
                return None
            download_strategy = "proxy"
            ydl_opts["proxy"] = cfg.transcript_proxy
            try:
                audio_file, info = _download_audio(url, ydl_opts)
            except Exception as e2:
                logger.warning(f"Proxy download also failed for {url}: {e2}. URL may not be a video.")
                return None

        if not audio_file or not os.path.exists(audio_file):
            logger.error(f"Failed to find downloaded audio for {label} URL: {url}")
            return None

        logger.info(f"Transcribing {audio_file} using Groq via httpx...")
        with open(audio_file, "rb") as file:
            audio_bytes = file.read()

        sync_client = create_sync_client(timeout=httpx.Timeout(60.0))
        with sync_client:
            files = {"file": (os.path.basename(audio_file), audio_bytes, "audio/mpeg")}
            data_payload = {
                "model": "whisper-large-v3-turbo",
                "temperature": "0",
                "response_format": "text",
            }
            groq_headers = {"Authorization": f"Bearer {cfg.groq_api_key}"}

            groq_resp = sync_client.post(
                GROQ_TRANSCRIBE_URL,
                headers=groq_headers,
                data=data_payload,
                files=files,
            )
            groq_resp.raise_for_status()
            transcript_text = groq_resp.text.strip()

        if not transcript_text:
            logger.warning(f"Groq returned an empty transcript for {url}")
            return None

        cache[url] = transcript_text
        return {
            "transcript_text": transcript_text,
            "groq_response": groq_resp,
            "pending_logs": sync_client._transport.pending_logs.copy(),
            "source_url": url,
            "download_strategy": download_strategy,
            "from_cache": False,
            "source_metadata": {
                "id": info.get("id") if info else None,
                "title": info.get("title") if info else None,
                "duration": info.get("duration") if info else None,
                "uploader": info.get("uploader") if info else None,
                "extractor": info.get("extractor") if info else None,
                "webpage_url": info.get("webpage_url") if info else None,
            },
            "audio_artifact": build_artifact(
                name=os.path.basename(audio_file),
                media_type="audio/mpeg",
                data=audio_bytes,
                extra={
                    "source_url": url,
                    "path": audio_file,
                },
            ),
        }

    except Exception as e:
        logger.error(f"Unexpected error processing {label} video {url}: {e}")
        return None
    finally:
        try:
            shutil.rmtree(temp_dir)
            logger.debug(f"Removed temporary directory: {temp_dir}")
        except OSError as e:
            logger.warning(f"Failed to remove temporary directory {temp_dir}: {e}")
