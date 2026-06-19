"""Shared multimodal chat-context builder for Discord message history.

Both the mention handler and the interject service turn a list of
``discord.Message`` objects into the native multimodal ``messages`` array
expected by the chat-completion services (text prefixed with timestamp/ID/sender,
reply resolution, embed text, image attachments, audio transcription, embed
image previews, and assistant-image splitting). This module is the single
source of truth for that transformation.
"""

import logging
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional

import discord

from src.utils.message_utils import format_discord_timestamp

logger = logging.getLogger(__name__)

# Pattern to strip cost prefixes like [$0.011] or [$0.005 @ z-image] from the
# start of bot messages so the model doesn't parrot them back.
COST_PREFIX_PATTERN = re.compile(r'^\[\$[\d.]+(?:\s*@\s*[^\]]+)?\]\s*')


async def build_chat_context(
    messages: List[discord.Message],
    bot_id: int,
    *,
    attachment_converter: Callable[[discord.Attachment], Awaitable[str]],
    url_converter: Callable[[str], Awaitable[str]],
    transcriber: Callable[[discord.Attachment], Awaitable[Optional[str]]],
    url_content_fetcher: Optional[
        Callable[[str], Awaitable[tuple[str, List[Dict[str, Any]]]]]
    ] = None,
) -> List[Dict[str, Any]]:
    """Convert Discord message history into a multimodal chat-completion array.

    Args:
        messages: Discord messages, oldest first.
        bot_id: The bot's user ID; its messages become the ``assistant`` role.
        attachment_converter: Async image attachment -> data URL.
        url_converter: Async image URL -> data URL (for embed previews).
        transcriber: Async audio attachment -> transcript text (or ``None``).
        url_content_fetcher: Optional async ``text -> (extra_text, image_parts)``.
            When provided, user messages are enriched with fetched URL content
            (the mention path); when ``None`` the enrichment is skipped (the
            interject path).
    """
    chat_context: List[Dict[str, Any]] = []

    for msg in messages:
        # Determine role
        role = "assistant" if msg.author.id == bot_id else "user"

        # 1. Start with the text component, prefixed with timestamp, message ID
        # and Discord ID so the bot knows who is speaking and can map replies.
        text_lines = []
        timestamp_str = format_discord_timestamp(msg.created_at)
        text_lines.append(f"[{timestamp_str}] [{msg.id}] <@{msg.author.id}>:")

        if msg.content:
            text_lines.append(msg.content)
        elif not msg.embeds and not msg.attachments:
            # Edge case: a message with no content, embed, or attachment
            text_lines.append("[Empty Message]")

        # Note any replies
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

            if ref_text is not None and ref_timestamp and ref_author_id:
                reply_text = f"[Replying to message: \"[{ref_timestamp}] [{msg.reference.message_id}] <@{ref_author_id}>: {ref_text}\"]"
            elif ref_text is not None:
                reply_text = f"[Replying to message: \"{ref_text}\"]"

            text_lines.insert(0, reply_text + "\n\n")

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

        # Strip cost prefixes from bot messages so the model doesn't parrot them
        if role == "assistant":
            final_text = COST_PREFIX_PATTERN.sub("", final_text)

        text_payload = [{"type": "text", "text": final_text}]
        file_payloads = []

        # Fetch all supported URLs automatically (mention path only)
        if role == "user" and url_content_fetcher is not None:
            url_content, url_images = await url_content_fetcher(final_text)
            if url_content:
                final_text = url_content + final_text
                text_payload[0]["text"] = final_text
            if url_images:
                file_payloads.extend(url_images)

        # 2. Add native image and file components
        for attach in msg.attachments:
            try:
                # We only convert attachments for user messages
                if role == "user":
                    is_image = attach.content_type and attach.content_type.startswith('image/')

                    is_voice_attr = getattr(attach, "is_voice_message", None)
                    is_voice = False
                    if is_voice_attr:
                        if callable(is_voice_attr):
                            is_voice = is_voice_attr()
                        else:
                            is_voice = bool(is_voice_attr)

                    is_audio = (attach.content_type and attach.content_type.startswith('audio/')) or is_voice

                    if is_image:
                        file_data_url = await attachment_converter(attach)
                        file_payloads.append(
                            {"type": "image_url", "image_url": {"url": file_data_url}}
                        )
                    elif is_audio:
                        # Send audio to Groq instead and get a transcript
                        transcript = await transcriber(attach)
                        duration_str = f" ({attach.duration}s)" if getattr(attach, "duration", None) else ""
                        if transcript:
                            text_payload[0]["text"] += f"\n[Sent an audio message/voice clip{duration_str}: {attach.filename}\nTranscript: {transcript}]"
                        else:
                            text_payload[0]["text"] += f"\n[Sent an audio message/voice clip{duration_str}: {attach.filename}\n(Transcription failed or unavailable)]"
                    else:
                        text_payload[0]["text"] += f"\n[Attached file: {attach.filename}]"
            except Exception as e:
                logger.error(f"Failed to convert attachment context for msg {msg.id}: {e}")

        # 3. Add native embed image previews
        for e in msg.embeds:
            logger.debug(f"Checking embed for image previews: {e.title}")
            embed_url = None
            if e.image and e.image.url:
                embed_url = e.image.url
            elif e.thumbnail and e.thumbnail.url:
                embed_url = e.thumbnail.url

            if embed_url:
                try:
                    logger.debug(f"Fetching embed preview image from: {embed_url}")
                    image_data_url = await url_converter(embed_url)
                    file_payloads.append(
                        {"type": "image_url", "image_url": {"url": image_data_url}}
                    )
                except Exception as ex:
                    logger.warning(f"Failed to fetch embed preview context for msg {msg.id}: {ex}")

        # Chat completions API doesn't support image_url parts in the 'assistant'
        # role natively. So if it's an assistant message with images, send text
        # as assistant and images as a follow-up 'user'.
        if role == "assistant" and file_payloads:
            chat_context.append({"role": "assistant", "content": text_payload})
            chat_context.append({
                "role": "user",
                "content": [{"type": "text", "text": f"[{timestamp_str}] [{msg.id}] <@{msg.author.id}>:"}] + file_payloads
            })
        else:
            chat_context.append({"role": role, "content": text_payload + file_payloads})

    return chat_context
