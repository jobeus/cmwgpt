import html
import re
import logging
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

import httpx

from src.config import get_config
from src.utils.cache_utils import PersistentCache, skip_recent_failures
from src.utils.discord_helper import url_to_base64_data_url
from src.utils.media_transcribe import transcribe_video_url

logger = logging.getLogger(__name__)

# Redis cache for Instagram/Threads: url -> (context text, image base64 data URLs).
# Entries from before Threads posts could have several images hold one data URL or None instead.
_instagram_cache = PersistentCache('instagram_transcripts')

RAPIDAPI_HOST = "instagram-downloader-download-instagram-videos-stories1.p.rapidapi.com"

# Threads shows a logged-out browser nothing, but serves link crawlers a rendered card of the post
# (author, text cut short, attached image, counts) as og:image: the same card Discord embeds.
CRAWLER_USER_AGENT = "facebookexternalhit/1.1"
_THREADS_HOST = re.compile(r'^https?://(?:www\.)?threads\.(?:net|com)/', re.IGNORECASE)

# Threads posts come from the Threads API on RapidAPI first, which has the whole post.
THREADS_RAPIDAPI_HOST = "threads-api4.p.rapidapi.com"
_THREADS_POST_CODE = re.compile(r'/post/([A-Za-z0-9_-]+)')
_SHORTCODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
MAX_THREADS_IMAGES = 4
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
    if is_threads_url(url):
        post = await _fetch_threads_post(url, key)
        return render_threads_post(post)[2] if post else None
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


def threads_post_id(url: str) -> Optional[str]:
    """The numeric post ID in a Threads ``/post/<code>`` URL (the code is the ID in base 64)."""
    match = _THREADS_POST_CODE.search(url)
    if not match:
        return None
    number = 0
    for char in match.group(1):
        number = number * 64 + _SHORTCODE_ALPHABET.index(char)
    return str(number)


async def _resolve_threads_url(url: str) -> str:
    """A ``/share/`` link's post URL (it redirects there); any other URL as it is."""
    if threads_post_id(url):
        return url
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0), follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": CRAWLER_USER_AGENT})
    if threads_post_id(str(response.url)):
        return str(response.url)
    return parse_og_tags(response.text).get("og:url", url)


async def _fetch_threads_post(url: str, rapidapi_key: str) -> Optional[dict]:
    """The Threads API's ``post`` object for a Threads URL, or ``None``."""
    try:
        post_id = threads_post_id(await _resolve_threads_url(url))
        if not post_id:
            logger.warning("No Threads post ID in %s", url)
            return None
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            response = await client.get(
                f"https://{THREADS_RAPIDAPI_HOST}/api/post/detail",
                headers={"x-rapidapi-host": THREADS_RAPIDAPI_HOST, "x-rapidapi-key": rapidapi_key},
                params={"post_id": post_id},
            )
            response.raise_for_status()
            data = response.json()
        return data["data"]["data"]["edges"][0]["node"]["thread_items"][0]["post"]
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as e:
        logger.warning("Couldn't get Threads post %s from RapidAPI: %s", url, e)
        return None


def _best_image(media: dict) -> Optional[str]:
    candidates = (media.get("image_versions2") or {}).get("candidates") or []
    return candidates[0].get("url") if candidates else None


def render_threads_post(post: dict) -> Tuple[str, List[str], Optional[str]]:
    """``(text, image URLs, video URL)`` for a Threads API post object."""
    info: dict = post.get("text_post_app_info") or {}
    user = (post.get("user") or {}).get("username", "unknown")
    lines = [f"Post on Threads by @{user}"]
    if post.get("taken_at"):
        lines[0] += " on " + datetime.fromtimestamp(post["taken_at"], timezone.utc).strftime("%Y-%m-%d")
    counts = [
        (post.get("like_count"), "likes"), (info.get("direct_reply_count"), "replies"),
        (info.get("repost_count"), "reposts"), (info.get("quote_count"), "quotes"),
        (info.get("reshare_count"), "shares"),
    ]
    lines.append(", ".join(f"{n} {label}" for n, label in counts if n is not None))
    text = (post.get("caption") or {}).get("text")
    if not text:
        fragments = (info.get("text_fragments") or {}).get("fragments") or []
        text = "".join(f.get("plaintext") or "" for f in fragments)
    lines.append(f"Text: {text or '(no text)'}")
    link: Any = info.get("link_preview_attachment") or {}
    if link.get("url"):
        lines.append(f"Links to: {link.get('title') or ''} {link['url']}".replace("  ", " "))

    media = post.get("carousel_media") or [post]
    images = [url for url in (_best_image(m) for m in media) if url][:MAX_THREADS_IMAGES]
    video = next((m["video_versions"][0].get("url") for m in media if m.get("video_versions")), None)
    if len(media) > 1:
        lines.append(f"Carousel of {len(media)} items, the first {len(images)} images attached")
    return "\n".join(lines), images, video


async def _threads_api_context(url: str, rapidapi_key: str) -> Optional[Tuple[str, List[str]]]:
    post = await _fetch_threads_post(url, rapidapi_key)
    if post is None:
        return None
    text, image_urls, video_url = render_threads_post(post)
    if video_url:
        try:
            transcript = await transcribe_video_url(video_url)
            if transcript:
                text += f"\nVideo Transcript: {transcript}"
        except Exception as e:
            logger.warning("Failed to process video for Threads %s: %s", url, e)
    images = [i for i in [await _image_to_data_url(u) for u in image_urls] if i]
    return text, images


async def _threads_card_context(url: str) -> Optional[Tuple[str, List[str]]]:
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
    return context, [image_data_url]


async def _rapidapi_context(url: str, rapidapi_key: str) -> Optional[Tuple[str, List[str]]]:
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

        image_data_url = await _image_to_data_url(image_url)
        return context, [image_data_url] if image_data_url else []

    except KeyError as ke:
        logger.warning("Failed to parse RapidAPI JSON response for Instagram %s: %s", url, ke)
    except httpx.HTTPError as he:
        logger.warning("HTTPError fetching Instagram %s: %s", url, he)
    except Exception as e:
        logger.warning("Failed to fetch Instagram text for %s: %s", url, e)
    return None


@skip_recent_failures("instagram")
async def get_instagram_context(url: str, *, cache_only: bool = False) -> Optional[Tuple[str, List[str]]]:
    """
    Fetch the content of an Instagram/Threads URL. Instagram comes from an Instagram
    downloader on RapidAPI, Threads from the Threads API on RapidAPI, falling back to
    the preview card Threads serves link crawlers.

    Returns ``(context, image data URLs)``, cached in Redis.
    """
    cached = await _instagram_cache.get(url)
    if cached is not None:
        context, images = cached
        if not isinstance(images, list):
            images = [images] if images else []
        return context, images
    if cache_only:
        return None

    cfg = get_config()
    result = None
    if cfg.rapidapi_key:
        if is_threads_url(url):
            result = await _threads_api_context(url, cfg.rapidapi_key)
        else:
            result = await _rapidapi_context(url, cfg.rapidapi_key)
    elif not is_threads_url(url):
        logger.error("RAPIDAPI_KEY is not set. Cannot fetch Instagram content.")
        return None
    if result is None and is_threads_url(url):
        result = await _threads_card_context(url)
    if result is not None:
        await _instagram_cache.set(url, result)
    return result
