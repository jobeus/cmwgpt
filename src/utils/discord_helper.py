import asyncio
import base64
import io
import logging
import os
import tempfile
import wave
from typing import Optional

import discord
from PIL import Image

from src.utils.cache_utils import LRUCache
from src.utils.http_client import fetch_public
from src.utils.media_transcribe import GROQ_MAX_UPLOAD_BYTES, transcribe_audio, transcribe_audio_file

logger = logging.getLogger(__name__)

# Refuse to decode images bigger than this (Pillow's own default is ~89M px and
# only a warning); a decompression bomb would otherwise stall a worker thread
# and eat hundreds of MB.
Image.MAX_IMAGE_PIXELS = 40_000_000

MAX_IMAGE_DOWNLOAD_BYTES = 20 * 1024 * 1024

# In-memory caches; sized for a full 100-message history window with images.
MAX_CACHE_SIZE = 300
_url_base64_cache: LRUCache[str] = LRUCache(MAX_CACHE_SIZE)
_attachment_base64_cache: LRUCache[str] = LRUCache(MAX_CACHE_SIZE)
_audio_transcript_cache: LRUCache[str] = LRUCache(MAX_CACHE_SIZE)

_IMAGE_FETCH_HEADERS = {
    # Strict servers like Wikimedia 403 requests without a descriptive UA.
    "User-Agent": "cmwgpt-bot/1.0 (jobeus@gmail.com)",
    "Api-User-Agent": "cmwgpt-bot/1.0 (jobeus@gmail.com)",
}

GROQ_AUDIO_EXTENSIONS = {'.flac', '.mp3', '.mp4', '.mpeg', '.mpga', '.m4a', '.ogg', '.wav', '.webm'}
_MIME_TO_EXTENSION = {
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


def is_voice_message(attachment: discord.Attachment) -> bool:
    """True if Discord flags the attachment as a voice message."""
    attr = getattr(attachment, "is_voice_message", None)
    if callable(attr):
        attr = attr()
    return attr is True


def compress_image(
        image_bytes: bytes,
        max_size: int = 1024,
        quality: int = 75) -> bytes:
    """Compress and resize image bytes to a JPEG using Pillow (CPU-bound; run in a thread)."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        # For JPEGs, let the decoder downscale while decoding (much cheaper).
        img.draft('RGB', (max_size, max_size))

        if img.mode != 'RGB':
            img = img.convert('RGB')

        if img.width > max_size or img.height > max_size:
            img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

        out_bytes = io.BytesIO()
        img.save(out_bytes, format='JPEG', quality=quality)
        return out_bytes.getvalue()
    except Exception as e:
        logger.warning(
            f"Image compression failed: {e}. Falling back to original bytes.")
        return image_bytes


async def _image_bytes_to_data_url(image_bytes: bytes) -> str:
    compressed = await asyncio.to_thread(compress_image, image_bytes)
    return f"data:image/jpeg;base64,{base64.b64encode(compressed).decode('ascii')}"


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


def _audio_content_type(attachment: discord.Attachment) -> Optional[str]:
    """The attachment's audio MIME type, or ``None`` if it isn't audio."""
    content_type = attachment.content_type
    if isinstance(content_type, str):
        cleaned = content_type.split(";")[0].strip().lower()
        if cleaned.startswith("audio/"):
            return cleaned
        return None
    return "audio/ogg" if is_voice_message(attachment) else None


async def attachment_to_base64_data_url(attachment: discord.Attachment) -> str:
    """
    Convert a Discord attachment to a base64 data URL (images are compressed to JPEG).
    Results are cached in memory to avoid repetitive downloads.

    Raises:
        Exception: If download or encoding fails
    """
    cached = _attachment_base64_cache.get(attachment.id)
    if cached is not None:
        return cached

    try:
        file_bytes = await attachment.read()
        audio_type = _audio_content_type(attachment)

        if audio_type:
            if audio_type == "audio/s16le":
                file_bytes = pcm_to_wav(file_bytes)
                audio_type = "audio/wav"
            data_url = f"data:{audio_type};base64,{base64.b64encode(file_bytes).decode('ascii')}"
        else:
            data_url = await _image_bytes_to_data_url(file_bytes)

        _attachment_base64_cache[attachment.id] = data_url
        return data_url

    except Exception as e:
        logger.error(
            f"Failed to convert attachment {attachment.filename} to base64: {e}")
        raise


async def url_to_base64_data_url(url: str) -> str:
    """
    Download an image URL and convert it to a compressed JPEG data URL.
    Results are cached in memory to avoid repetitive downloads.

    Raises:
        Exception: If the URL is non-public, too large, or the download fails
    """
    if url.startswith("data:"):
        return url
    cached = _url_base64_cache.get(url)
    if cached is not None:
        return cached

    try:
        response = await fetch_public(
            url, headers=_IMAGE_FETCH_HEADERS, timeout=10.0, max_bytes=MAX_IMAGE_DOWNLOAD_BYTES)
        response.raise_for_status()
        data_url = await _image_bytes_to_data_url(response.content)
        _url_base64_cache[url] = data_url
        return data_url
    except Exception as e:
        logger.warning(f"Failed to fetch image URL '{url}': {type(e).__name__}: {e}")
        raise


async def get_mention_legend(
        channel: discord.TextChannel,
        bot_user: discord.User) -> str:
    # channel.members is all members who can see this channel
    lines = [f"You are <@{bot_user.id}>!"]

    # Sorted by ID so the list (part of the cached system prompt) doesn't reorder between requests.
    for member in sorted(channel.members, key=lambda m: m.id):
        # use nickname if set, otherwise username
        name = member.display_name
        lines.append(f"@{name} = <@{member.id}>")

    return (
        f"Here are all the users in this channel:\n"
        f"{chr(10).join(lines)}\n"
        f"A <@discord_user_id> in the chat refers to the person with that handle above. To @mention someone, "
        f"write <@discord_user_id>; a plain @nickname is just text and won't ping them."
    )


async def transcribe_audio_attachment(attachment: discord.Attachment) -> Optional[str]:
    """
    Transcribe a Discord audio attachment using Groq Whisper.
    Results are cached in memory to avoid repetitive API calls.
    """
    cached = _audio_transcript_cache.get(attachment.id)
    if cached is not None:
        return cached

    try:
        file_bytes = await attachment.read()
        content_type = _audio_content_type(attachment) or "audio/ogg"
        filename = attachment.filename or "audio.ogg"

        if content_type == "audio/s16le":
            file_bytes = pcm_to_wav(file_bytes)
            content_type = "audio/wav"
            filename = os.path.splitext(filename)[0] + ".wav"

        # Groq picks the decoder from the file extension.
        if os.path.splitext(filename)[1].lower() not in GROQ_AUDIO_EXTENSIONS:
            filename = os.path.splitext(filename)[0] + _MIME_TO_EXTENSION.get(content_type, ".ogg")

        if len(file_bytes) <= GROQ_MAX_UPLOAD_BYTES:
            transcript = await transcribe_audio(file_bytes, filename, content_type)
        else:
            # Too big for one Groq upload: let the chunker split it.
            with tempfile.TemporaryDirectory(prefix="attachment_") as temp_dir:
                path = os.path.join(temp_dir, filename)
                with open(path, "wb") as f:
                    f.write(file_bytes)
                transcript = await transcribe_audio_file(path, content_type)
        if transcript:
            logger.info(f"Successfully transcribed audio attachment {filename} using Groq.")
            _audio_transcript_cache[attachment.id] = transcript
        return transcript

    except Exception as e:
        logger.error(f"Error transcribing audio attachment {attachment.filename}: {e}")
        return None
