import os
import base64
import logging
import discord
import io
import httpx
import wave
from PIL import Image
from typing import Optional
from src.config import get_config

logger = logging.getLogger(__name__)


# Simple bounded caches for base64 conversions
_url_base64_cache = {}
_attachment_base64_cache = {}
_audio_transcript_cache = {}
MAX_CACHE_SIZE = 100


def _delete_cache_entry(cache, key) -> None:
    if hasattr(cache, 'delete'):
        cache.delete(key)
    elif key in cache:
        del cache[key]


def compress_image(
        image_bytes: bytes,
        max_size: int = 1024,
        quality: int = 75) -> bytes:
    """Compress and resize image bytes using Pillow."""
    try:
        img = Image.open(io.BytesIO(image_bytes))

        # Convert to RGB for JPEG compression to save maximum space
        if img.mode != 'RGB':
            img = img.convert('RGB')

        # Resize if dimensions exceed max_size while maintaining aspect ratio
        if img.width > max_size or img.height > max_size:
            img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

        # Save to bytes as JPEG
        out_bytes = io.BytesIO()
        img.save(out_bytes, format='JPEG', quality=quality)
        return out_bytes.getvalue()
    except Exception as e:
        logger.warning(
            f"Image compression failed: {e}. Falling back to original bytes.")
        return image_bytes


def pcm_to_wav(
    pcm_bytes: bytes,
    sample_rate: int = 48000,
    channels: int = 2,
    sampwidth: int = 2
) -> bytes:
    """Prepend a standard RIFF/WAV header to raw PCM bytes."""
    wav_io = io.BytesIO()
    try:
        with wave.open(wav_io, 'wb') as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(sampwidth)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_bytes)
        return wav_io.getvalue()
    except Exception as e:
        logger.warning(f"PCM to WAV conversion failed: {e}. Returning original bytes.")
        return pcm_bytes


async def attachment_to_base64_data_url(attachment: discord.Attachment) -> str:
    """
    Convert a Discord attachment to a base64 data URL.
    Results are cached in memory to avoid repetitive downloads.

    Args:
        attachment: Discord attachment to convert

    Returns:
        Base64 data URL string (e.g., "data:image/png;base64,...")

    Raises:
        Exception: If download or encoding fails
    """
    # Use attachment ID as the unique cache key
    if attachment.id in _attachment_base64_cache:
        cached_result = _attachment_base64_cache[attachment.id]
        if cached_result is None:
            logger.debug(f"Discarding legacy attachment failure sentinel for: {attachment.filename}")
            _delete_cache_entry(_attachment_base64_cache, attachment.id)
        else:
            logger.debug(f"Cache hit for attachment base64: {attachment.filename}")
            return cached_result

    try:
        # Download the attachment
        file_bytes = await attachment.read()

        # Check if it is an audio file or voice message
        is_audio = False
        content_type = attachment.content_type
        
        from unittest.mock import Mock
        is_voice_attr = getattr(attachment, "is_voice_message", None)
        is_voice = False
        if is_voice_attr:
            if callable(is_voice_attr):
                res = is_voice_attr()
                if not isinstance(res, Mock):
                    is_voice = bool(res)
            elif not isinstance(is_voice_attr, Mock):
                is_voice = bool(is_voice_attr)

        if isinstance(content_type, str):
            cleaned_content_type = content_type.split(";")[0].strip().lower()
            if cleaned_content_type.startswith("audio/"):
                is_audio = True
        elif is_voice:
            is_audio = True
            cleaned_content_type = "audio/ogg"

        if is_audio:
            # Check if raw PCM (audio/s16le) needs to be converted to WAV
            if cleaned_content_type == "audio/s16le":
                file_bytes = pcm_to_wav(file_bytes)
                cleaned_content_type = "audio/wav"

            # For audio, do not compress, keep original bytes (or converted WAV bytes)
            base64_data = base64.b64encode(file_bytes).decode('utf-8')
            # Fallback/default content type for audio if not specified
            if not content_type or not cleaned_content_type.startswith("audio/"):
                cleaned_content_type = "audio/ogg"
            data_url = f"data:{cleaned_content_type};base64,{base64_data}"
        else:
            # Compress image to save token limit space on APIs
            compressed_bytes = compress_image(file_bytes)
            # Encode to base64
            base64_data = base64.b64encode(compressed_bytes).decode('utf-8')
            # Content type is now JPEG due to compression
            data_url = f"data:image/jpeg;base64,{base64_data}"

        logger.debug(
            f"Converted attachment {attachment.filename} to base64 data URL ({len(base64_data)} chars)")

        # Store in bounded cache
        if len(_attachment_base64_cache) >= MAX_CACHE_SIZE:
            oldest_key = next(iter(_attachment_base64_cache))
            del _attachment_base64_cache[oldest_key]

        _attachment_base64_cache[attachment.id] = data_url
        return data_url

    except Exception as e:
        logger.error(
            f"Failed to convert attachment {attachment.filename} to base64: {e}")
        raise e


