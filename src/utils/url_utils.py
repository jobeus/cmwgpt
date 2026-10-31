import asyncio
import re
import logging
from typing import List, Optional
from urllib.parse import unquote, urljoin, urlparse, urlunparse

import httpx
import trafilatura

from src.config import get_config
from src.utils.cache_utils import PersistentCache, skip_recent_failures
from src.utils.http_client import fetch_public
from src.utils.reddit_utils import is_reddit_post_url

logger = logging.getLogger(__name__)

# Redis cache for articles: url -> extracted text
_article_cache = PersistentCache('articles')

MAX_HTML_BYTES = 5 * 1024 * 1024

# Punctuation that ends a sentence around a link rather than belonging to it,
# e.g. "(see https://example.org/x)." -> "https://example.org/x".
_TRAILING_PUNCTUATION = ').,;:!?\'"]>'

_BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'Accept-Language': 'en-US,en;q=0.9',
    'Sec-Ch-Ua': '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    'Sec-Ch-Ua-Mobile': '?0',
    'Sec-Ch-Ua-Platform': '"Windows"',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Upgrade-Insecure-Requests': '1',
    'Dnt': '1',
    'Cache-Control': 'max-age=0'
}


EXCLUDED_DOMAINS = {
    'youtube.com', 'youtu.be',
    'tiktok.com', 'vm.tiktok.com',
    'instagram.com', 'www.instagram.com',
    'facebook.com', 'www.facebook.com', 'fb.watch',
    'x.com', 'www.x.com', 'twitter.com', 'www.twitter.com',
    'xcancel.com', 'www.xcancel.com',
    'cdn.discordapp.com', 'media.discordapp.net',
    'images-ext-1.discordapp.net', 'images-ext-2.discordapp.net',
    'threads.com', 'www.threads.com',
    'threads.net', 'www.threads.net',
}

EXCLUDED_MEDIA_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg', '.ico',
    '.mp4', '.webm', '.mov', '.avi', '.mkv',
    '.mp3', '.wav', '.m4a', '.ogg', '.flac', '.aac'
}


def _is_excluded_domain(domain: str) -> bool:
    return any(domain == ex or domain.endswith('.' + ex) for ex in EXCLUDED_DOMAINS)


def _is_excluded_media_url(parsed) -> bool:
    path = parsed.path.lower()
    return any(path.endswith(ext) for ext in EXCLUDED_MEDIA_EXTENSIONS)


def _strip_trailing_punctuation(url: str) -> str:
    while url and url[-1] in _TRAILING_PUNCTUATION:
        # Keep a closing paren that balances one inside the URL (Wikipedia-style links).
        if url[-1] == ')' and url.count('(') >= url.count(')'):
            break
        url = url[:-1]
    return url


def extract_target_urls(text: str) -> List[str]:
    """
    Extract URLs from text, ignoring specific excluded domains.
    """
    if not text:
        return []
    
    # Regex for capturing http/https URLs
    pattern = r'https?://[^\s<>"]+|www\.[^\s<>"]+'
    
    matches = re.finditer(pattern, text)
    urls = []
    
    for match in matches:
        url = _strip_trailing_punctuation(match.group(0))
        # A bare www. link needs a scheme to be fetchable at all.
        if not url.startswith('http'):
            url = 'https://' + url
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        # Exclude domains with dedicated downloader/media handling and obvious binary media URLs
        # Reddit posts go through reddit_utils; other Reddit pages are still fetched here.
        is_excluded = _is_excluded_domain(domain) or _is_excluded_media_url(parsed) or is_reddit_post_url(url)

        if not is_excluded and url not in urls:
            urls.append(url)

    return urls


async def inject_article_cache(url: str, text: str) -> None:
    """
    Directly inject text into the article cache for a given URL.
    Useful for immediately caching content we just generated (like from paste services).
    """
    await _article_cache.set(url, text)
    logger.debug(f"Directly injected cache for article: {url}")


