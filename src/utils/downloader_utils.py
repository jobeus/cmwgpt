"""
Downloader Utilities
Unifies URL downloading from YouTube, TikTok, and general websites
"""

import sys
import logging
import asyncio

from src.utils.youtube_utils import extract_video_ids, get_transcript
from src.utils.tiktok_utils import extract_tiktok_urls, get_tiktok_transcript
from src.utils.twitter_utils import extract_twitter_urls, get_tweet_context
from src.utils.facebook_utils import extract_facebook_urls, get_facebook_transcript
from src.utils.url_utils import extract_target_urls, get_article_text
from src.db.logger import build_artifact, log_api_request, log_pipeline_step

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
    video_ids = extract_video_ids(message_text)
    tiktok_urls = extract_tiktok_urls(message_text)
    twitter_urls = extract_twitter_urls(message_text)
    facebook_urls = extract_facebook_urls(message_text)
    target_urls = extract_target_urls(message_text)

    discovered_sources = {
        "youtube_video_ids": video_ids,
        "tiktok_urls": tiktok_urls,
        "twitter_urls": twitter_urls,
        "facebook_urls": facebook_urls,
        "article_urls": target_urls,
    }

    if any(discovered_sources.values()):
        await log_pipeline_step(
            service_name="downloader/url_discovery",
            endpoint_url="message://url-discovery",
            title="Message text → discovered downloader targets",
            step="url_discovery",
            input_summary="Scanned the incoming message for supported URLs and IDs",
            input_data={"message_text": message_text},
            output_summary="Detected downloader targets grouped by source type",
            output_data=discovered_sources,
        )

    # 1. YouTube Transcripts
    if video_ids:
        transcripts = []
        for vid_id in video_ids:
            try:
                transcript_text = await get_transcript(vid_id)
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
    if tiktok_urls:
        tiktok_transcripts = []
        for t_url in tiktok_urls:
            try:
                result = await asyncio.to_thread(get_tiktok_transcript, t_url)
                if result:
                    transcript_text = result["transcript_text"]
                    groq_resp = result.get("groq_response")
                    from_cache = result.get("from_cache", False)
                    if hasattr(sys, 'stdout') and 'pytest' not in sys.modules:
                        logger.info(f"Target TikTok Video {t_url} Transcript grabbed successfully.")
                    tiktok_transcripts.append(f"Target TikTok Video {t_url} Transcript:\n{transcript_text}")

                    if result.get("audio_artifact"):
                        await log_pipeline_step(
                            service_name="downloader/tiktok/audio",
                            endpoint_url=t_url,
                            title="TikTok URL → audio artifact",
                            step="tiktok_audio",
                            input_summary="Downloaded TikTok media and extracted audio",
                            input_data={
                                "source_url": t_url,
                                "download_strategy": result.get("download_strategy"),
                                "source_metadata": result.get("source_metadata"),
                            },
                            output_summary="Produced an audio artifact for transcription",
                            output_data={
                                "source_url": t_url,
                                "download_strategy": result.get("download_strategy"),
                            },
                            response_artifacts=[result["audio_artifact"]],
                        )

                    if not from_cache:
                        await log_pipeline_step(
                            service_name="downloader/tiktok/transcript",
                            endpoint_url=t_url,
                            title="TikTok audio → transcript",
                            step="tiktok_transcript",
                            input_summary="Prepared TikTok audio for transcription",
                            input_data={
                                "source_url": t_url,
                                "from_cache": False,
                                "download_strategy": result.get("download_strategy"),
                            },
                            output_summary="Produced transcript text for TikTok content",
                            output_data={
                                "source_url": t_url,
                                "transcript_text": transcript_text,
                            },
                            request_artifacts=[result["audio_artifact"]] if result.get("audio_artifact") else None,
                            response_artifacts=[
                                build_artifact(
                                    name="tiktok_transcript.txt",
                                    media_type="text/plain",
                                    text=transcript_text,
                                    extra={"source_url": t_url},
                                )
                            ],
                        )
                    
                    if groq_resp:
                        # Log exact HTTP multipart request as it went over the wire
                        actual_req = groq_resp.request
                        actual_req.read()
                        req_body_bytes = actual_req.content if actual_req.content else b""
                        
                        await log_api_request(
                            service_name="groq/whisper-large-v3-turbo",
                            method=actual_req.method,
                            endpoint_url=str(actual_req.url),
                            request_headers=dict(actual_req.headers),
                            request_body=req_body_bytes.hex() if req_body_bytes else "",
                            response_status=groq_resp.status_code,
                            response_headers=dict(groq_resp.headers),
                            response_body=transcript_text,
                            cost=0.0
                        )
            except Exception as e:
                logger.warning(f"Failed to fetch TikTok transcript for {t_url}: {e}")

        if tiktok_transcripts:
            aggregated_content.append(
                "------\nIncluded TikTok video transcript follows:\n\n" + "\n\n".join(tiktok_transcripts)
            )

    # 3. Twitter / X Context
    if twitter_urls:
        twitter_contexts = []
        for t_url in twitter_urls:
            try:
                tweet_text = await get_tweet_context(t_url)
                if tweet_text:
                    if hasattr(sys, 'stdout') and 'pytest' not in sys.modules:
                        logger.info(f"Target Tweet {t_url} context grabbed successfully.")
                    twitter_contexts.append(f"Target Tweet {t_url} Context:\n{tweet_text}")
            except Exception as e:
                logger.warning(f"Failed to fetch Tweet context for {t_url}: {e}")

        if twitter_contexts:
            aggregated_content.append(
                "------\nIncluded Twitter post context follows:\n\n" + "\n\n".join(twitter_contexts)
            )

    # 4. Facebook Video Transcripts
    if facebook_urls:
        facebook_transcripts = []
        for fb_url in facebook_urls:
            try:
                result = await asyncio.to_thread(get_facebook_transcript, fb_url)
                if result:
                    transcript_text = result["transcript_text"]
                    groq_resp = result.get("groq_response")
                    from_cache = result.get("from_cache", False)
                    if hasattr(sys, 'stdout') and 'pytest' not in sys.modules:
                        logger.info(f"Target Facebook Video {fb_url} Transcript grabbed successfully.")
                    facebook_transcripts.append(f"Target Facebook Video {fb_url} Transcript:\n{transcript_text}")

                    if result.get("audio_artifact"):
                        await log_pipeline_step(
                            service_name="downloader/facebook/audio",
                            endpoint_url=fb_url,
                            title="Facebook URL → audio artifact",
                            step="facebook_audio",
                            input_summary="Downloaded Facebook media and extracted audio",
                            input_data={
                                "source_url": fb_url,
                                "download_strategy": result.get("download_strategy"),
                                "source_metadata": result.get("source_metadata"),
                            },
                            output_summary="Produced an audio artifact for transcription",
                            output_data={
                                "source_url": fb_url,
                                "download_strategy": result.get("download_strategy"),
                            },
                            response_artifacts=[result["audio_artifact"]],
                        )

                    if not from_cache:
                        await log_pipeline_step(
                            service_name="downloader/facebook/transcript",
                            endpoint_url=fb_url,
                            title="Facebook audio → transcript",
                            step="facebook_transcript",
                            input_summary="Prepared Facebook audio for transcription",
                            input_data={
                                "source_url": fb_url,
                                "from_cache": False,
                                "download_strategy": result.get("download_strategy"),
                            },
                            output_summary="Produced transcript text for Facebook video content",
                            output_data={
                                "source_url": fb_url,
                                "transcript_text": transcript_text,
                            },
                            request_artifacts=[result["audio_artifact"]] if result.get("audio_artifact") else None,
                            response_artifacts=[
                                build_artifact(
                                    name="facebook_transcript.txt",
                                    media_type="text/plain",
                                    text=transcript_text,
                                    extra={"source_url": fb_url},
                                )
                            ],
                        )
                    
                    if groq_resp:
                        actual_req = groq_resp.request
                        actual_req.read()
                        req_body_bytes = actual_req.content if actual_req.content else b""
                        
                        await log_api_request(
                            service_name="groq/whisper-large-v3-turbo",
                            method=actual_req.method,
                            endpoint_url=str(actual_req.url),
                            request_headers=dict(actual_req.headers),
                            request_body=req_body_bytes.hex() if req_body_bytes else "",
                            response_status=groq_resp.status_code,
                            response_headers=dict(groq_resp.headers),
                            response_body=transcript_text,
                            cost=0.0
                        )
            except Exception as e:
                logger.warning(f"Failed to fetch Facebook transcript for {fb_url}: {e}")

        if facebook_transcripts:
            aggregated_content.append(
                "------\nIncluded Facebook video transcript follows:\n\n" + "\n\n".join(facebook_transcripts)
            )

    # 5. General Articles
    if target_urls:
        articles = []
        for t_url in target_urls:
            try:
                article_text = await get_article_text(t_url)
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
        final_content = "\n\n".join(aggregated_content) + "\n\n"
        await log_pipeline_step(
            service_name="downloader/aggregate",
            endpoint_url="message://downloader-aggregate",
            title="Downloader outputs → injected prompt context",
            step="downloader_aggregate",
            input_summary="Combined all successful downloader outputs into the final context block",
            input_data=discovered_sources,
            output_summary="Produced the exact text block injected into the model prompt",
            output_data={
                "aggregated_content": final_content,
            },
            response_artifacts=[
                build_artifact(
                    name="downloader_aggregate.txt",
                    media_type="text/plain",
                    text=final_content,
                )
            ],
        )
        return final_content
    
    return ""
