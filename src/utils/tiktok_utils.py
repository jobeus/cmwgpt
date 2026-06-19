import re
import logging
from typing import List, Optional

from src.utils.cache_utils import PersistentCache
from src.utils.media_transcribe import download_and_transcribe_video

logger = logging.getLogger(__name__)

# Bounded persistent cache for TikTok transcripts: url -> transcript text
_tiktok_cache = PersistentCache('tiktok_transcripts')


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
    Fetch the transcript for a TikTok video by downloading audio and processing with Groq.
    Results are cached to persistent disk.
    """
    return download_and_transcribe_video(url, _tiktok_cache, label="TikTok", file_prefix="tiktok")
