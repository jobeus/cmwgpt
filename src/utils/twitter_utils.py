import os
import re
import uuid
import logging
import requests
import subprocess
import asyncio
import httpx
from typing import List, Optional
from groq import AsyncGroq

from src.config import RAPIDAPI_KEY, GROQ_API_KEY
from src.utils.cache_utils import PersistentCache
from src.db.logger import log_api_request

logger = logging.getLogger(__name__)

# Bounded persistent cache for Tweets: url -> text or None
_twitter_cache = PersistentCache('twitter_transcripts')


def extract_twitter_urls(text: str) -> List[str]:
    """
    Extract x.com and twitter.com and xcancel.com URLs from text.
    """
    if not text:
        return []

    # Regex targeting twitter/x URLs
    pattern = r'https?://(?:www\.)?(?:twitter\.com|x\.com|xcancel\.com)/[^\s<>"]+'

    matches = re.finditer(pattern, text)
    urls = []

    for match in matches:
        url = match.group(0)
        if url not in urls:
            urls.append(url)

    return urls


def extract_video_url(data):
    try:
        entries = data['data']['threaded_conversation_with_injections_v2']['instructions'][1]['entries']
        result = entries[0]['content']['itemContent']['tweet_results']['result']
        media = result['legacy']['extended_entities']['media']
        for m in media:
            if m.get('video_info'):
                variants = m['video_info']['variants']
                mp4s = [v for v in variants if v.get('content_type') == 'video/mp4']
                if mp4s:
                    return min(mp4s, key=lambda v: v.get('bitrate', 999999))['url']
    except (KeyError, TypeError, IndexError):
        return None


async def get_tweet_context(tweet_url: str) -> Optional[str]:
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


    if not RAPIDAPI_KEY:
        logger.error("RAPIDAPI_KEY is not set. Cannot fetch Twitter content.")
        return None

    try:
        # Extract the trailing ID, ignoring any query parameters
        tweet_id = tweet_url.rstrip('/').split('/')[-1].split('?')[0]
        
        headers = {
            "x-rapidapi-host": "x-com2.p.rapidapi.com",
            "x-rapidapi-key": RAPIDAPI_KEY
        }
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                "https://x-com2.p.rapidapi.com/v2/TweetDetail/",
                headers=headers,
                params={"id": tweet_id}
            )
            response.raise_for_status()
            
            data = response.json()

            await log_api_request(
                service_name="rapidapi/twitter",
                method="GET",
                endpoint_url="https://x-com2.p.rapidapi.com/v2/TweetDetail/",
                request_headers=headers,
                request_body={"id": tweet_id},
                response_status=response.status_code,
                response_headers=dict(response.headers),
                response_body=data,
                cost=0.0
            )
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
        
        # Check for video and transcribe
        video_url = extract_video_url(data)
        video_transcript = None
        if video_url and GROQ_API_KEY:
            try:
                tmp_id = uuid.uuid4().hex
                mp4_path = f"/tmp/twit_vid_{tmp_id}.mp4"
                mp3_path = f"/tmp/twit_aud_{tmp_id}.mp3"
                
                logger.info(f"Downloading Twitter video: {video_url}")
                async with httpx.AsyncClient(timeout=30.0) as client:
                    async with client.stream('GET', video_url) as r:
                        r.raise_for_status()
                        with open(mp4_path, 'wb') as f:
                            async for chunk in r.aiter_bytes():
                                f.write(chunk)
                
                logger.info("Converting to mp3 with ffmpeg...")
                # Run ffmpeg in a thread pool to avoid blocking the event loop
                await asyncio.to_thread(
                    subprocess.run,
                    [
                        "ffmpeg", "-y", "-i", mp4_path,
                        "-vn", "-ar", "44100", "-ac", "2", "-b:a", "64k",
                        mp3_path
                    ],
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                
                if os.path.exists(mp3_path):
                    groq_client = AsyncGroq(api_key=GROQ_API_KEY)
                    logger.info(f"Transcribing {mp3_path} using Groq...")
                    with open(mp3_path, "rb") as file:
                        transcription = await groq_client.audio.transcriptions.create(
                            file=(os.path.basename(mp3_path), file.read()),
                            model="whisper-large-v3-turbo",
                            temperature=0,
                            response_format="text",
                        )

                    transcript_text = transcription.strip()
                    if transcript_text:
                        video_transcript = transcript_text
                        await log_api_request(
                            service_name="groq/whisper-large-v3-turbo",
                            method="POST",
                            endpoint_url="https://api.groq.com/openai/v1/audio/transcriptions",
                            request_headers={"Authorization": f"Bearer {GROQ_API_KEY[:8]}..."} if GROQ_API_KEY else {},
                            request_body={"model": "whisper-large-v3-turbo", "source_url": tweet_url, "video_url": video_url},
                            response_status=200,
                            response_headers={},
                            response_body=transcript_text,
                            cost=0.0
                        )
            except Exception as e:
                logger.warning(f"Failed to process video for tweet {tweet_url}: {e}")
            finally:
                if 'mp4_path' in locals() and os.path.exists(mp4_path):
                    try:
                        os.remove(mp4_path)
                    except Exception:
                        pass
                if 'mp3_path' in locals() and os.path.exists(mp3_path):
                    try:
                        os.remove(mp3_path)
                    except Exception:
                        pass
        
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
        
        if video_transcript:
            context += f"\ntranscript of video: {video_transcript}"
            
        if replies:
            context += "\n\nTop replies:\n" + "\n".join(replies)
        
        _twitter_cache[tweet_url] = context
        return context

    except KeyError as ke:
        logger.warning(f"Failed to parse RapidAPI JSON response for {tweet_url}: {ke}")
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch Twitter text for {tweet_url}: {e}")
        return None
