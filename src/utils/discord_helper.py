import base64
import logging
import discord
import io
from PIL import Image

logger = logging.getLogger(__name__)


# Simple bounded caches for base64 conversions
_url_base64_cache = {}
_attachment_base64_cache = {}
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
        image_bytes = await attachment.read()

        # Compress image to save token limit space on APIs
        image_bytes = compress_image(image_bytes)

        # Encode to base64
        base64_data = base64.b64encode(image_bytes).decode('utf-8')

        # Content type is now JPEG due to compression
        content_type = "image/jpeg"

        # Create data URL
        data_url = f"data:{content_type};base64,{base64_data}"

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

    import httpx

    try:
        # Follow redirects in case embeds resolve through URL shorteners or
        # edge network bounces
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            response = await client.get(url)
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