async def _fetch_wikipedia(parsed) -> Optional[str]:
    """Use the official API for Wikipedia (scraping gets 403s from datacenter IPs)."""
    lang = parsed.netloc.split('.')[0]
    params = {
        "action": "query",
        "prop": "extracts",
        "explaintext": "1",
        "titles": unquote(parsed.path[len('/wiki/'):]),
        "format": "json",
    }
    async with httpx.AsyncClient(proxy=get_config().transcript_proxy or None, timeout=10.0) as client:
        response = await client.get(
            f"https://{lang}.wikipedia.org/w/api.php",
            params=params,
            headers={"User-Agent": "CMWGPT/1.0 (https://github.com/jobeus/cmwgpt)"},
        )
        response.raise_for_status()
    for page in response.json().get("query", {}).get("pages", {}).values():
        if page.get("extract"):
            return page["extract"]
    return None


async def _resolve_vreddit(url: str, proxy: Optional[str]) -> str:
    """Follow v.redd.it's redirect ourselves so we never hit www.reddit.com's bot wall."""
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=10.0, follow_redirects=False) as client:
            response = await client.get(url, headers=_BROWSER_HEADERS)
        location = response.headers.get("location")
        if response.is_redirect and location:
            return urljoin(url, location)
    except Exception as e:
        logger.debug(f"Failed to resolve v.redd.it redirect for {url}: {e}")
    return url


def _extract_text(html: str) -> Optional[str]:
    """Main article text, falling back to trafilatura's recall-oriented mode."""
    return trafilatura.extract(html) or trafilatura.extract(html, favor_recall=True)


@skip_recent_failures("article")
async def get_article_text(url: str, *, cache_only: bool = False) -> Optional[str]:
    """
    Fetch the readable text of a web page. Results are cached in Redis.
    Returns ``None`` on failure, or on a cache miss when ``cache_only`` is set.
    """
    cached = await _article_cache.get(url)
    if cached is not None or cache_only:
        return cached

    try:
        logger.info(f"Fetching article for URL: {url}")
        proxy = get_config().transcript_proxy or None
        # Fetch a possibly rewritten URL but always cache under the original,
        # since that is what later lookups use.
        fetch_url = url
        parsed = urlparse(fetch_url)

        if parsed.netloc.endswith('wikipedia.org') and parsed.path.startswith('/wiki/'):
            try:
                text = await _fetch_wikipedia(parsed)
                if text:
                    await _article_cache.set(url, text)
                    return text
            except Exception as e:
                logger.warning(f"Failed to fetch Wikipedia via API for {url}, falling back to scraper: {e}")

        if parsed.netloc.lower() == 'v.redd.it':
            fetch_url = await _resolve_vreddit(fetch_url, proxy)
            parsed = urlparse(fetch_url)

        # old.reddit skips the login wall. Swap only the host, never a path substring.
        if 'reddit.com' in parsed.netloc.lower() and parsed.netloc.lower() != 'old.reddit.com':
            fetch_url = urlunparse(parsed._replace(netloc='old.reddit.com'))

        response = await fetch_public(
            fetch_url, headers=_BROWSER_HEADERS, proxy=proxy, timeout=15.0, max_bytes=MAX_HTML_BYTES)
        response.raise_for_status()

        text = await asyncio.to_thread(_extract_text, response.text)
        if not text:
            logger.warning(f"No article text could be extracted for {url}")
            return None

        await _article_cache.set(url, text)
        return text

    except Exception as e:
        logger.warning(f"Failed to fetch article text for {url}: {e}")
        return None


def wiki_title_candidates(target: str) -> List[str]:
    """Article titles to try for a Wikipedia URL or a typed title, best guess first.

    A URL gives its own title. A typed title gets underscores and a capital first letter
    (Wikipedia is case-sensitive after that), then title case as a fallback.
    """
    if "wikipedia.org/wiki/" in target:
        path_parts = urlparse(target).path.split("/wiki/")
        return [unquote(path_parts[1])] if len(path_parts) > 1 and path_parts[1] else []
    title = target.strip().replace(" ", "_")
    if not title:
        return []
    candidates = [title[0].upper() + title[1:]]
    title_cased = target.strip().title().replace(" ", "_")
    if title_cased not in candidates:
        candidates.append(title_cased)
    return candidates
