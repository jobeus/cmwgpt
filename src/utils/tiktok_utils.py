import os
import re
import logging
import yt_dlp
import httpx
from typing import List, Optional

from src.config import TRANSCRIPT_PROXY, GROQ_API_KEY
from src.utils.cache_utils import PersistentCache
from src.db.logger import build_artifact

logger = logging.getLogger(__name__)

# Bounded persistent cache for TikTok transcripts: url -> transcript text
_tiktok_cache = PersistentCache('tiktok_transcripts')


def _delete_cache_entry(cache, key: str) -> None:
    if hasattr(cache, 'delete'):
        cache.delete(key)
    elif key in cache:
        del cache[key]

def extract_tiktok_urls(text: str) -> List[str]:
    """
    Extract TikTok video URLs from a block of text.
    Handles vt.tiktok.com and www.tiktok.com links.
    """
    if not text:
        return []

    # Regex to match TikTok video URLs
    pattern = r'(https?://(?:vt\.tiktok\.com/[a-zA-Z0-9]+/?|www\.tiktok\.com/@[a-zA-Z0-9_.]+/video/\d+/?))'

    matches = re.finditer(pattern, text)
    urls = []

    for match in matches:
        url = match.group(1)
        if url not in urls:
            urls.append(url)

    return urls

def get_tiktok_transcript(url: str) -> Optional[dict]:
    """
    Fetch the transcript for a TikTok video by downloading audio and processing with Groq via httpx.
    Results are cached to persistent disk.
    """
    if url in _tiktok_cache:
        cached_result = _tiktok_cache[url]
        if cached_result is None:
            logger.debug(f"Discarding legacy TikTok transcript failure sentinel for: {url}")
            _delete_cache_entry(_tiktok_cache, url)
        else:
            logger.debug(f"Cache hit for TikTok transcript: {url}")
            return {
                "transcript_text": cached_result,
                "groq_response": None,
                "source_url": url,
                "download_strategy": "cache",
                "from_cache": True,
            }


    if not GROQ_API_KEY:
        logger.error("GROQ_API_KEY is not set. Cannot transcribe TikTok videos.")
        return None

    logger.info(f"Fetching TikTok transcript for URL: {url}")
    
    # yt-dlp options (without proxy first)
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': '/tmp/tiktok_%(id)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '64',
        }],
    }

    audio_file = None
    info = None
    download_strategy = "direct"
    try:
        # Try direct download first
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # The actual downloaded file might be a .mp3 now instead of .ext
            audio_file = ydl.prepare_filename(info)
            # yt-dlp's prepare_filename with postprocessors might return the original extension path,
            # so we ensure we have the correct mp3 path
            if not audio_file.endswith('.mp3'):
                audio_file = os.path.splitext(audio_file)[0] + '.mp3'
                
    except Exception as e:
        logger.warning(f"Direct download failed for {url}: {e}. Falling back to proxy.")
        if TRANSCRIPT_PROXY:
            download_strategy = "proxy"
            ydl_opts['proxy'] = TRANSCRIPT_PROXY
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    audio_file = ydl.prepare_filename(info)
                    if not audio_file.endswith('.mp3'):
                        audio_file = os.path.splitext(audio_file)[0] + '.mp3'
            except Exception as e2:
                logger.error(f"Proxy download failed for {url}: {e2}")
                return None
        else:
            logger.error(f"No proxy configured to fall back to for {url}")
            return None

    if not audio_file or not os.path.exists(audio_file):
        logger.error(f"Failed to find downloaded audio for TikTok URL: {url}")
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

        # Log it right here synchronously, but wait, log_api_request is async.
        # I need to return the request/response data to downloader_utils to log asynchronously.
        
        _tiktok_cache[url] = transcript_text
        return {
            "transcript_text": transcript_text,
            "groq_response": groq_resp,
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
        logger.error(f"Unexpected error processing TikTok video {url}: {e}")
        return None
    finally:
        # Cleanup
        if audio_file and os.path.exists(audio_file):
            try:
                os.remove(audio_file)
                logger.debug(f"Removed temporary audio file: {audio_file}")
            except Exception as e:
                logger.warning(f"Failed to remove temporary file {audio_file}: {e}")
