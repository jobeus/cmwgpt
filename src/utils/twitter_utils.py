import re
import logging
from typing import List, Optional, Tuple

import httpx

from src.config import get_config
from src.utils.cache_utils import PersistentCache, skip_recent_failures
from src.utils.discord_helper import url_to_base64_data_url
from src.utils.media_transcribe import transcribe_video_url

logger = logging.getLogger(__name__)

# Redis cache for Tweets: url -> (context text, photo base64 data URLs)
_twitter_cache = PersistentCache('twitter_transcripts')

RAPIDAPI_HOST = "x-com2.p.rapidapi.com"


def extract_twitter_urls(text: str) -> List[str]:
    """
    Extract x.com and twitter.com and xcancel.com tweet URLs from text.
    Only URLs containing /status/<id> are returned, so profile links and other
    non-tweet pages (e.g. x.com/home) are ignored.
    Normalizes all variants to x.com so the same tweet isn't fetched twice.
    """
    if not text:
        return []

    # Regex targeting twitter/x tweet URLs (must contain /status/<numeric id>)
    pattern = r'https?://(?:www\.)?(?:twitter\.com|x\.com|xcancel\.com)/[^\s<>"]+/status/\d+[^\s<>"]*'

    matches = re.finditer(pattern, text)
    urls = []

    for match in matches:
        url = match.group(0)
        # Normalize to x.com so twitter.com/foo/status/123 and x.com/foo/status/123 dedup
        url = re.sub(r'https?://(?:www\.)?(?:twitter\.com|xcancel\.com)', 'https://x.com', url)
        if url not in urls:
            urls.append(url)

    return urls


# For watching a tweet's video, the best variant up to this bitrate (it's re-encoded anyway).
WATCH_MAX_BITRATE = 2_500_000


def extract_media(data, *, for_watching: bool = False):
    """Photos and the video from a TweetDetail response.

    The video is its smallest mp4 (only the audio is transcribed), or with ``for_watching``
    the best one up to ``WATCH_MAX_BITRATE``.
    """
    media_items = []
    try:
        entries = data['data']['threaded_conversation_with_injections_v2']['instructions'][1]['entries']
        result = entries[0]['content']['itemContent']['tweet_results']['result']
        media = result['legacy'].get('extended_entities', {}).get('media', [])
        
        for m in media:
            if m.get('type') == 'photo' and m.get('media_url_https'):
                media_items.append({'type': 'photo', 'url': m['media_url_https']})
            elif m.get('video_info'):
                variants = m['video_info']['variants']
                mp4s = [v for v in variants if v.get('content_type') == 'video/mp4']
                if mp4s:
                    by_bitrate = sorted(mp4s, key=lambda v: v.get('bitrate', 0))
                    watchable = [v for v in by_bitrate if v.get('bitrate', 0) <= WATCH_MAX_BITRATE] or by_bitrate[:1]
                    video_url = (watchable[-1] if for_watching else by_bitrate[0])['url']
                    media_items.append({'type': 'video', 'url': video_url})
    except (KeyError, TypeError, IndexError):
        pass
    return media_items


async def _photos_to_data_urls(photo_urls: List[str]) -> List[str]:
    data_urls = []
    for photo_url in photo_urls:
        try:
            data_urls.append(await url_to_base64_data_url(photo_url))
        except Exception as e:
            logger.warning(f"Failed to fetch tweet photo {photo_url}: {e}")
    return data_urls


def _extract_tweet_text(result) -> str:
    note = result.get('note_tweet', {})
    if note:
        return note['note_tweet_results']['result']['text']
    return result['legacy']['full_text']


def _extract_author(result) -> str:
    return result['core']['user_results']['result']['legacy']['name']


async def _fetch_tweet_detail(tweet_url: str, rapidapi_key: str) -> Optional[dict]:
    """The RapidAPI TweetDetail response for a tweet URL, or ``None`` if it has no tweet ID."""
    # The numeric ID after /status/, ignoring trailing segments like /photo/1.
    id_match = re.search(r'/status/(\d+)', tweet_url)
    if not id_match:
        logger.warning(f"No /status/<id> tweet ID found in URL, skipping: {tweet_url}")
        return None
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        response = await client.get(
            f"https://{RAPIDAPI_HOST}/v2/TweetDetail/",
            headers={"x-rapidapi-host": RAPIDAPI_HOST, "x-rapidapi-key": rapidapi_key},
            params={"id": id_match.group(1)},
        )
        response.raise_for_status()
        return response.json()


async def find_tweet_video_url(tweet_url: str) -> Optional[str]:
    """A watchable mp4 of the tweet's video, or ``None`` (no video, no RapidAPI key, or a failed lookup)."""
    key = get_config().rapidapi_key
    if not key:
        return None
    try:
        data = await _fetch_tweet_detail(tweet_url, key)
    except Exception as e:
        logger.warning(f"Couldn't look up the video in {tweet_url}: {e}")
        return None
    media = extract_media(data, for_watching=True) if data else []
    return next((m['url'] for m in media if m['type'] == 'video'), None)


@skip_recent_failures("twitter")
async def get_tweet_context(tweet_url: str, *, cache_only: bool = False) -> Optional[Tuple[str, List[str]]]:
    """
    Fetch a tweet's text, top replies, video transcript and photos via RapidAPI.

    Returns ``(context, image_data_urls)``, cached in Redis.
    """
    cached = await _twitter_cache.get(tweet_url)
    if cached is not None:
        context, image_data_urls = cached
        return context, image_data_urls
    if cache_only:
        return None

    cfg = get_config()
    if not cfg.rapidapi_key:
        logger.error("RAPIDAPI_KEY is not set. Cannot fetch Twitter content.")
        return None

    try:
        data = await _fetch_tweet_detail(tweet_url, cfg.rapidapi_key)
        if data is None:
            return None

        entries = data['data']['threaded_conversation_with_injections_v2']['instructions'][1]['entries']
        main_result = entries[0]['content']['itemContent']['tweet_results']['result']
        main_author = _extract_author(main_result)
        main_text = _extract_tweet_text(main_result)

        media_items = extract_media(data)
        video_url = next((m['url'] for m in media_items if m['type'] == 'video'), None)
        photo_urls = [m['url'] for m in media_items if m['type'] == 'photo']

        video_transcript = None
        if video_url:
            try:
                video_transcript = await transcribe_video_url(video_url)
            except Exception as e:
                logger.warning(f"Failed to process video for tweet {tweet_url}: {e}")

        # Top replies: the first 5 entries after the main tweet (cursor entries are skipped).
        replies = []
        for entry in entries[1:6]:
            try:
                items = entry['content'].get('items', [])
                if items:
                    reply_result = items[0]['item']['itemContent']['tweet_results']['result']
                else:
                    reply_result = entry['content']['itemContent']['tweet_results']['result']
                replies.append(f"  ↳ {_extract_author(reply_result)}: {_extract_tweet_text(reply_result)}")
            except (KeyError, TypeError):
                continue

        context = f"Tweet by {main_author}:\n{main_text}"
        if video_transcript:
            context += f"\ntranscript of video: {video_transcript}"
        if replies:
            context += "\n\nTop replies:\n" + "\n".join(replies)

        image_data_urls = await _photos_to_data_urls(photo_urls)
        await _twitter_cache.set(tweet_url, (context, image_data_urls))
        return context, image_data_urls

    except KeyError as ke:
        logger.warning(f"Failed to parse RapidAPI JSON response for {tweet_url}: {ke}")
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch Twitter text for {tweet_url}: {e}")
        return None