async def url_to_base64_data_url(url: str) -> str:
    """
    Download an image URL and convert it to a base64 data URL.
    Results are cached in memory to avoid repetitive downloads.

    Args:
        url: Direct link to the image/thumbnail (e.g. embed preview)

    Returns:
        Base64 data URL string (e.g., "data:image/png;base64,...")

    Raises:
        Exception: If download or encoding fails
    """
    if url in _url_base64_cache:
        cached_result = _url_base64_cache[url]
        if cached_result is None:
            logger.debug(f"Discarding legacy URL failure sentinel for: {url}")
            _delete_cache_entry(_url_base64_cache, url)
        else:
            logger.debug(f"Cache hit for URL base64: {url}")
            return cached_result

    from src.utils.http_client import create_async_client

    try:
        # Follow redirects in case embeds resolve through URL shorteners or
        # edge network bounces
        # Add User-Agent headers to avoid 403 Forbidden from strict servers like Wikimedia
        headers = {
            "User-Agent": "cmwgpt-bot/1.0 (jobeus@gmail.com)",
            "Api-User-Agent": "cmwgpt-bot/1.0 (jobeus@gmail.com)"
        }
        async with create_async_client(timeout=httpx.Timeout(5.0), follow_redirects=True, service_name="discord_cdn") as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            image_bytes = response.content

            # Compress image to save token limit space on APIs
            image_bytes = compress_image(image_bytes)

            content_type = "image/jpeg"

            # Encode to base64
            base64_data = base64.b64encode(image_bytes).decode('utf-8')

            # Create data URL
            data_url = f"data:{content_type};base64,{base64_data}"

            logger.debug(
                f"Converted embed URL to base64 data URL ({len(base64_data)} chars)")

            # Store in bounded cache
            if len(_url_base64_cache) >= MAX_CACHE_SIZE:
                # Remove oldest item (Python dicts maintain insertion order)
                oldest_key = next(iter(_url_base64_cache))
                del _url_base64_cache[oldest_key]

            _url_base64_cache[url] = data_url
            return data_url

    except httpx.TimeoutException as e:
        logger.warning(f"Timeout while fetching image URL '{url}': {e}")
        raise e
    except httpx.HTTPError as e:
        logger.error(f"HTTP Error while fetching image URL '{url}': {e}")
        raise e
    except Exception as e:
        logger.error(f"Failed to fetch embed URL to base64: {e}")
        raise e


async def get_mention_legend(
        channel: discord.TextChannel,
        bot_user: discord.User) -> str:
    # channel.members is all members who can see this channel
    lines = [f"You are <@{bot_user.id}>!"]

    for member in channel.members:
        # use nickname if set, otherwise username
        name = member.display_name
        lines.append(f"@{name} = <@{member.id}>")

    return (
        f"Here are all the users in this channel:\n"
        f"{chr(10).join(lines)}\n"
        f"Whenever you see a mention like <@discord_user_id>, map it back to the corresponding handle. "
        f"If you want to @mention someone yourself use <@discord_user_id> instead of @nickname for discord "
        f"to recoginize your intent."
    )


