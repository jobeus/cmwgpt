"""Multimodal chat-context builder for Discord message history.

The mention handler turns a list of ``discord.Message`` objects into the multimodal
``messages`` array the chat-completion services take: text prefixed with
timestamp/ID/sender, reply resolution, embed text, image attachments, audio
transcripts, embed image previews, and assistant-image splitting.
"""

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional

import discord

from src.utils.discord_helper import is_voice_message
from src.utils.message_utils import format_discord_timestamp

logger = logging.getLogger(__name__)

# Pattern to strip cost prefixes like [$0.011] or [$0.034 @ banana-2.1, 9:16] from the
# start of bot messages so the model doesn't parrot them back.
COST_PREFIX_PATTERN = re.compile(r'^\[\$[\d.]+(?:\s*@\s*[^\]]+)?\]\s*')

# Links in only the newest messages are fetched; older history uses cached
# results only, so a mention never waits on links nobody is asking about.
FRESH_FETCH_RECENT_MESSAGES = 10
# How many messages are converted (downloads, transcriptions) at once.
MAX_CONCURRENT_MESSAGES = 8
# The history keeps at most this many images. A window can grow to 200 messages, and enough
# pictures in it would pass the API's request size limit and fail every mention until it reset.
MAX_HISTORY_IMAGES = 40
# Past the limit, the oldest images are dropped this many at a time rather than one by one:
# dropping one changes the history from that point, which the prompt cache then re-writes.
HISTORY_IMAGE_DROP_STEP = 20
DROPPED_IMAGE_NOTE = "[An image was here. It's left out because this conversation has a lot of images.]"


