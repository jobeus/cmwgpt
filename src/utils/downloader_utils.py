"""
Downloader Utilities
Unifies URL downloading from YouTube, TikTok, and general websites
"""

import sys
import logging
import asyncio
from typing import Optional

from src.utils.youtube_utils import extract_video_ids, get_transcript
from src.utils.tiktok_utils import extract_tiktok_urls, get_tiktok_transcript
from src.utils.url_utils import extract_target_urls, get_article_text

logger = logging.getLogger(__name__)


async def fetch_all_url_content(message_text: str) -> str:
    """
    Extracts all supported URLs (YouTube, TikTok, General Articles) from the text,
    fetches their content, and returns an aggregated formatted string to prepend to context.
    
    Args:
        message_text: The user's message text containing potential URLs.
        
    Returns:
        A formatted string with the fetched content. Empty string if no URLs were found or all failed.
    """
    if not message_text:
        return ""
        
    aggregated_content = []

    # 1. YouTube Transcripts
    video_ids = extract_video_ids(message_text)
    if video_ids:
        transcripts = []
        for vid_id in video_ids:
            try:
                transcript_text = await asyncio.to_thread(get_transcript, vid_id)
                if transcript_text:
                    if hasattr(sys, 'stdout') and 'pytest' not in sys.modules:
                       logger.info(f"Target Video ID {vid_id} Transcript grabbed successfully.") 
                    transcripts.append(f"Target Video ID {vid_id} Transcript:\n{transcript_text}")
            except Exception as e:
                logger.warning(f"Failed to fetch transcript for {vid_id}: {e}")

        if transcripts:
            aggregated_content.append(
                "------\nIncluded youtube link transcript follows:\n\n" + "\n\n".join(transcripts)
            )

    # 2. TikTok Transcripts
    tiktok_urls = extract_tiktok_urls(message_text)
    if tiktok_urls:
        tiktok_transcripts = []
        for t_url in tiktok_urls:
            try:
                transcript_text = await asyncio.to_thread(get_tiktok_transcript, t_url)
                if transcript_text:
                    if hasattr(sys, 'stdout') and 'pytest' not in sys.modules:
                       logger.info(f"Target TikTok Video {t_url} Transcript grabbed successfully.") 
                    tiktok_transcripts.append(f"Target TikTok Video {t_url} Transcript:\n{transcript_text}")
            except Exception as e:
                logger.warning(f"Failed to fetch TikTok transcript for {t_url}: {e}")

        if tiktok_transcripts:
            aggregated_content.append(
                "------\nIncluded TikTok video transcript follows:\n\n" + "\n\n".join(tiktok_transcripts)
            )

    # 3. General Articles
    target_urls = extract_target_urls(message_text)
    if target_urls:
        articles = []
        for t_url in target_urls:
            try:
                article_text = await asyncio.to_thread(get_article_text, t_url)
                if article_text:
                    if hasattr(sys, 'stdout') and 'pytest' not in sys.modules:
                       logger.info(f"URL content for {t_url} grabbed successfully.") 
                    articles.append(f"URL content:\n{article_text}")
            except Exception as e:
                logger.warning(f"Failed to fetch article for {t_url}: {e}")

        if articles:
            aggregated_content.append(
                "------\nIncluded article content follows:\n\n" + "\n\n".join(articles)
            )
    if aggregated_content:
        return "\n\n".join(aggregated_content) + "\n\n"
    
    return ""
