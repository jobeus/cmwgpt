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
        f"Whenever you see a mention like <@USER_ID>, map it back to the corresponding handle. "
        f"If you want to @mention someone yourself use <@USER_ID> instead of @nickname for discord "
        f"to recoginize your intent."
    )