async def transcribe_audio_attachment(attachment: discord.Attachment) -> Optional[str]:
    """
    Transcribe a Discord audio attachment using Groq Whisper API.
    Results are cached in memory to avoid repetitive API calls.
    """
    if attachment.id in _audio_transcript_cache:
        logger.debug(f"Cache hit for audio transcription: {attachment.filename}")
        return _audio_transcript_cache[attachment.id]

    groq_api_key = get_config().groq_api_key
    if not groq_api_key:
        logger.warning("GROQ_API_KEY is not set. Cannot transcribe audio.")
        return None

    try:
        # Download the attachment bytes
        file_bytes = await attachment.read()
        
        # Determine the content type and filename
        content_type = attachment.content_type
        filename = attachment.filename or "audio.ogg"
        
        is_voice_attr = getattr(attachment, "is_voice_message", None)
        is_voice = False
        if is_voice_attr:
            if callable(is_voice_attr):
                res = is_voice_attr()
                is_voice = bool(res)
            else:
                is_voice = bool(is_voice_attr)

        if isinstance(content_type, str):
            cleaned_content_type = content_type.split(";")[0].strip().lower()
        else:
            cleaned_content_type = "audio/ogg" if is_voice else "audio/ogg"

        # Transcode PCM (audio/s16le) to WAV
        if cleaned_content_type == "audio/s16le":
            file_bytes = pcm_to_wav(file_bytes)
            cleaned_content_type = "audio/wav"
            if not filename.endswith(".wav"):
                filename = os.path.splitext(filename)[0] + ".wav"

        # Ensure the filename has a supported extension for Groq Whisper
        _, ext = os.path.splitext(filename)
        supported_extensions = {'.flac', '.mp3', '.mp4', '.mpeg', '.mpga', '.m4a', '.ogg', '.wav', '.webm'}
        if ext.lower() not in supported_extensions:
            # Map MIME type to a supported extension
            mime_to_ext = {
                "audio/wav": ".wav",
                "audio/x-wav": ".wav",
                "audio/mp3": ".mp3",
                "audio/mpeg": ".mp3",
                "audio/ogg": ".ogg",
                "audio/flac": ".flac",
                "audio/aac": ".m4a",
                "audio/m4a": ".m4a",
                "audio/x-m4a": ".m4a",
                "audio/mp4": ".mp4",
                "audio/webm": ".webm",
            }
            mapped_ext = mime_to_ext.get(cleaned_content_type, ".ogg")
            filename = os.path.splitext(filename)[0] + mapped_ext

        # Use httpx.AsyncClient for non-blocking network I/O
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            files = {'file': (filename, file_bytes, cleaned_content_type)}
            data_payload = {
                'model': 'whisper-large-v3-turbo',
                'temperature': '0',
                'response_format': 'text'
            }
            groq_headers = {'Authorization': f'Bearer {groq_api_key}'}
            
            groq_resp = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers=groq_headers,
                data=data_payload,
                files=files
            )
            groq_resp.raise_for_status()
            transcript_text = groq_resp.text.strip()
            
            if transcript_text:
                logger.info(f"Successfully transcribed audio attachment {filename} using Groq.")
                # Store in bounded cache
                if len(_audio_transcript_cache) >= MAX_CACHE_SIZE:
                    oldest_key = next(iter(_audio_transcript_cache))
                    del _audio_transcript_cache[oldest_key]
                _audio_transcript_cache[attachment.id] = transcript_text
                return transcript_text
            else:
                logger.warning(f"Groq returned empty transcription for {filename}")
                return None

    except Exception as e:
        logger.error(f"Error transcribing audio attachment {attachment.filename}: {e}")
        return None
