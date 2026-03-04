import re
import logging
import requests
from typing import List, Optional
from urllib.parse import urlparse

import trafilatura
from src.config import TRANSCRIPT_PROXY
from src.utils.cache_utils import PersistentCache

logger = logging.getLogger(__name__)

# Bounded persistent cache for articles: url -> text or None
_article_cache = PersistentCache('articles')

EXCLUDED_DOMAINS = {
    'youtube.com', 'youtu.be',
    'tiktok.com', 'vm.tiktok.com',
    'instagram.com', 'www.instagram.com',
    'facebook.com', 'www.facebook.com', 'fb.watch',
    'x.com', 'www.x.com', 'twitter.com', 'www.twitter.com',
    'xcancel.com', 'www.xcancel.com'
}

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
        try:
            parsed = urlparse(parse_url)
            domain = parsed.netloc.lower()
            
            # Simple check to see if domain ends with any excluded domain
            is_excluded = any(domain == ex or domain.endswith('.' + ex) for ex in EXCLUDED_DOMAINS)
            
            if not is_excluded and url not in urls:
                urls.append(url)
        except Exception:
            pass

    return urls

def inject_article_cache(url: str, text: str) -> None:
    """
    Directly inject text into the article cache for a given URL.
    Useful for immediately caching content we just generated (like from paste services).
    """
    _article_cache[url] = text
    logger.debug(f"Directly injected cache for article: {url}")

def get_article_text(url: str) -> Optional[str]:
    """
    Fetch the text content for a URL, falling back from trafilatura to newspaper3k.
    Results are cached to persistent disk.
    """
    if url in _article_cache:
        cached_result = _article_cache[url]
        if cached_result is None:
            logger.debug(f"Cache hit for article failure: {url}")
            return None
        logger.debug(f"Cache hit for article: {url}")
        return cached_result
        
    def cache_failure(u: str):
        _article_cache[u] = None

    try:
        logger.info(f"Fetching article for URL: {url}")
        
        proxies = None
        if TRANSCRIPT_PROXY:
            proxies = {
                "http": TRANSCRIPT_PROXY,
                "https": TRANSCRIPT_PROXY
            }
            
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, proxies=proxies, timeout=15)
        response.raise_for_status()
        html = response.text
        
        # Try trafilatura first
        text = trafilatura.extract(html)
        
        if not text:
            logger.info(f"Trafilatura failed or returned empty for {url}, falling back to newspaper3k")
            article = Article(url)
            article.set_html(html)
            article.parse()
            text = article.text
            
        if not text:
            logger.warning(f"Both trafilatura and newspaper3k failed to extract text for {url}")
            cache_failure(url)
            return None

        _article_cache[url] = text
        return text

    except Exception as e:
        logger.warning(f"Failed to fetch article text for {url}: {e}")
        cache_failure(url)
        return None
