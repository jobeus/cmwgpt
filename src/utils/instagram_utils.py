import re
import logging
from typing import List, Optional, Tuple

import httpx

from src.config import get_config
from src.utils.cache_utils import PersistentCache, skip_recent_failures
from src.utils.discord_helper import url_to_base64_data_url
from src.utils.media_transcribe import transcribe_video_url

logger = logging.getLogger(__name__)

# Redis cache for Instagram: url -> (context text, image base64 data URL or None)
_instagram_cache = PersistentCache('instagram_transcripts')

RAPIDAPI_HOST = "instagram-downloader-download-instagram-videos-stories1.p.rapidapi.com"


def extract_instagram_urls(text: str) -> List[str]:
    """
    Extract instagram.com and threads.com URLs from text.
    """
    if not text:
        return []

    pattern = r'https?://(?:www\.)?(?:instagram\.com|threads\.net|threads\.com)/[^\s<>"]+'

    urls: List[str] = []
    for match in re.finditer(pattern, text):
        # Drop query params and trailing slashes so the same post dedups.
        normalized = match.group(0).split('?')[0].rstrip('/')
        if normalized not in urls:
            urls.append(normalized)
    return urls


async def _image_to_data_url(image_url: Optional[str]) -> Optional[str]:
    if not image_url:
        return None
    try:
        return await url_to_base64_data_url(image_url)
    except Exception as e:
        logger.warning(f"Failed to fetch Instagram image {image_url}: {e}")
        return None


@skip_recent_failures("instagram")
async def get_instagram_context(url: str, *, cache_only: bool = False) -> Optional[Tuple[str, Optional[str]]]:
    """
    Fetch the content of an Instagram/Threads URL using RapidAPI.

    Returns ``(context, image_data_url)``, cached in Redis.
    """
    cached = await _instagram_cache.get(url)
    if cached is not None:
        context, image_data_url = cached
        return context, image_data_url
    if cache_only:
        return None

    cfg = get_config()
    if not cfg.rapidapi_key:
        logger.error("RAPIDAPI_KEY is not set. Cannot fetch Instagram content.")
        return None

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            response = await client.get(
                f"https://{RAPIDAPI_HOST}/get-info-rapidapi",
                headers={"x-rapidapi-host": RAPIDAPI_HOST, "x-rapidapi-key": cfg.rapidapi_key},
                params={"url": url},
            )
            response.raise_for_status()
            data = response.json()

        if data.get("error"):
            logger.warning(f"Error returned from RapidAPI for Instagram {url}: {data}")
            return None

        media_type = data.get("type")
        caption = data.get("caption", "No caption provided.")
        download_url = data.get("download_url")
        posting_source = data.get("hosting", "Instagram/Threads")

        context = f"Post on {posting_source}:\nCaption: {caption}"
        image_url = download_url if media_type == "image" else None

        if media_type == "video" and download_url:
            try:
                video_transcript = await transcribe_video_url(download_url)
                if video_transcript:
                    context += f"\nVideo Transcript: {video_transcript}"
            except Exception as e:
                logger.warning(f"Failed to process video for Instagram {url}: {e}")

        image_data_url = await _image_to_data_url(image_url)
        await _instagram_cache.set(url, (context, image_data_url))
        return context, image_data_url

    except KeyError as ke:
        logger.warning(f"Failed to parse RapidAPI JSON response for Instagram {url}: {ke}")
        return None
    except httpx.HTTPError as he:
        logger.warning(f"HTTPError fetching Instagram {url}: {he}")
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch Instagram text for {url}: {e}")
        return None
