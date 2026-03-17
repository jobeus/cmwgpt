import re
import logging
import httpx
from typing import List, Optional
from urllib.parse import urlparse
from src.config import MAX_CACHE_SIZE, TRANSCRIPT_PROXY
from src.db.logger import build_artifact, log_pipeline_step
from src.utils.http_client import create_async_client

import trafilatura
from newspaper import Article, Config
from src.utils.cache_utils import PersistentCache

logger = logging.getLogger(__name__)

# Bounded persistent cache for articles: url -> extracted text
_article_cache = PersistentCache('articles')


def _delete_cache_entry(cache, key: str) -> None:
    if hasattr(cache, 'delete'):
        cache.delete(key)
    elif key in cache:
        del cache[key]


EXCLUDED_DOMAINS = {
    'youtube.com', 'youtu.be',
    'tiktok.com', 'vm.tiktok.com',
    'instagram.com', 'www.instagram.com',
    'facebook.com', 'www.facebook.com', 'fb.watch',
    'x.com', 'www.x.com', 'twitter.com', 'www.twitter.com',
    'xcancel.com', 'www.xcancel.com',
    'cdn.discordapp.com', 'media.discordapp.net',
    'images-ext-1.discordapp.net', 'images-ext-2.discordapp.net',
    'instagram.com','www.instagram.com',
    'threads.com','www.threads.com',
    'threads.net','www.threads.net',
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
        url = match.group(0)
        # Add http:// if it starts with www. (needed for urlparse to work well)
        parse_url = url if url.startswith('http') else 'http://' + url
        parsed = urlparse(parse_url)
        domain = parsed.netloc.lower()

        # Exclude domains with dedicated downloader/media handling and obvious binary media URLs
        is_excluded = _is_excluded_domain(domain) or _is_excluded_media_url(parsed)

        if not is_excluded and url not in urls:
            urls.append(url)

    return urls


def inject_article_cache(url: str, text: str) -> None:
    """
    Directly inject text into the article cache for a given URL.
    Useful for immediately caching content we just generated (like from paste services).
    """
    _article_cache[url] = text
    logger.debug(f"Directly injected cache for article: {url}")


async def get_article_text(url: str) -> Optional[str]:
    """
    Fetch the text content for a URL, falling back from trafilatura to newspaper3k.
    Results are cached to persistent disk.
    """
    if url in _article_cache:
        cached_result = _article_cache[url]
        if cached_result is None:
            logger.debug(f"Discarding legacy article failure sentinel for: {url}")
            _delete_cache_entry(_article_cache, url)
        else:
            logger.debug(f"Cache hit for article: {url}")
            return cached_result

    try:
        logger.info(f"Fetching article for URL: {url}")
        
        proxy = TRANSCRIPT_PROXY if TRANSCRIPT_PROXY else None
            
        headers = {
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

        # Handle v.redd.it redirects manually first to avoid hitting Reddit's bot block on www.reddit.com
        parsed = urlparse(url)
        if parsed.netloc.lower() == 'v.redd.it':
            async with httpx.AsyncClient(proxy=proxy, timeout=10.0, follow_redirects=False) as preflight:
                try:
                    pre_resp = await preflight.get(url, headers=headers)
                    if pre_resp.status_code in (301, 302, 303, 307, 308) and "Location" in pre_resp.headers:
                        url = pre_resp.headers["Location"]
                        parsed = urlparse(url)  # update parsed for next step
                except Exception as e:
                    logger.debug(f"Failed to resolve v.redd.it redirect for {url}: {e}")

        # Swap reddit to old.reddit to bypass blocks and login walls
        if 'reddit.com' in parsed.netloc.lower() and parsed.netloc.lower() != 'old.reddit.com':
            url = url.replace(parsed.netloc, 'old.reddit.com')
        
        async with create_async_client(proxy=proxy, timeout=httpx.Timeout(15.0), follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            html = response.text
            actual_request_headers = dict(response.request.headers)
            actual_response_status = response.status_code
            actual_response_headers = dict(response.headers)
        
        await log_pipeline_step(
            service_name="downloader/article/fetch",
            endpoint_url=url,
            title="Article URL → HTML",
            step="article_fetch",
            input_summary="Fetched article HTML from URL",
            input_data={
                "url": url,
                "proxy": proxy,
                "headers": headers,
            },
            output_summary="Received raw article HTML",
            output_data={
                "url": url,
                "html": html,
                "status_code": actual_response_status,
            },
            request_headers=actual_request_headers,
            response_headers=actual_response_headers,
            response_status=actual_response_status,
            response_artifacts=[
                build_artifact(
                    name="article.html",
                    media_type="text/html",
                    text=html,
                    extra={"url": url},
                )
            ],
        )

        # Try trafilatura first
        extractor_used = "trafilatura"
        text = trafilatura.extract(html)
        
        if not text:
            logger.info(f"Trafilatura failed or returned empty for {url}, falling back to newspaper3k")
            extractor_used = "newspaper3k"
            config = Config()
            config.browser_user_agent = headers['User-Agent']
            config.headers = headers
            config.request_timeout = 15
            if proxy:
                config.proxies = {'http': proxy, 'https': proxy}
            article = Article(url, config=config)
            article.set_html(html)
            article.parse()
            text = article.text
            
        if not text:
            logger.warning(f"Both trafilatura and newspaper3k failed to extract text for {url}")
            return None

        _article_cache[url] = text

        await log_pipeline_step(
            service_name="downloader/article/extract",
            endpoint_url=url,
            title="Article HTML → extracted text",
            step="article_extract",
            input_summary="Ran article extraction on fetched HTML",
            input_data={
                "url": url,
                "html": html,
                "extractor_attempt_order": ["trafilatura", "newspaper3k"],
            },
            output_summary=f"Extracted article text using {extractor_used}",
            output_data={
                "url": url,
                "extractor_used": extractor_used,
                "text": text,
            },
            response_status=200,
            request_artifacts=[
                build_artifact(
                    name="article.html",
                    media_type="text/html",
                    text=html,
                    extra={"url": url},
                )
            ],
            response_artifacts=[
                build_artifact(
                    name="article.txt",
                    media_type="text/plain",
                    text=text,
                    extra={"url": url, "extractor_used": extractor_used},
                )
            ],
        )
        
        return text

    except Exception as e:
        logger.warning(f"Failed to fetch article text for {url}: {e}")
        return None
