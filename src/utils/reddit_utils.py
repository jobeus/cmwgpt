"""
Reddit Utils - A Reddit post's comment thread, through RapidAPI.

The post itself still goes through the article fetcher (``url_utils``, via old.reddit),
so a Reddit link gets both the post text and its comments.
"""

import html
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from src.config import get_config
from src.utils.cache_utils import PersistentCache, skip_recent_failures

logger = logging.getLogger(__name__)

# Redis cache: normalized post URL -> rendered comment thread
_reddit_cache = PersistentCache('reddit_threads')

RAPIDAPI_HOST = "reddit34.p.rapidapi.com"
COMMENTS_SORT = "top"
# Longest comment body kept, and the most text one thread renders to.
MAX_COMMENT_CHARS = 1000
MAX_THREAD_CHARS = 20000

# A post link: reddit.com/r/<sub>/comments/<id>/..., on any reddit host, or redd.it/<id>.
_POST_RE = re.compile(
    r'https?://(?:[\w-]+\.)?reddit\.com/(?:r/\w+/)?comments/(?P<id>[a-z0-9]+)(?P<rest>/[^\s<>"?#]*)?'
    r'|https?://redd\.it/(?P<short>[a-z0-9]+)',
    re.IGNORECASE,
)
_HIDDEN_BODIES = {"[deleted]", "[removed]", ""}
# Punctuation that ends a sentence around a link rather than belonging to it.
_TRAILING_PUNCTUATION = ').,;:!?\'"]>'


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


def _render_comment(comment: Dict[str, Any]) -> Optional[str]:
    body = html.unescape(str(comment.get("body") or "")).strip()
    if body in _HIDDEN_BODIES:
        return None
    if len(body) > MAX_COMMENT_CHARS:
        body = body[:MAX_COMMENT_CHARS].rstrip() + "…"
    depth = int(comment.get("depth") or 0)
    created = comment.get("created_utc")
    when = datetime.fromtimestamp(created, timezone.utc).strftime("%Y-%m-%d") if created else "?"
    indent = "  " * depth
    body = body.replace("\n", "\n" + indent + "  ")
    return f"{indent}- u/{comment.get('author') or '?'} ({comment.get('score', 0)} points, {when}): {body}"


def render_thread(comments: List[Dict[str, Any]]) -> str:
    """The comments, in the API's order, indented by reply depth and capped at MAX_THREAD_CHARS."""
    lines: List[str] = []
    total = 0
    for comment in comments:
        line = _render_comment(comment)
        if line is None:
            continue
        if total + len(line) > MAX_THREAD_CHARS:
            lines.append("(more comments not shown)")
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


@skip_recent_failures("reddit")
async def get_reddit_thread(url: str, *, cache_only: bool = False) -> Optional[str]:
    """The top comments of the Reddit post at ``url``, rendered as text and cached in Redis."""
    cached = _reddit_cache.get(url)
    if cached is not None:
        return cached
    if cache_only:
        return None

    cfg = get_config()
    if not cfg.rapidapi_key:
        logger.error("RAPIDAPI_KEY is not set. Cannot fetch Reddit comments.")
        return None

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
            response = await client.get(
                f"https://{RAPIDAPI_HOST}/getPostCommentsWithSortV2",
                headers={"x-rapidapi-host": RAPIDAPI_HOST, "x-rapidapi-key": cfg.rapidapi_key},
                params={"post_url": url, "sort": COMMENTS_SORT},
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"Failed to fetch Reddit comments for {url}: {e}")
        return None

    if not data.get("success"):
        logger.warning(f"RapidAPI returned no Reddit comments for {url}: {str(data)[:300]}")
        return None
    comments = (data.get("data") or {}).get("comments") or []
    subreddit = next((c.get("subreddit_name_prefixed") for c in comments if c.get("subreddit_name_prefixed")), "")
    thread = render_thread(comments)
    text = f"{subreddit} comments, {COMMENTS_SORT} first:\n{thread}" if thread else "No comments."
    _reddit_cache[url] = text
    return text
