import re
import logging
import requests
from typing import List, Optional

from src.config import RAPIDAPI_KEY
from src.utils.cache_utils import PersistentCache

logger = logging.getLogger(__name__)

# Bounded persistent cache for Tweets: url -> text or None
_twitter_cache = PersistentCache('twitter_transcripts')


def extract_twitter_urls(text: str) -> List[str]:
    """
    Extract x.com and twitter.com URLs from text.
    """
    if not text:
        return []

    # Regex targeting twitter/x URLs
    pattern = r'https?://(?:www\.)?(?:twitter\.com|x\.com)/[^\s<>"]+'

    matches = re.finditer(pattern, text)
    urls = []

    for match in matches:
        url = match.group(0)
        if url not in urls:
            urls.append(url)

    return urls


def get_tweet_context(tweet_url: str) -> Optional[str]:
    """
    Fetch the text content of a tweet and its top replies using RapidAPI.
    Results are cached to persistent disk.
    """
    if tweet_url in _twitter_cache:
        cached_result = _twitter_cache[tweet_url]
        if cached_result is None:
            logger.debug(f"Cache hit for Twitter fetch failure: {tweet_url}")
            return None
        logger.debug(f"Cache hit for Twitter fetch: {tweet_url}")
        return cached_result

    def cache_failure(u: str):
        _twitter_cache[u] = None

    if not RAPIDAPI_KEY:
        logger.error("RAPIDAPI_KEY is not set. Cannot fetch Twitter content.")
        cache_failure(tweet_url)
        return None

    try:
        # Extract the trailing ID, ignoring any query parameters
        tweet_id = tweet_url.rstrip('/').split('/')[-1].split('?')[0]
        
        headers = {
            "x-rapidapi-host": "x-com2.p.rapidapi.com",
            "x-rapidapi-key": RAPIDAPI_KEY
        }
        
        response = requests.get(
            "https://x-com2.p.rapidapi.com/tweet",
            headers=headers,
            params={"id": tweet_id},
            timeout=15
        )
        response.raise_for_status()
        
        data = response.json()
        entries = data['data']['threaded_conversation_with_injections_v2']['instructions'][1]['entries']
        
        # Helper to get exact main tweet text
        def extract_tweet_text(result):
            note = result.get('note_tweet', {})
            if note:
                return note['note_tweet_results']['result']['text']
            return result['legacy']['full_text']
        
        # Helper to get the author's screen-name / name
        def extract_author(result):
            return result['core']['user_results']['result']['legacy']['name']
        
        # Main tweet
        main_result = entries[0]['content']['itemContent']['tweet_results']['result']
        main_author = extract_author(main_result)
        main_text = extract_tweet_text(main_result)
        
        # Top replies - grab first 5
        replies = []
        for entry in entries[1:6]:  # skip cursor entries at the end
            try:
                # replies are nested under items[]
                items = entry['content'].get('items', [])
                if items:
                    reply_result = items[0]['item']['itemContent']['tweet_results']['result']
                else:
                    reply_result = entry['content']['itemContent']['tweet_results']['result']
                
                reply_author = extract_author(reply_result)
                reply_text = extract_tweet_text(reply_result)
                replies.append(f"  ↳ {reply_author}: {reply_text}")
            except (KeyError, TypeError):
                continue
        
        context = f"Tweet by {main_author}:\n{main_text}"
        if replies:
            context += "\n\nTop replies:\n" + "\n".join(replies)
        
        _twitter_cache[tweet_url] = context
        return context

    except KeyError as ke:
        logger.warning(f"Failed to parse RapidAPI JSON response for {tweet_url}: {ke}")
        cache_failure(tweet_url)
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch Twitter text for {tweet_url}: {e}")
        cache_failure(tweet_url)
        return None
