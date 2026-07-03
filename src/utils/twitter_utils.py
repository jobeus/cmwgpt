import os
import re
import uuid
import logging
import tempfile
import subprocess  # nosec B404
import asyncio
import httpx
from typing import List, Optional
from src.config import get_config
from src.utils.cache_utils import PersistentCache
from src.db.logger import build_artifact, log_pipeline_step
from src.utils.http_client import create_async_client
from src.utils.discord_helper import url_to_base64_data_url
from typing import Tuple

logger = logging.getLogger(__name__)

# Bounded persistent cache for Tweets: url -> text or None
_twitter_cache = PersistentCache('twitter_transcripts')


def _delete_cache_entry(cache, key: str) -> None:
    if hasattr(cache, 'delete'):
        cache.delete(key)
    elif key in cache:
        del cache[key]


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


def extract_media(data):
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
                    # Pick smallest bitrate video for transcription efficiency if multiple exist
                    # Wait, do we want best video for download? Smallest is fine for audio extraction.
                    video_url = min(mp4s, key=lambda v: v.get('bitrate', 999999))['url']
                    media_items.append({'type': 'video', 'url': video_url})
    except (KeyError, TypeError, IndexError):
        pass
    return media_items


async def get_tweet_context(tweet_url: str) -> Optional[Tuple[str, List[str]]]:
    """
    Fetch the text content of a tweet and its top replies using RapidAPI.
    Results are cached to persistent disk.
    """
    if tweet_url in _twitter_cache:
        cached_result = _twitter_cache[tweet_url]
        if cached_result is None:
            logger.debug(f"Discarding legacy Twitter fetch failure sentinel for: {tweet_url}")
            _delete_cache_entry(_twitter_cache, tweet_url)
        else:
            logger.debug(f"Cache hit for Twitter fetch: {tweet_url}")
            return cached_result

    cfg = get_config()
    if not cfg.rapidapi_key:
        logger.error("RAPIDAPI_KEY is not set. Cannot fetch Twitter content.")
        return None

    try:
        # Extract the numeric tweet ID following /status/, ignoring any
        # trailing path segments (e.g. /photo/1) or query parameters.
        id_match = re.search(r'/status/(\d+)', tweet_url)
        if not id_match:
            logger.warning(f"No /status/<id> tweet ID found in URL, skipping: {tweet_url}")
            return None
        tweet_id = id_match.group(1)

        headers = {
            "x-rapidapi-host": "x-com2.p.rapidapi.com",
            "x-rapidapi-key": cfg.rapidapi_key
        }
        
        async with create_async_client(timeout=httpx.Timeout(15.0)) as client:
            response = await client.get(
                "https://x-com2.p.rapidapi.com/v2/TweetDetail/",
                headers=headers,
                params={"id": tweet_id}
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
        
        # Check for media (video or photos)
        media_items = extract_media(data)
        
        video_url = None
        for m in media_items:
            if m['type'] == 'video':
                video_url = m['url']
                break
                
        video_transcript = None
        if video_url and cfg.groq_api_key:
            mp4_path = None
            mp3_path = None
            try:
                tmp_id = uuid.uuid4().hex
                temp_dir = tempfile.gettempdir()
                mp4_path = os.path.join(temp_dir, f"twit_vid_{tmp_id}.mp4")
                mp3_path = os.path.join(temp_dir, f"twit_aud_{tmp_id}.mp3")

                logger.info(f"Downloading Twitter video: {video_url}")
                async with create_async_client(timeout=httpx.Timeout(30.0)) as client:
                    async with client.stream('GET', video_url) as r:
                        r.raise_for_status()
                        with open(mp4_path, 'wb') as f:
                            async for chunk in r.aiter_bytes():
                                f.write(chunk)
                
                logger.info("Converting to mp3 with ffmpeg...")
                # Run ffmpeg in a thread pool to avoid blocking the event loop.
                # timeout guards against a hung ffmpeg pinning the worker thread forever.
                await asyncio.to_thread(
                    subprocess.run,
                    [
                        "ffmpeg", "-y", "-i", mp4_path,
                        "-vn", "-ar", "44100", "-ac", "2", "-b:a", "64k",
                        mp3_path
                    ],
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=300
                )
                
                if os.path.exists(mp3_path):
                    logger.info(f"Transcribing {mp3_path} using Groq via httpx...")
                    with open(mp4_path, "rb") as file:
                        video_bytes = file.read()
                    with open(mp3_path, "rb") as file:
                        audio_bytes = file.read()

                    await log_pipeline_step(
                        service_name="downloader/twitter/video_audio",
                        endpoint_url=tweet_url,
                        title="Twitter video URL → audio artifact",
                        step="twitter_video_audio",
                        input_summary="Downloaded tweet video and converted it to MP3",
                        input_data={
                            "tweet_url": tweet_url,
                            "video_url": video_url,
                            "ffmpeg": ["-vn", "-ar", "44100", "-ac", "2", "-b:a", "64k"],
                        },
                        output_summary="Produced local video and audio artifacts for transcription",
                        output_data={
                            "tweet_url": tweet_url,
                            "video_url": video_url,
                            "mp4_path": mp4_path,
                            "mp3_path": mp3_path,
                        },
                        response_artifacts=[
                            build_artifact(
                                name=os.path.basename(mp4_path),
                                media_type="video/mp4",
                                data=video_bytes,
                                extra={"tweet_url": tweet_url, "video_url": video_url},
                            ),
                            build_artifact(
                                name=os.path.basename(mp3_path),
                                media_type="audio/mpeg",
                                data=audio_bytes,
                                extra={"tweet_url": tweet_url},
                            ),
                        ],
                    )

                    async with create_async_client(timeout=httpx.Timeout(60.0)) as client:
                        files = {'file': (os.path.basename(mp3_path), audio_bytes, 'audio/mpeg')}
                        data_payload = {
                            'model': 'whisper-large-v3-turbo',
                            'temperature': '0',
                            'response_format': 'text'
                        }
                        groq_headers = {'Authorization': f'Bearer {cfg.groq_api_key}'}
                        
                        groq_resp = await client.post(
                            "https://api.groq.com/openai/v1/audio/transcriptions",
                            headers=groq_headers,
                            data=data_payload,
                            files=files
                        )
                        groq_resp.raise_for_status()
                        transcript_text = groq_resp.text.strip()

                    if transcript_text:
                        video_transcript = transcript_text
                        await log_pipeline_step(
                            service_name="downloader/twitter/transcript",
                            endpoint_url=tweet_url,
                            title="Tweet audio → transcript",
                            step="twitter_transcript",
                            input_summary="Transcribed tweet audio with Groq Whisper",
                            input_data={
                                "tweet_url": tweet_url,
                                "video_url": video_url,
                                "model": "whisper-large-v3-turbo",
                            },
                            output_summary="Produced transcript text for embedded tweet video",
                            output_data={
                                "tweet_url": tweet_url,
                                "transcript_text": transcript_text,
                            },
                            request_artifacts=[
                                build_artifact(
                                    name=os.path.basename(mp3_path),
                                    media_type="audio/mpeg",
                                    data=audio_bytes,
                                    extra={"tweet_url": tweet_url},
                                )
                            ],
                            response_artifacts=[
                                build_artifact(
                                    name="tweet_video_transcript.txt",
                                    media_type="text/plain",
                                    text=transcript_text,
                                    extra={"tweet_url": tweet_url},
                                )
                            ],
                        )
            except Exception as e:
                logger.warning(f"Failed to process video for tweet {tweet_url}: {e}")
            finally:
                if mp4_path and os.path.exists(mp4_path):
                    try:
                        os.remove(mp4_path)
                    except OSError as exc:
                        logger.warning(f"Failed to remove temporary file {mp4_path}: {exc}")
                if mp3_path and os.path.exists(mp3_path):
                    try:
                        os.remove(mp3_path)
                    except OSError as exc:
                        logger.warning(f"Failed to remove temporary file {mp3_path}: {exc}")
                        
        # Download and encode images
        image_data_urls = []
        for m in media_items:
            if m['type'] == 'photo':
                try:
                    data_url = await url_to_base64_data_url(m['url'])
                    image_data_urls.append(data_url)
                    await log_pipeline_step(
                        service_name="downloader/twitter/image",
                        endpoint_url=tweet_url,
                        title="Tweet photo URL → data URL",
                        step="twitter_image",
                        input_summary="Downloaded tweet photo",
                        input_data={"tweet_url": tweet_url, "photo_url": m['url']},
                        output_summary="Produced an image data URL for the bot context",
                    )
                except Exception as e:
                    logger.warning(f"Failed to fetch image data url for {tweet_url}: {e}")
        
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

        await log_pipeline_step(
            service_name="downloader/twitter/context",
            endpoint_url=tweet_url,
            title="Tweet detail JSON → final context",
            step="twitter_context",
            input_summary="Built tweet context from RapidAPI response, replies, and optional video transcript",
            input_data={
                "tweet_url": tweet_url,
                "tweet_id": tweet_id,
                "main_author": main_author,
                "main_text": main_text,
                "reply_count": len(replies),
                "video_url": video_url,
                "video_transcript": video_transcript,
            },
            output_summary="Produced the tweet context string injected into the prompt",
            output_data={
                "tweet_url": tweet_url,
                "context": context,
            },
            response_artifacts=[
                build_artifact(
                    name="tweet_context.txt",
                    media_type="text/plain",
                    text=context,
                    extra={"tweet_url": tweet_url},
                )
            ],
        )
        
        result_tuple = (context, image_data_urls)
        _twitter_cache[tweet_url] = result_tuple
        return result_tuple

    except KeyError as ke:
        logger.warning(f"Failed to parse RapidAPI JSON response for {tweet_url}: {ke}")
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch Twitter text for {tweet_url}: {e}")
        return None
