import re
import logging
from typing import List, Optional

from src.utils.cache_utils import PersistentCache
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


def get_facebook_transcript(url: str) -> Optional[dict]:
    """
    Fetch the transcript for a Facebook video by downloading audio and processing with Groq.
    Results are cached to persistent disk. Returns ``None`` if it fails
    (e.g. the URL is not a video).
    """
    return download_and_transcribe_video(url, _facebook_cache, label="Facebook", file_prefix="facebook")
