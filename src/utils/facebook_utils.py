import os
import re
import logging
import yt_dlp
import httpx
from typing import List, Optional

from src.config import TRANSCRIPT_PROXY, GROQ_API_KEY
from src.utils.cache_utils import PersistentCache

logger = logging.getLogger(__name__)

# Bounded persistent cache for Facebook transcripts: url -> transcript text or None
_facebook_cache = PersistentCache('facebook_transcripts')

def extract_facebook_urls(text: str) -> List[str]:
    """
    Extract Facebook video URLs from a block of text.
    Handles facebook.com, *.facebook.com, and fb.watch links.
    """
    if not text:
        return []

    # Match any http(s) URL on facebook.com (with or without subdomain) or fb.watch
    pattern = r'(https?://(?:[a-zA-Z0-9-]+\.)*facebook\.com/\S+|https?://fb\.watch/\S+)'

    matches = re.finditer(pattern, text)
    urls = []

    for match in matches:
        url = match.group(1)
        if url not in urls:
            urls.append(url)

    return urls

def get_facebook_transcript(url: str) -> Optional[tuple]:
    """
    Fetch the transcript for a Facebook video by downloading audio and processing with Groq via httpx.
    Results are cached to persistent disk.
    Returns (transcript_text, groq_response) or None if it fails (e.g. URL is not a video).
    """
    if url in _facebook_cache:
        cached_result = _facebook_cache[url]
        if cached_result is None:
            logger.debug(f"Cache hit for Facebook transcript failure: {url}")
            return None
        logger.debug(f"Cache hit for Facebook transcript: {url}")
        return cached_result, None

    if not GROQ_API_KEY:
        logger.error("GROQ_API_KEY is not set. Cannot transcribe Facebook videos.")
        return None

    logger.info(f"Fetching Facebook transcript for URL: {url}")

    # yt-dlp options (without proxy first)
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': '/tmp/facebook_%(id)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '64',
        }],
    }

    audio_file = None
    try:
        # Try direct download first
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            audio_file = ydl.prepare_filename(info)
            if not audio_file.endswith('.mp3'):
                audio_file = os.path.splitext(audio_file)[0] + '.mp3'

    except Exception as e:
        logger.warning(f"Direct download failed for {url}: {e}. Falling back to proxy.")
        if TRANSCRIPT_PROXY:
            ydl_opts['proxy'] = TRANSCRIPT_PROXY
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    audio_file = ydl.prepare_filename(info)
                    if not audio_file.endswith('.mp3'):
                        audio_file = os.path.splitext(audio_file)[0] + '.mp3'
            except Exception as e2:
                logger.warning(f"Proxy download also failed for {url}: {e2}. URL may not be a video.")
                return None
        else:
            logger.warning(f"Download failed and no proxy configured for {url}. URL may not be a video.")
            return None

    if not audio_file or not os.path.exists(audio_file):
        logger.error(f"Failed to find downloaded audio for Facebook URL: {url}")
        return None

    try:
        logger.info(f"Transcribing {audio_file} using Groq via httpx...")
        with open(audio_file, "rb") as file:
            audio_bytes = file.read()

        with httpx.Client(timeout=60.0) as client:
            files = {'file': (os.path.basename(audio_file), audio_bytes, 'audio/mpeg')}
            data_payload = {
                'model': 'whisper-large-v3-turbo',
                'temperature': '0',
                'response_format': 'text'
            }
            groq_headers = {'Authorization': f'Bearer {GROQ_API_KEY}'}

            groq_resp = client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers=groq_headers,
                data=data_payload,
                files=files
            )
            groq_resp.raise_for_status()
            transcript_text = groq_resp.text.strip()

        if not transcript_text:
            logger.warning(f"Groq returned an empty transcript for {url}")
            return None

        _facebook_cache[url] = transcript_text
        return transcript_text, groq_resp

    except Exception as e:
        logger.error(f"Unexpected error processing Facebook video {url}: {e}")
        return None
    finally:
        # Cleanup
        if audio_file and os.path.exists(audio_file):
            try:
                os.remove(audio_file)
                logger.debug(f"Removed temporary audio file: {audio_file}")
            except Exception as e:
                logger.warning(f"Failed to remove temporary file {audio_file}: {e}")
