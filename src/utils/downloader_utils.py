"""
Downloader Utilities
Unifies URL content fetching from YouTube, TikTok, Twitter, Facebook,
Instagram/Threads and general websites.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from src.utils.facebook_utils import extract_facebook_urls, get_facebook_transcript
from src.utils.instagram_utils import extract_instagram_urls, get_instagram_context
from src.utils.reddit_utils import extract_reddit_urls, get_reddit_thread
from src.utils.tiktok_utils import extract_tiktok_urls, get_tiktok_transcript
from src.utils.twitter_utils import extract_twitter_urls, get_tweet_context
from src.utils.url_utils import extract_target_urls, get_article_text
from src.utils.youtube_utils import extract_video_ids, get_transcript

logger = logging.getLogger(__name__)

# At most this many fetches run at once across all sources of one message.
MAX_CONCURRENT_FETCHES = 4
# Page/transcript lookups that take longer than this are dropped. Sources that
# transcribe video have no limit, so a long video still gets its full transcript.
QUICK_FETCH_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class _Source:
    """One kind of link: how to find it, fetch it, and present the result."""
    name: str
    extract: Callable[[str], List[str]]
    fetch: Callable[..., Awaitable[Any]]
    header: str
    # (key, result) -> (text block, image data URLs)
    render: Callable[[str, Any], Tuple[str, List[str]]]
    timeout: Optional[float] = None


def _text_only(template: str) -> Callable[[str, Any], Tuple[str, List[str]]]:
    return lambda key, result: (template.format(key=key, text=result), [])


def _render_tweet(key: str, result: Any) -> Tuple[str, List[str]]:
    text, images = result
    return f"Target Tweet {key} Context:\n{text}", list(images or [])


def _render_instagram(key: str, result: Any) -> Tuple[str, List[str]]:
    text, image = result
    return f"Instagram/Threads Post {key}:\n{text}", [image] if image else []


# Order here is the order sections appear in the prompt.
SOURCES: List[_Source] = [
    _Source("youtube", extract_video_ids, get_transcript,
            "Included youtube link transcript follows:",
            _text_only("Target Video ID {key} Transcript:\n{text}"), QUICK_FETCH_TIMEOUT_SECONDS),
    _Source("tiktok", extract_tiktok_urls, get_tiktok_transcript,
            "Included TikTok video transcript follows:",
            _text_only("Target TikTok Video {key} Transcript:\n{text}")),
    _Source("twitter", extract_twitter_urls, get_tweet_context,
            "Included Twitter post context follows:", _render_tweet),
    _Source("facebook", extract_facebook_urls, get_facebook_transcript,
            "Included Facebook video transcript follows:",
            _text_only("Target Facebook Video {key} Transcript:\n{text}")),
    _Source("instagram", extract_instagram_urls, get_instagram_context,
            "Included Instagram/Threads posts follow:", _render_instagram),
    # Comments only; the post itself comes through the article source below.
    _Source("reddit", extract_reddit_urls, get_reddit_thread,
            "Included Reddit comment threads follow:",
            _text_only("Reddit thread {key}:\n{text}"), QUICK_FETCH_TIMEOUT_SECONDS),
    _Source("article", extract_target_urls, get_article_text,
            "Included article content follows:",
            _text_only("URL content:\n{text}"), QUICK_FETCH_TIMEOUT_SECONDS),
]


async def fetch_all_url_content(
    message_text: str, *, cache_only: bool = False
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Fetch the content of every supported link in ``message_text``.

    Args:
        message_text: The message text containing potential URLs.
        cache_only: Only use already-cached results; never hit the network.
            Used for older history messages, so a mention never waits on
            fetching links nobody is asking about any more.

    Returns:
        ``(text block to prepend to the message, image_url content parts)``.
        The text is empty if no URLs were found or every fetch failed.
    """
    if not message_text:
        return "", []

    jobs = [(source, key) for source in SOURCES for key in source.extract(message_text)]
    if not jobs:
        return "", []

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)

    async def run(source: _Source, key: str) -> Optional[Tuple[str, List[str]]]:
        try:
            async with semaphore:
                result = await asyncio.wait_for(source.fetch(key, cache_only=cache_only), source.timeout)
            if not result:
                return None
            return source.render(key, result)
        except Exception as e:
            logger.warning(f"Failed to fetch {source.name} content for {key}: {type(e).__name__}: {e}")
            return None

    results = await asyncio.gather(*(run(source, key) for source, key in jobs))

    sections: Dict[str, List[str]] = {}
    images: List[Dict[str, Any]] = []
    for (source, key), rendered in zip(jobs, results):
        if rendered is None:
            continue
        text, image_urls = rendered
        sections.setdefault(source.name, []).append(text)
        images.extend({"type": "image_url", "image_url": {"url": url}} for url in image_urls)
        if not cache_only:
            logger.info(f"Fetched {source.name} content for {key}")

    blocks = [
        f"------\n{source.header}\n\n" + "\n\n".join(sections[source.name])
        for source in SOURCES if source.name in sections
    ]
    if not blocks:
        return "", images
    return "\n\n".join(blocks) + "\n\n", images
