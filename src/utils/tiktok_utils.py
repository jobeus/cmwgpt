import re
import logging
from typing import List, Optional

from src.utils.cache_utils import PersistentCache, skip_recent_failures
from src.utils.media_transcribe import download_and_transcribe_video

logger = logging.getLogger(__name__)

# Redis cache for TikTok transcripts: url -> transcript text
_tiktok_cache = PersistentCache('tiktok_transcripts')


def extract_tiktok_urls(text: str) -> List[str]:
    """
    Extract TikTok video URLs from a block of text.
    Handles vt.tiktok.com / vm.tiktok.com short links, tiktok.com/t/ share
    links, and full (www.)tiktok.com/@user/video/ID links.
    """
    if not text:
        return []

    # Regex to match TikTok video URLs
    pattern = (
        r'(https?://(?:'
        r'(?:vt|vm)\.tiktok\.com/[a-zA-Z0-9]+/?'
        r'|(?:www\.)?tiktok\.com/t/[a-zA-Z0-9]+/?'
        r'|(?:www\.)?tiktok\.com/@[a-zA-Z0-9_.]+/video/\d+/?'
        r'))'
    )

    matches = re.finditer(pattern, text)
    urls = []

    for match in matches:
        url = match.group(1)
        if url not in urls:
            urls.append(url)

    return urls


@skip_recent_failures("tiktok")
async def get_tiktok_transcript(url: str, *, cache_only: bool = False) -> Optional[str]:
    """Transcribe a TikTok video (downloaded with yt-dlp, transcribed by Groq).

    Results are cached in Redis. Returns ``None`` on failure (e.g.
    the URL is not a video), or on a cache miss when ``cache_only`` is set.
    """
    return await download_and_transcribe_video(
        url, _tiktok_cache, label="TikTok", file_prefix="tiktok", cache_only=cache_only)
