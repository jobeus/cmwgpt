import html
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

# Threads shows a logged-out browser nothing, but serves link crawlers a rendered card of the post
# (author, text cut short, attached image, counts) as og:image: the same card Discord embeds.
CRAWLER_USER_AGENT = "facebookexternalhit/1.1"
_THREADS_HOST = re.compile(r'^https?://(?:www\.)?threads\.(?:net|com)/', re.IGNORECASE)
_META_TAG = re.compile(r'<meta\s+[^>]*>', re.IGNORECASE)
_META_ATTR = re.compile(r'(property|name|content)\s*=\s*"([^"]*)"', re.IGNORECASE)


def extract_instagram_urls(text: str) -> List[str]:
    """
    Extract instagram.com, threads.com and threads.net URLs from text.
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
        logger.warning("Failed to fetch Instagram image %s: %s", image_url, e)
        return None


async def _fetch_instagram_info(url: str, rapidapi_key: str) -> dict:
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        response = await client.get(
            f"https://{RAPIDAPI_HOST}/get-info-rapidapi",
            headers={"x-rapidapi-host": RAPIDAPI_HOST, "x-rapidapi-key": rapidapi_key},
            params={"url": url},
        )
        response.raise_for_status()
        return response.json()


async def find_instagram_video_url(url: str) -> Optional[str]:
    """The download URL of an Instagram/Threads video post, or ``None`` (not a video, or the lookup failed)."""
    key = get_config().rapidapi_key
    if not key:
        return None
    try:
        data = await _fetch_instagram_info(url, key)
    except Exception as e:
        logger.warning("Couldn't look up the video in %s: %s", url, e)
        return None
    if data.get("error") or data.get("type") != "video":
        return None
    return data.get("download_url") or None


def is_threads_url(url: str) -> bool:
    return bool(_THREADS_HOST.match(url))


def parse_og_tags(page: str) -> dict:
    """The page's ``og:*`` meta tags, first one of each name wins, HTML entities decoded."""
    tags: dict = {}
    for tag in _META_TAG.findall(page):
        attrs = {k.lower(): v for k, v in _META_ATTR.findall(tag)}
        name = attrs.get("property") or attrs.get("name") or ""
        if name.startswith("og:") and "content" in attrs:
            tags.setdefault(name, html.unescape(attrs["content"]))
    return tags


async def _threads_card_context(url: str) -> Optional[Tuple[str, Optional[str]]]:
    """A Threads post as the card image Threads gives link crawlers, or ``None``."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0), follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": CRAWLER_USER_AGENT})
            response.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("Couldn't fetch the Threads card for %s: %s", url, e)
        return None
    tags = parse_og_tags(response.text)
    image_data_url = await _image_to_data_url(tags.get("og:image"))
    if not image_data_url:
        logger.warning("No Threads card image for %s", url)
        return None
    context = (
        f"Post on Threads: {tags.get('og:title', 'Threads post')}\n"
        f"Link: {tags.get('og:url', url)}\n"
        "The post is the attached image: Threads' preview card, with the author, the post's text "
        "(cut off where it ends in '...'), any attached image or video thumbnail, and like/reply/repost/share counts."
    )
    return context, image_data_url


async def _rapidapi_context(url: str, rapidapi_key: str) -> Optional[Tuple[str, Optional[str]]]:
    try:
        data = await _fetch_instagram_info(url, rapidapi_key)

        if data.get("error"):
            logger.warning("Error returned from RapidAPI for Instagram %s: %s", url, data)
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
                logger.warning("Failed to process video for Instagram %s: %s", url, e)

        return context, await _image_to_data_url(image_url)

    except KeyError as ke:
        logger.warning("Failed to parse RapidAPI JSON response for Instagram %s: %s", url, ke)
    except httpx.HTTPError as he:
        logger.warning("HTTPError fetching Instagram %s: %s", url, he)
    except Exception as e:
        logger.warning("Failed to fetch Instagram text for %s: %s", url, e)
    return None


@skip_recent_failures("instagram")
async def get_instagram_context(url: str, *, cache_only: bool = False) -> Optional[Tuple[str, Optional[str]]]:
    """
    Fetch the content of an Instagram/Threads URL using RapidAPI. A Threads post RapidAPI
    can't find falls back to the preview card Threads serves link crawlers.

    Returns ``(context, image_data_url)``, cached in Redis.
    """
    cached = await _instagram_cache.get(url)
    if cached is not None:
        context, image_data_url = cached
        return context, image_data_url
    if cache_only:
        return None

    cfg = get_config()
    result = None
    if cfg.rapidapi_key:
        result = await _rapidapi_context(url, cfg.rapidapi_key)
    elif not is_threads_url(url):
        logger.error("RAPIDAPI_KEY is not set. Cannot fetch Instagram content.")
        return None
    if result is None and is_threads_url(url):
        result = await _threads_card_context(url)
    if result is not None:
        await _instagram_cache.set(url, result)
    return result
