import base64
import logging
import discord

logger = logging.getLogger(__name__)


async def attachment_to_base64_data_url(attachment: discord.Attachment) -> str:
    """
    Convert a Discord attachment to a base64 data URL.

    This prevents issues with expired Discord CDN URLs by downloading
    the image and encoding it as a data URL that can be stored in conversation history.

    Args:
        attachment: Discord attachment to convert

    Returns:
        Base64 data URL string (e.g., "data:image/png;base64,...")

    Raises:
        Exception: If download or encoding fails
    """
    try:
        # Download the attachment
        image_bytes = await attachment.read()

        # Encode to base64
        base64_data = base64.b64encode(image_bytes).decode('utf-8')

        # Determine content type from filename or default to image/png
        content_type = attachment.content_type or "image/png"

        # Create data URL
        data_url = f"data:{content_type};base64,{base64_data}"

        logger.debug(
            f"Converted attachment {
                attachment.filename} to base64 data URL ({
                len(base64_data)} chars)")
        return data_url

    except Exception as e:
        logger.error(f"Failed to convert attachment to base64: {e}")
        raise


# Simple bounded cache for base64 URLs
_url_base64_cache = {}
MAX_CACHE_SIZE = 100

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
        logger.debug(f"Cache hit for URL base64: {url}")
        return _url_base64_cache[url]

    import httpx
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            image_bytes = response.content

            # Attempt to determine content-type from headers
            content_type = response.headers.get("Content-Type", "image/png")
            # Fallback if the URL endpoint is dumb and didn't provide it
            if not content_type.startswith("image/"):
                content_type = "image/png"

            # Encode to base64
            base64_data = base64.b64encode(image_bytes).decode('utf-8')

            # Create data URL
            data_url = f"data:{content_type};base64,{base64_data}"
            
            logger.debug(f"Converted embed URL to base64 data URL ({len(base64_data)} chars)")

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
