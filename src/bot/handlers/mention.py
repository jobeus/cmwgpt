"""
Mention Handler - Handles bot mentions and context preparation
"""

import logging
import re
import time
from typing import Any, Awaitable, Callable, Dict, List

import discord

from src.utils.discord_helper import get_mention_legend, attachment_to_base64_data_url, url_to_base64_data_url
from src.services.openai_service import OpenAIServiceError
from src.utils.downloader_utils import fetch_all_url_content
from src.utils.message_utils import format_discord_timestamp

# Pattern to strip cost prefixes like [$0.011] or [$0.005 @ z-image] from the start of bot messages
COST_PREFIX_PATTERN = re.compile(r'^\[\$[\d.]+(?:\s*@\s*[^\]]+)?\]\s*')

logger = logging.getLogger(__name__)


class MentionHandler:
    """Handles bot mentions and context preparation."""

    def __init__(
        self,
        *,
        state_service: Any,
        openai_service: Any,
        message_service: Any,
        queue_service: Any,
        system_prompt_loader: Callable[[], str],
        include_num_chatlines: int,
        mention_legend_provider: Callable[[discord.TextChannel, discord.User], Awaitable[str]] = get_mention_legend,
        attachment_converter: Callable[[discord.Attachment], Awaitable[str]] = attachment_to_base64_data_url,
        url_converter: Callable[[str], Awaitable[str]] = url_to_base64_data_url,
        url_content_fetcher: Callable[[str], Awaitable[str]] = fetch_all_url_content,
    ):
        self._state_service = state_service
        self._openai_service = openai_service
        self._message_service = message_service
        self._queue_service = queue_service
        self._system_prompt_loader = system_prompt_loader
        self._include_num_chatlines = include_num_chatlines
        self._mention_legend_provider = mention_legend_provider
        self._attachment_converter = attachment_converter
        self._url_converter = url_converter
        self._url_content_fetcher = url_content_fetcher
        # Cache for history fetching optimization
        # channel_id -> {"timestamp": float, "oldest_message": discord.Message}
        self._history_cache = {}

    async def handle_mention(
            self,
            message: discord.Message,
            bot_user: discord.User,
            model: str) -> None:
        """
        Handle a bot mention by preparing context and sending a reply.

        This method processes the mention immediately without queuing.
        For queued processing, use queue_mention() instead.

        Args:
            message: The Discord message containing the mention
            bot_user: The bot user object
            model: The model to use for the response
        """
        async with message.channel.typing():
            try:
                # Mark channel as active
                self._state_service.mark_channel_active(message.channel.id)

                recent_messages, system_prompt = await self._prepare_mention_context(message, bot_user)
                logger.info(
                    f"Context prepared for mention by {message.author}, sending to OpenRouter...")
                channel_id = message.channel.id
                reply_content = await self._openai_service.get_chat_completion(
                    model=model,
                    messages=recent_messages,
                    system_prompt=system_prompt,
                    channel_id=channel_id,
                    discord_user_id=message.author.id,
                )

                if reply_content is None:
                    logger.error(f"❌ Received None from get_chat_completion for model {model}.")
                    await self._message_service.send_channel_reply(
                        message.channel,
                        f"I'm sorry, I failed to get a response from the model '{model}'. It returned an empty result."
                    )
                    return

                # Handle different response formats
                if isinstance(reply_content, dict) and "text" in reply_content:
                    # Response includes files (from image generation or other
                    # tools)
                    reply_text = reply_content["text"]
                    files_to_upload = reply_content.get("files", [])

                    if files_to_upload:
                        await self._message_service.send_channel_reply_with_files(message.channel, reply_text, files_to_upload)
                    else:
                        await self._message_service.send_channel_reply(message.channel, reply_text)
                else:
                    # Regular text response
                    await self._message_service.send_channel_reply(message.channel, reply_content)

            except OpenAIServiceError as e:
                logger.error(f"❌ OpenAI API error in mention handler:\\n{e}")
                error_message = f"Sorry, I encountered an error while processing your mention: {str(e)}"

                try:
                    await self._message_service.send_channel_reply(message.channel, error_message)
                except Exception as discord_error:
                    logger.error(f"⚠️ Failed to send error message to Discord: {discord_error}")
                    # Try to send a simpler error message
                    try:
                        await message.channel.send(
                            "Sorry, I encountered an error and couldn't respond to your mention. Please try again later."
                        )
                    except Exception:
                        logger.error("Failed to send fallback error message")

            except Exception:
                logger.exception("🚨 Unexpected error in mention handler")
                error_message = (
                    "Sorry, I encountered an unexpected error while processing your mention. Please try again later."
                )

                try:
                    await self._message_service.send_channel_reply(message.channel, error_message)
                except Exception as discord_error:
                    logger.error(f"⚠️ Failed to send error message to Discord: {discord_error}")
                    # Try to send a simpler error message
                    try:
                        await message.channel.send(
                            "Sorry, I encountered an error and couldn't respond to your mention. Please try again later."
                        )
                    except Exception:
                        logger.error("Failed to send fallback error message")

    async def queue_mention(
            self,
            message: discord.Message,
            bot_user: discord.User,
            model: str) -> bool:
        """
        Queue a bot mention for FIFO processing.

        This method adds the mention to a queue for sequential processing,
        ensuring no race conditions between concurrent mentions.

        Args:
            message: The Discord message containing the mention
            bot_user: The bot user object
            model: The model to use for the response

        Returns:
            True if the mention was successfully queued, False if queue is full
        """
        return await self._queue_service.queue_mention(
            message=message, bot_user=bot_user, model=model, handler=self.handle_mention
        )

    async def _prepare_mention_context(
        self, message: discord.Message, bot_user: discord.User
    ) -> tuple[List[Dict[str, Any]], str]:
        """
        Prepares the message list and system prompt for OpenAI context in case of a mention.

        Args:
            message: The Discord message containing the mention
            bot_user: The bot user object

        Returns:
            Tuple of (message list for OpenAI API, system prompt string)
        """
        logger.info(
            f"Mention by {message.author} in #{message.channel}: {message.content}"
        )

        # Gather message history
        history_msgs = []

        current_time = time.time()
        channel_id = message.channel.id
        cache_entry = self._history_cache.get(channel_id)

        if cache_entry and (current_time - cache_entry["timestamp"]) <= 600:
            oldest_message = cache_entry["oldest_message"]
            history_msgs.append(oldest_message)
            async for msg in message.channel.history(limit=None, after=oldest_message):
                history_msgs.append(msg)

            # Update timestamp for this channel
            self._history_cache[channel_id]["timestamp"] = current_time
        else:
            async for msg in message.channel.history(limit=self._include_num_chatlines):
                history_msgs.append(msg)
            history_msgs.reverse()

            if history_msgs:
                self._history_cache[channel_id] = {
                    "timestamp": current_time,
                    "oldest_message": history_msgs[0]
                }

        # Get user legend for the channel
        legend_section = await self._mention_legend_provider(message.channel, bot_user)

        # Prepare system prompt
        channel_system_prompt = self._state_service.get_system_prompt(
            message.channel.id)
        if channel_system_prompt:
            current_channel_system_prompt = channel_system_prompt
        else:
            current_channel_system_prompt = self._system_prompt_loader()

        current_channel_system_prompt += (
            f"\n\nErrata about how to chat in this channel:\n"
            f"In the channel you are <@{bot_user.id}>!\n"
            f"You will receive a chat history containing user messages and your own assistant messages. "
            f"Each message is prefixed with its timestamp, message ID, and the sender's Discord ID (e.g. `[2024-01-01 12:00:00] [123456789] <@12345>: ...`). "
            f"Please respond naturally to the very last message in the conversation, as it mentions you. "
            f"You are expected to reply, but less metaphysics and more straight up answers like a user on a "
            f"30 year old IRC board and not a talkative robot. Respond with ONLY the text/image content of your reply, "
            f"without prefixing it with your own ID or message ID.\n"
            f"CRITICAL INSTRUCTION: DO NOT start your own messages with a `[timestamp] [message ID]` prefix. Just write your text directly as a speaker.\n"
            f"CRITICAL INSTRUCTION: When @mentioning other users in your reply, use THEIR Discord ID from the chat history — never use your own ID <@{bot_user.id}> to mention someone else.\n\n"
            f"{legend_section}\n\n"
        )

        # We will build a native messages array for OpenAI
        chat_context = []

        # Build chat history
        for msg in history_msgs:
            # Determine role
            role = "assistant" if msg.author.id == bot_user.id else "user"

            # 1. Start with the text component
            text_lines = []
            # Prefix with timestamp, message ID and discord ID for ALL messages
            # so the bot knows who is speaking and can map replies
            timestamp_str = format_discord_timestamp(msg.created_at)
            text_lines.append(
                f"[{timestamp_str}] [{msg.id}] <@{msg.author.id}>:")

            # Add message content if any exists
            if msg.content:
                text_lines.append(msg.content)
            elif not msg.embeds and not msg.attachments:
                # Edge case where a message somehow has no content, embed, or
                # attachment
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
                    for h_msg in history_msgs:
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
                    text_lines.append(
                        "\n[Embeds:\n- " + "\n- ".join(embeds_info) + "\n]")

            # Compile the entire text block
            final_text = " ".join(text_lines).strip()

            # Strip cost prefixes from bot messages so the model doesn't parrot them
            if role == "assistant":
                final_text = COST_PREFIX_PATTERN.sub("", final_text)

            # Fetch all supported URLs automatically
            if role == "user":
                url_content = await self._url_content_fetcher(final_text)
                if url_content:
                    final_text = url_content + final_text

            # No need for fallback handling here since we always prepend the
            # sender prefix above

            text_payload = [{"type": "text", "text": final_text}]
            file_payloads = []

            # 2. Add native image and file components
            for attach in msg.attachments:
                try:
                    # We only convert attachments for user messages
                    if role == "user":
                        if attach.content_type and attach.content_type.startswith('image/'):
                            file_data_url = await self._attachment_converter(attach)
                            file_payloads.append(
                                {"type": "image_url", "image_url": {"url": file_data_url}}
                            )
                        else:
                            text_payload[0]["text"] += f"\n[Attached file: {attach.filename}]"
                except Exception as e:
                    logger.error(
                        f"Failed to convert attachment context for msg {msg.id}: {e}")
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
                        logger.debug(
                            f"Fetching embed preview image from: {embed_url}")
                        image_data_url = await self._url_converter(embed_url)
                        file_payloads.append(
                            {"type": "image_url", "image_url": {"url": image_data_url}}
                        )
                    except Exception as ex:
                        logger.warning(
                            f"Failed to fetch embed preview context for msg {msg.id}: {ex}")

            # Chat completions API doesn't support image_url parts in the 'assistant' role natively.
            # So if it's an assistant message with images, send text as
            # assistant and images as a follow-up 'user'.
            if role == "assistant" and file_payloads:
                chat_context.append(
                    {"role": "assistant", "content": text_payload})
                chat_context.append({
                    "role": "user",
                    "content": [{"type": "text", "text": f"[{timestamp_str}] [{msg.id}] <@{msg.author.id}>:"}] + file_payloads
                })
            else:
                chat_context.append(
                    {"role": role, "content": text_payload + file_payloads})

        return chat_context, current_channel_system_prompt
