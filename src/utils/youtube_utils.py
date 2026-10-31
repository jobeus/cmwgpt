import re
import logging
import asyncio
from typing import List, Optional
from youtube_transcript_api import YouTubeTranscriptApi
from src.config import get_config
from src.utils.cache_utils import PersistentCache, skip_recent_failures

logger = logging.getLogger(__name__)

# Redis cache for transcripts: video_id -> transcript text
_transcript_cache = PersistentCache('youtube_transcripts')


def extract_video_ids(text: str) -> List[str]:
    """
    Extract YouTube video IDs from a block of text.
    Handles standard youtube.com links and youtu.be short links.
    """
    if not text:
        return []

    # Regex to match youtube video IDs from various URL formats
    pattern = r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:shorts\/|[^\/\n\s]+\/\S+\/|(?:v|e(?:mbed)?)\/|\S*?[?\&]v=)|youtu\.be\/)([a-zA-Z0-9_-]{11})'

    matches = re.finditer(pattern, text)
    video_ids = []

    for match in matches:
        vid_id = match.group(1)
        if vid_id not in video_ids:
            video_ids.append(vid_id)

    return video_ids


@skip_recent_failures("youtube")
async def get_transcript(video_id: str, *, cache_only: bool = False) -> Optional[str]:
    """
    Fetch the transcript for a YouTube video as a single string.
    Results are cached in Redis. Returns ``None`` on failure, or on a
    cache miss when ``cache_only`` is set.
    """
    cached = await _transcript_cache.get(video_id)
    if cached is not None or cache_only:
        return cached

    try:
        transcript_proxy = get_config().transcript_proxy

        def _fetch():
            if transcript_proxy:
                from youtube_transcript_api.proxies import GenericProxyConfig
                api = YouTubeTranscriptApi(proxy_config=GenericProxyConfig(https_url=transcript_proxy))
            else:
                api = YouTubeTranscriptApi()

            logger.info(f"Fetching transcript for video ID: {video_id}")
            return " ".join(snippet.text for snippet in api.fetch(video_id))

        transcript_text = await asyncio.to_thread(_fetch)
        await _transcript_cache.set(video_id, transcript_text)
        return transcript_text

    except Exception as e:
        logger.warning(
            f"Failed to fetch transcript for video ID {video_id}: {e}")
        return None
