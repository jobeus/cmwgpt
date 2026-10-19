import re
import logging
from typing import List, Optional

from src.utils.cache_utils import PersistentCache, skip_recent_failures
from src.utils.media_transcribe import download_and_transcribe_video

logger = logging.getLogger(__name__)

# Bounded persistent cache for Facebook transcripts: url -> transcript text
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


@skip_recent_failures("facebook")
async def get_facebook_transcript(url: str, *, cache_only: bool = False) -> Optional[str]:
    """Transcribe a Facebook video (downloaded with yt-dlp, transcribed by Groq).

    Results are cached to persistent disk. Returns ``None`` on failure (e.g.
    the URL is not a video), or on a cache miss when ``cache_only`` is set.
    """
    return await download_and_transcribe_video(
        url, _facebook_cache, label="Facebook", file_prefix="facebook", cache_only=cache_only)
