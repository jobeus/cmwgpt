import re
import logging
import asyncio
from typing import List, Optional
from youtube_transcript_api import YouTubeTranscriptApi
from src.config import TRANSCRIPT_PROXY
from src.utils.cache_utils import PersistentCache
from src.db.logger import log_api_request

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
    pattern = r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:shorts\/|[^\/\n\s]+\/\S+\/|(?:v|e(?:mbed)?)\/|\S*?[?\&]v=)|youtu\.be\/)([a-zA-Z0-9_-]{11})'

    matches = re.finditer(pattern, text)
    video_ids = []

    for match in matches:
        vid_id = match.group(1)
        if vid_id not in video_ids:
            video_ids.append(vid_id)

    return video_ids


async def get_transcript(video_id: str) -> Optional[str]:
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


    try:
        def _fetch():
            if TRANSCRIPT_PROXY:
                from youtube_transcript_api.proxies import GenericProxyConfig
                proxy_config = GenericProxyConfig(https_url=TRANSCRIPT_PROXY)
                api = YouTubeTranscriptApi(proxy_config=proxy_config)
            else:
                api = YouTubeTranscriptApi()

            logger.info(f"Fetching transcript for video ID: {video_id}")
            snippets = api.fetch(video_id)
            return " ".join([snippet.text for snippet in snippets])

        transcript_text = await asyncio.to_thread(_fetch)
        
        # Build an executable python script snippet representing exactly how this transcript is fetched
        python_snippet = f'''from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter

video_id = "{video_id}"
proxy = "{TRANSCRIPT_PROXY}" if "{TRANSCRIPT_PROXY}" else None

if proxy:
    from youtube_transcript_api.proxies import GenericProxyConfig
    api = YouTubeTranscriptApi(proxy_config=GenericProxyConfig(https_url=proxy))
else:
    api = YouTubeTranscriptApi()

try:
    transcript_list = api.list_transcripts(video_id)
    transcript = transcript_list.find_transcript(['en'])
except Exception:
    transcripts = list(transcript_list._manually_created_transcripts.values()) or \\
                  list(transcript_list._generated_transcripts.values())
    transcript = transcripts[0].translate('en')

print(TextFormatter().format_transcript(transcript.fetch()))
'''

        await log_api_request(
            service_name="youtube/transcript",
            method="PYTHON",
            endpoint_url=f"https://www.youtube.com/watch?v={video_id}",
            request_headers={},
            request_body=python_snippet,
            response_status=200,
            response_headers={},
            response_body=transcript_text,
            cost=0.0
        )

        _transcript_cache[video_id] = transcript_text
        return transcript_text

    except Exception as e:
        logger.warning(
            f"Failed to fetch transcript for video ID {video_id}: {e}")
        return None
