"""
Reddit Utils - A Reddit post's details (title, text, link, images), through RapidAPI.
"""

import html
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from src.config import get_config
from src.utils.cache_utils import PersistentCache, skip_recent_failures
from src.utils.discord_helper import url_to_base64_data_url

logger = logging.getLogger(__name__)

# Redis cache: normalized post URL -> (post text, image base64 data URLs)
_reddit_cache = PersistentCache('reddit_posts')

RAPIDAPI_HOST = "reddit34.p.rapidapi.com"
# Longest post text kept, and most images (a gallery's first ones) returned.
MAX_SELFTEXT_CHARS = 15000
MAX_POST_IMAGES = 4

# A post link: reddit.com/r/<sub>/comments/<id>/..., on any reddit host, or redd.it/<id>.
_POST_RE = re.compile(
    r'https?://(?:[\w-]+\.)?reddit\.com/(?:r/\w+/)?comments/(?P<id>[a-z0-9]+)(?P<rest>/[^\s<>"?#]*)?'
    r'|https?://redd\.it/(?P<short>[a-z0-9]+)',
    re.IGNORECASE,
)
# Punctuation that ends a sentence around a link rather than belonging to it.
_TRAILING_PUNCTUATION = ').,;:!?\'"]>'


def is_reddit_post_url(url: str) -> bool:
    return bool(_POST_RE.match(url))


def extract_reddit_urls(text: str) -> List[str]:
    """Reddit post URLs in ``text``, normalized to www.reddit.com so the same post dedups."""
    if not text:
        return []
    urls: List[str] = []
    for match in _POST_RE.finditer(text):
        if match.group("short"):
            url = f"https://www.reddit.com/comments/{match.group('short').lower()}/"
        else:
            sub = re.search(r'/r/(\w+)/comments/', match.group(0))
            prefix = f"/r/{sub.group(1)}" if sub else ""
            rest = (match.group("rest") or "/").rstrip(_TRAILING_PUNCTUATION)
            url = f"https://www.reddit.com{prefix}/comments/{match.group('id').lower()}{rest}"
            url = url if url.endswith("/") else url + "/"
        if url not in urls:
            urls.append(url)
    return urls


def _image_urls(post: Dict[str, Any]) -> List[str]:
    """The post's images: a gallery's in order, else its preview image."""
    urls: List[str] = []
    metadata = post.get("media_metadata") or {}
    for item in (post.get("gallery_data") or {}).get("items") or []:
        source = (metadata.get(item.get("media_id")) or {}).get("s") or {}
        if source.get("u"):
            urls.append(source["u"])
    if not urls:
        for image in (post.get("preview") or {}).get("images") or []:
            if (image.get("source") or {}).get("url"):
                urls.append(image["source"]["url"])
    return [html.unescape(u) for u in urls[:MAX_POST_IMAGES]]


def render_post(post: Dict[str, Any]) -> str:
    """The post as text: title, where and by whom, flair, score, its text, and what it links to."""
    created = post.get("created_utc")
    when = datetime.fromtimestamp(created, timezone.utc).strftime("%Y-%m-%d") if created else "?"
    lines = [f"Title: {html.unescape(str(post.get('title') or ''))}"]
    lines.append(
        f"Posted in {post.get('subreddit_name_prefixed') or '?'} by u/{post.get('author') or '?'} on {when}"
    )
    flags = [f"flair: {post['link_flair_text']}"] if post.get("link_flair_text") else []
    flags += [name for name in ("over_18", "spoiler", "locked") if post.get(name)]
    if flags:
        lines.append("(" + ", ".join(f.replace("over_18", "NSFW") for f in flags) + ")")
    ratio = post.get("upvote_ratio")
    lines.append(
        f"Score {post.get('score', 0)}" + (f" ({round(ratio * 100)}% upvoted)" if ratio is not None else "")
        + f", {post.get('num_comments', 0)} comments"
    )
    selftext = html.unescape(str(post.get("selftext") or "")).strip()
    if selftext:
        if len(selftext) > MAX_SELFTEXT_CHARS:
            selftext = selftext[:MAX_SELFTEXT_CHARS].rstrip() + "…"
        lines.append(f"Text:\n{selftext}")
    link = post.get("url_overridden_by_dest") or ("" if post.get("is_self") else post.get("url"))
    if post.get("is_video"):
        lines.append("It's a video post.")
    elif link and not post.get("gallery_data"):
        lines.append(f"Links to: {html.unescape(str(link))}")
    return "\n".join(lines)


@skip_recent_failures("reddit")
async def get_reddit_post(url: str, *, cache_only: bool = False) -> Optional[Tuple[str, List[str]]]:
    """The Reddit post at ``url``: ``(text, image data URLs)``, cached in Redis."""
    cached = await _reddit_cache.get(url)
    if cached is not None:
        cached_text, cached_images = cached
        return cached_text, list(cached_images or [])
    if cache_only:
        return None

    cfg = get_config()
    if not cfg.rapidapi_key:
        logger.error("RAPIDAPI_KEY is not set. Cannot fetch Reddit posts.")
        return None

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
            response = await client.get(
                f"https://{RAPIDAPI_HOST}/getPostDetails",
                headers={"x-rapidapi-host": RAPIDAPI_HOST, "x-rapidapi-key": cfg.rapidapi_key},
                params={"post_url": url},
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"Failed to fetch Reddit post {url}: {e}")
        return None

    post = data.get("data") if data.get("success") else None
    if not isinstance(post, dict) or not post.get("title"):
        logger.warning(f"RapidAPI returned no Reddit post for {url}: {str(data)[:300]}")
        return None

    images: List[str] = []
    for image_url in _image_urls(post):
        try:
            images.append(await url_to_base64_data_url(image_url))
        except Exception as e:
            logger.warning(f"Failed to fetch Reddit image {image_url}: {e}")
    text = render_post(post)
    await _reddit_cache.set(url, (text, images))
    return text, images
