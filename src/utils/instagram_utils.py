import os
import re
import uuid
import logging
import tempfile
import subprocess
import asyncio
import httpx
from typing import List, Optional, Tuple
from src.config import get_config
from src.utils.cache_utils import PersistentCache
from src.db.logger import build_artifact, log_pipeline_step
from src.utils.http_client import create_async_client
from src.utils.discord_helper import url_to_base64_data_url

logger = logging.getLogger(__name__)

# Bounded persistent cache for Instagram: url -> tuple(text_context, image_data_url or None)
_instagram_cache = PersistentCache('instagram_transcripts')


def extract_instagram_urls(text: str) -> List[str]:
    """
    Extract instagram.com and threads.com URLs from text.
    """
    if not text:
        return []

    pattern = r'https?://(?:www\.)?(?:instagram\.com|threads\.net)/[^\s<>"]+'

    matches = re.finditer(pattern, text)
    urls = []

    for match in matches:
        url = match.group(0)
        # Normalize trailing slashes and query params for dedup
        normalized = url.split('?')[0].rstrip('/')
        if normalized not in urls:
            urls.append(normalized)

    # Note: user might have written threads.com, but canonical is typically threads.net.
    # We should support threads.com as well based on the prompt.
    pattern2 = r'https?://(?:www\.)?threads\.com/[^\s<>"]+'
    matches2 = re.finditer(pattern2, text)
    for match in matches2:
        url = match.group(0)
        normalized = url.split('?')[0].rstrip('/')
        if normalized not in urls:
            urls.append(normalized)

    return urls


