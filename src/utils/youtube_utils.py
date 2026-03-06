import re
import logging
from typing import List, Optional
from youtube_transcript_api import YouTubeTranscriptApi
from src.config import TRANSCRIPT_PROXY
from src.utils.cache_utils import PersistentCache

logger = logging.getLogger(__name__)

# Bounded persistent cache for transcripts: video_id -> transcript text or None
_transcript_cache = PersistentCache('youtube_transcripts')


def extract_video_ids(text: str) -> List[str]:
    """
    Extract YouTube video IDs from a block of text.
    Handles standard youtube.com links and youtu.be short links.
    """
    if not text:
        return []

    # Regex to match youtube video IDs from various URL formats
    pattern = r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:[^\/\n\s]+\/\S+\/|(?:v|e(?:mbed)?)\/|\S*?[?&]v=)|youtu\.be\/)([a-zA-Z0-9_-]{11})'

    matches = re.finditer(pattern, text)
    video_ids = []

    for match in matches:
        vid_id = match.group(1)
        if vid_id not in video_ids:
            video_ids.append(vid_id)

    return video_ids


def get_transcript(video_id: str) -> Optional[str]:
    """
    Fetch the transcript for a YouTube video.
    Results are cached to persistent disk to avoid redundant API calls.
    Returns the transcript as a single string, or None if it fails.
    """
    if video_id in _transcript_cache:
        cached_result = _transcript_cache[video_id]
        if cached_result is None:
            logger.debug(f"Cache hit for transcript failure: {video_id}")
            return None
        logger.debug(f"Cache hit for transcript: {video_id}")
        return cached_result

    def cache_failure(vid_id: str):
        _transcript_cache[vid_id] = None

    try:
        if TRANSCRIPT_PROXY:
            from youtube_transcript_api.proxies import GenericProxyConfig
            proxy_config = GenericProxyConfig(https_url=TRANSCRIPT_PROXY)
            api = YouTubeTranscriptApi(proxy_config=proxy_config)
        else:
            api = YouTubeTranscriptApi()

        logger.info(f"Fetching transcript for video ID: {video_id}")

        # In youtube-transcript-api 1.2.4 fetch() returns an iterable of
        # FetchedTranscriptSnippet
        snippets = api.fetch(video_id)
        transcript_text = " ".join([snippet.text for snippet in snippets])

        _transcript_cache[video_id] = transcript_text
        return transcript_text

    except Exception as e:
        logger.warning(
            f"Failed to fetch transcript for video ID {video_id}: {e}")
        cache_failure(video_id)
        return None
