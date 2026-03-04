import os
import re
import logging
import yt_dlp
from typing import List, Optional
from groq import Groq

from src.config import TRANSCRIPT_PROXY, GROQ_API_KEY
from src.utils.cache_utils import PersistentCache

logger = logging.getLogger(__name__)

MAX_CACHE_SIZE = 100
# Bounded persistent cache for TikTok transcripts: url -> transcript text or None
_tiktok_cache = PersistentCache('tiktok_transcripts', MAX_CACHE_SIZE)

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

def get_tiktok_transcript(url: str) -> Optional[str]:
    """
    Fetch the transcript for a TikTok video by downloading audio and processing with Groq.
    Results are cached to persistent disk.
    """
    if url in _tiktok_cache:
        cached_result = _tiktok_cache[url]
        if cached_result is None:
            logger.debug(f"Cache hit for TikTok transcript failure: {url}")
            return None
        logger.debug(f"Cache hit for TikTok transcript: {url}")
        return cached_result

    def cache_failure(u: str):
        _tiktok_cache[u] = None

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
            ydl_opts['proxy'] = TRANSCRIPT_PROXY
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    audio_file = ydl.prepare_filename(info)
                    if not audio_file.endswith('.mp3'):
                        audio_file = os.path.splitext(audio_file)[0] + '.mp3'
            except Exception as e2:
                logger.error(f"Proxy download failed for {url}: {e2}")
                cache_failure(url)
                return None
        else:
            logger.error(f"No proxy configured to fall back to for {url}")
            cache_failure(url)
            return None

    if not audio_file or not os.path.exists(audio_file):
        logger.error(f"Failed to find downloaded audio for TikTok URL: {url}")
        cache_failure(url)
        return None

    try:
        # Groq client transcription
        groq_client = Groq(api_key=GROQ_API_KEY)
        
        logger.info(f"Transcribing {audio_file} using Groq...")
        with open(audio_file, "rb") as file:
            transcription = groq_client.audio.transcriptions.create(
                file=(os.path.basename(audio_file), file.read()),
                model="whisper-large-v3-turbo",
                temperature=0,
                response_format="text",
            )
            
        transcript_text = transcription.strip()
        
        if not transcript_text:
            logger.warning(f"Groq returned an empty transcript for {url}")
            cache_failure(url)
            return None

        _tiktok_cache[url] = transcript_text
        return transcript_text

    except Exception as e:
        logger.error(f"Unexpected error processing TikTok video {url}: {e}")
        cache_failure(url)
        return None
    finally:
        # Cleanup
        if audio_file and os.path.exists(audio_file):
            try:
                os.remove(audio_file)
                logger.debug(f"Removed temporary audio file: {audio_file}")
            except Exception as e:
                logger.warning(f"Failed to remove temporary file {audio_file}: {e}")