async def get_instagram_context(url: str) -> Optional[Tuple[str, Optional[str]]]:
    """
    Fetch the content of an Instagram/Threads URL using RapidAPI.
    Results are cached to persistent disk.
    Returns a tuple of (context_string, image_data_url).
    """
    if url in _instagram_cache:
        cached_result = _instagram_cache[url]
        if cached_result is None:
            logger.debug(f"Cache hit for Instagram fetch failure: {url}")
            return None
        logger.debug(f"Cache hit for Instagram fetch: {url}")
        return cached_result

    cfg = get_config()
    if not cfg.rapidapi_key:
        logger.error("RAPIDAPI_KEY is not set. Cannot fetch Instagram content.")
        return None

    try:
        headers = {
            "x-rapidapi-host": "instagram-downloader-download-instagram-videos-stories1.p.rapidapi.com",
            "x-rapidapi-key": cfg.rapidapi_key
        }
        
        async with create_async_client(timeout=httpx.Timeout(15.0), service_name="rapidapi/instagram") as client:
            response = await client.get(
                "https://instagram-downloader-download-instagram-videos-stories1.p.rapidapi.com/get-info-rapidapi",
                headers=headers,
                params={"url": url}
            )
            response.raise_for_status()
            
            data = response.json()

        if data.get("error"):
            logger.warning(f"Error returned from RapidAPI for Instagram {url}: {data}")
            return None

        media_type = data.get("type")
        caption = data.get("caption", "No caption provided.")
        download_url = data.get("download_url")
        posting_source = data.get("hosting", "Instagram/Threads")
        
        context = f"Post on {posting_source}:\nCaption: {caption}"
        image_data_url = None
        
        if media_type == "image" and download_url:
            try:
                # Convert the image to base64 data URL
                image_data_url = await url_to_base64_data_url(download_url)
                await log_pipeline_step(
                    service_name="downloader/instagram/image",
                    endpoint_url=url,
                    title="Instagram/Threads image URL → data URL",
                    step="instagram_image",
                    input_summary="Downloaded Instagram/Threads image",
                    input_data={"source_url": url, "image_url": download_url},
                    output_summary="Produced an image data URL for the bot context",
                )
            except Exception as e:
                logger.warning(f"Failed to fetch image data url for {url}: {e}")

        elif media_type == "video" and download_url and cfg.groq_api_key:
            # It's a video, let's transcribe it
            mp4_path = None
            mp3_path = None
            video_transcript = None
            try:
                tmp_id = uuid.uuid4().hex
                temp_dir = tempfile.gettempdir()
                mp4_path = os.path.join(temp_dir, f"insta_vid_{tmp_id}.mp4")
                mp3_path = os.path.join(temp_dir, f"insta_aud_{tmp_id}.mp3")

                logger.info(f"Downloading Instagram video: {download_url}")
                async with create_async_client(timeout=httpx.Timeout(30.0), service_name="rapidapi/instagram/media") as client:
                    async with client.stream('GET', download_url) as r:
                        r.raise_for_status()
                        with open(mp4_path, 'wb') as f:
                            async for chunk in r.aiter_bytes():
                                f.write(chunk)
                
                logger.info("Converting to mp3 with ffmpeg...")
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
                    logger.info(f"Transcribing {mp3_path} using Groq via httpx...")
                    with open(mp4_path, "rb") as file:
                        video_bytes = file.read()
                    with open(mp3_path, "rb") as file:
                        audio_bytes = file.read()

                    await log_pipeline_step(
                        service_name="downloader/instagram/video_audio",
                        endpoint_url=url,
                        title="Instagram video URL → audio artifact",
                        step="instagram_video_audio",
                        input_summary="Downloaded video and converted it to MP3",
                        input_data={
                            "url": url,
                            "video_url": download_url,
                        },
                        response_artifacts=[
                            build_artifact(
                                name=os.path.basename(mp4_path),
                                media_type="video/mp4",
                                data=video_bytes,
                                extra={"source_url": url},
                            ),
                            build_artifact(
                                name=os.path.basename(mp3_path),
                                media_type="audio/mpeg",
                                data=audio_bytes,
                                extra={"source_url": url},
                            ),
                        ],
                    )

                    async with create_async_client(timeout=httpx.Timeout(60.0), service_name="groq") as client:
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
                            service_name="downloader/instagram/transcript",
                            endpoint_url=url,
                            title="Instagram audio → transcript",
                            step="instagram_transcript",
                            input_summary="Transcribed Instagram audio with Groq Whisper",
                            input_data={
                                "url": url,
                                "model": "whisper-large-v3-turbo",
                            },
                            output_summary="Produced transcript text for embedded Instagram video",
                            output_data={
                                "url": url,
                                "transcript_text": transcript_text,
                            },
                        )

            except Exception as e:
                logger.warning(f"Failed to process video for Instagram {url}: {e}")
            finally:
                if mp4_path and os.path.exists(mp4_path):
                    try:
                        os.remove(mp4_path)
                    except OSError:
                        pass
                if mp3_path and os.path.exists(mp3_path):
                    try:
                        os.remove(mp3_path)
                    except OSError:
                        pass

            if video_transcript:
                context += f"\nVideo Transcript: {video_transcript}"

        await log_pipeline_step(
            service_name="downloader/instagram/context",
            endpoint_url=url,
            title="Instagram JSON → final context",
            step="instagram_context",
            input_summary="Built Instagram context from API response",
            input_data={
                "url": url,
                "caption": caption,
                "media_type": media_type,
            },
            output_summary="Produced the Instagram context string injected into the prompt",
            output_data={
                "url": url,
                "context": context,
            },
            response_artifacts=[
                build_artifact(
                    name="instagram_context.txt",
                    media_type="text/plain",
                    text=context,
                    extra={"source_url": url},
                )
            ],
        )

        result_tuple = (context, image_data_url)
        _instagram_cache[url] = result_tuple
        return result_tuple

    except KeyError as ke:
        logger.warning(f"Failed to parse RapidAPI JSON response for Instagram {url}: {ke}")
        return None
    except httpx.HTTPError as he:
        logger.warning(f"HTTPError fetching Instagram {url}: {he}")
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch Instagram text for {url}: {e}")
        return None