def cap_history_images(chat_context: List[Dict[str, Any]]) -> int:
    """Replace the oldest images in ``chat_context`` with a note once there are too many.

    Returns how many were dropped. Which ones go depends only on how many images there are,
    so the same history always loses the same images and the cached prefix stays the same.
    """
    images = [
        (entry, index)
        for entry in chat_context if isinstance(entry.get("content"), list)
        for index, part in enumerate(entry["content"]) if isinstance(part, dict) and part.get("type") == "image_url"
    ]
    if len(images) <= MAX_HISTORY_IMAGES:
        return 0
    over = len(images) - MAX_HISTORY_IMAGES
    drop = -(-over // HISTORY_IMAGE_DROP_STEP) * HISTORY_IMAGE_DROP_STEP
    for entry, index in images[:drop]:
        entry["content"][index] = {"type": "text", "text": DROPPED_IMAGE_NOTE}
    return drop


async def build_chat_context(
    messages: List[discord.Message],
    bot_id: int,
    *,
    attachment_converter: Callable[[discord.Attachment], Awaitable[str]],
    url_converter: Callable[[str], Awaitable[str]],
    transcriber: Callable[[discord.Attachment], Awaitable[Optional[str]]],
    url_content_fetcher: Optional[
        Callable[..., Awaitable[tuple[str, List[Dict[str, Any]]]]]
    ] = None,
) -> List[Dict[str, Any]]:
    """Convert Discord message history into a multimodal chat-completion array.

    Args:
        messages: Discord messages, oldest first.
        bot_id: The bot's user ID; its messages become the ``assistant`` role.
        attachment_converter: Async image attachment -> data URL.
        url_converter: Async image URL -> data URL (for embed previews).
        transcriber: Async audio attachment -> transcript text (or ``None``).
        url_content_fetcher: Optional async ``(text, *, cache_only) -> (extra_text, image_parts)``.
            When provided (models without tools), user messages are enriched with fetched
            URL content; when ``None`` the enrichment is skipped. Only
            the last ``FRESH_FETCH_RECENT_MESSAGES`` messages may fetch.
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_MESSAGES)
    fresh_from = len(messages) - FRESH_FETCH_RECENT_MESSAGES

    async def convert(index: int, msg: discord.Message) -> List[Dict[str, Any]]:
        async with semaphore:
            return await _convert_message(
                msg, messages, bot_id,
                attachment_converter=attachment_converter,
                url_converter=url_converter,
                transcriber=transcriber,
                url_content_fetcher=url_content_fetcher,
                cache_only=index < fresh_from,
            )

    converted = await asyncio.gather(*(convert(i, m) for i, m in enumerate(messages)))
    # Tag each entry with its Discord message so services can tell which came after the mention.
    return [
        {**entry, "message_id": msg.id}
        for msg, entries in zip(messages, converted, strict=True)
        for entry in entries
    ]


async def _convert_message(
    msg: discord.Message,
    messages: List[discord.Message],
    bot_id: int,
    *,
    attachment_converter: Callable[[discord.Attachment], Awaitable[str]],
    url_converter: Callable[[str], Awaitable[str]],
    transcriber: Callable[[discord.Attachment], Awaitable[Optional[str]]],
    url_content_fetcher: Optional[Callable[..., Awaitable[tuple[str, List[Dict[str, Any]]]]]],
    cache_only: bool,
) -> List[Dict[str, Any]]:
    """Turn one Discord message into one or two chat-completion entries."""
    chat_context: List[Dict[str, Any]] = []
    role = "assistant" if msg.author.id == bot_id else "user"

    # Start with the text component, prefixed with timestamp, message ID
    # and Discord ID so the bot knows who is speaking and can map replies.
    text_lines = []
    timestamp_str = format_discord_timestamp(msg.created_at)
    text_lines.append(f"[{timestamp_str}] [{msg.id}] <@{msg.author.id}>:")

    if msg.content:
        # Strip the cost prefix from the bot's own replies so the model doesn't parrot it.
        content = COST_PREFIX_PATTERN.sub("", msg.content) if role == "assistant" else msg.content
        text_lines.append(content)
    elif not msg.embeds and not msg.attachments:
        # Edge case: a message with no content, embed, or attachment
        text_lines.append("[Empty Message]")

    # Note any replies
    quotes_bot = False
    if msg.reference and msg.reference.message_id:
        reply_text = f"[Replying to message ID: {msg.reference.message_id}]"

        # Try to extract the original message text via resolved message or history buffer
        ref_msg = getattr(msg.reference, 'resolved', None)
        if ref_msg is None:
            ref_msg = getattr(msg.reference, 'cached_message', None)

        ref_text = None
        ref_timestamp = None
        ref_author_id = None
        if isinstance(ref_msg, discord.Message) and ref_msg.content:
            ref_text = ref_msg.content
            ref_timestamp = format_discord_timestamp(ref_msg.created_at)
            ref_author_id = ref_msg.author.id
        else:
            for h_msg in messages:
                if h_msg.id == msg.reference.message_id:
                    ref_text = h_msg.content
                    ref_timestamp = format_discord_timestamp(h_msg.created_at)
                    ref_author_id = h_msg.author.id
                    break

        if ref_text is not None:
            # A quoted bot reply starts with its cost, which the model shouldn't see or copy.
            ref_text = COST_PREFIX_PATTERN.sub("", ref_text)

        if ref_text is not None and ref_timestamp and ref_author_id:
            reply_text = f"[Replying to message: \"[{ref_timestamp}] [{msg.reference.message_id}] <@{ref_author_id}>: {ref_text}\"]"
        elif ref_text is not None:
            reply_text = f"[Replying to message: \"{ref_text}\"]"

        text_lines.insert(0, reply_text + "\n\n")
        quotes_bot = ref_author_id == bot_id

    # Note single-text representations for embeds
    if msg.embeds:
        embeds_info = []
        for e in msg.embeds:
            embed_text = []
            if e.title:
                embed_text.append(f"Title: {e.title}")
            if e.description:
                embed_text.append(f"Description: {e.description}")
            if e.url:
                embed_text.append(f"URL: {e.url}")
            if embed_text:
                embeds_info.append(" | ".join(embed_text))

        if embeds_info:
            text_lines.append("\n[Embeds:\n- " + "\n- ".join(embeds_info) + "\n]")

    # Compile the entire text block
    final_text = " ".join(text_lines).strip()


    text_payload = [{"type": "text", "text": final_text}]
    file_payloads = []

    # Fetch all supported URLs automatically (models without tools only). Not the links in a
    # quoted reply of the bot's: those are often from logs or quotes its tools returned.
    if role == "user" and url_content_fetcher is not None:
        fetch_text = " ".join(text_lines[1:]).strip() if quotes_bot else final_text
        url_content, url_images = await url_content_fetcher(fetch_text, cache_only=cache_only)
        if url_content:
            final_text = url_content + final_text
            text_payload[0]["text"] = final_text
        if url_images:
            file_payloads.extend(url_images)

    # Image and file attachments
    for attach in msg.attachments:
        try:
            is_image = attach.content_type and attach.content_type.startswith('image/')
            if is_image:
                # The bot's own images too (what its draw tool attached): without them the model
                # sees its description of a picture and no picture, and decides it never drew one.
                file_data_url = await attachment_converter(attach)
                file_payloads.append(
                    {"type": "image_url", "image_url": {"url": file_data_url}}
                )
            elif role == "user":
                is_audio = (attach.content_type and attach.content_type.startswith('audio/')) or is_voice_message(attach)

                if is_audio:
                    # Audio goes in as a transcript.
                    transcript = await transcriber(attach)
                    duration_str = f" ({attach.duration}s)" if getattr(attach, "duration", None) else ""
                    if transcript:
                        text_payload[0]["text"] += f"\n[Sent an audio message/voice clip{duration_str}: {attach.filename}\nTranscript: {transcript}]"
                    else:
                        text_payload[0]["text"] += f"\n[Sent an audio message/voice clip{duration_str}: {attach.filename}\n(Transcription failed or unavailable)]"
                else:
                    text_payload[0]["text"] += f"\n[Attached file: {attach.filename}]"
        except Exception as e:
            logger.error("Failed to convert attachment context for msg %s: %s", msg.id, e, exc_info=True)

    # Embed image previews
    for embed in msg.embeds:
        logger.debug("Checking embed for image previews: %s", embed.title)
        # Prefer Discord's media proxy: it's always reachable, already
        # resized, and never points at an address of the poster's choosing.
        embed_url = None
        if embed.image and (embed.image.proxy_url or embed.image.url):
            embed_url = embed.image.proxy_url or embed.image.url
        elif embed.thumbnail and (embed.thumbnail.proxy_url or embed.thumbnail.url):
            embed_url = embed.thumbnail.proxy_url or embed.thumbnail.url

        if embed_url:
            try:
                logger.debug("Fetching embed preview image from: %s", embed_url)
                image_data_url = await url_converter(embed_url)
                file_payloads.append(
                    {"type": "image_url", "image_url": {"url": image_data_url}}
                )
            except Exception as ex:
                logger.warning("Failed to fetch embed preview context for msg %s: %s", msg.id, ex)

    # Assistant turns can't carry images, so an assistant message with images goes
    # in as its text, then the images in a follow-up user turn.
    if role == "assistant" and file_payloads:
        chat_context.append({"role": "assistant", "content": text_payload})
        chat_context.append({
            "role": "user",
            "content": [{"type": "text", "text": f"[{timestamp_str}] [{msg.id}] <@{msg.author.id}>:"}] + file_payloads
        })
    else:
        chat_context.append({"role": role, "content": text_payload + file_payloads})

    return chat_context
