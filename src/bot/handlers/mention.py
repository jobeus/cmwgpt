"""
Mention Handler - Handles bot mentions and context preparation
"""

import logging
import time
from typing import List, Dict, Any

import discord

from src.config import get_system_prompt, INCLUDE_NUM_CHATLINES
from src.services.state_service import state_service
from src.utils.discord_helper import get_mention_legend, attachment_to_base64_data_url, url_to_base64_data_url
from src.services.openai_service import openai_service, OpenAIServiceError
from src.services.message_service import message_service
from src.services.queue_service import queue_service

logger = logging.getLogger(__name__)


class MentionHandler:
    """Handles bot mentions and context preparation."""

    def __init__(self):
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
                state_service.mark_channel_active(message.channel.id)

                chat_msgs, system_prompt = await self._prepare_mention_context(message, bot_user)
                logger.info(
                    f"Context prepared for mention by {
                        message.author}, sending to OpenRouter...")
                reply_content = await openai_service.get_chat_completion(
                    model=model, messages=chat_msgs, system_prompt=system_prompt
                )

                # Handle different response formats
                if isinstance(reply_content, dict) and "text" in reply_content:
                    # Response includes files (from image generation or other
                    # tools)
                    reply_text = reply_content["text"]
                    files_to_upload = reply_content.get("files", [])

                    if files_to_upload:
                        await message_service.send_channel_reply_with_files(message.channel, reply_text, files_to_upload)
                    else:
                        await message_service.send_channel_reply(message.channel, reply_text)
                else:
                    # Regular text response
                    await message_service.send_channel_reply(message.channel, reply_content)

            except OpenAIServiceError as e:
                logger.error(f"OpenAI API error in mention handler: {e}")
                error_message = f"""Sorry, I encountered an error while processing your mention: {
                    str(e)}"""

                try:
                    await message_service.send_channel_reply(message.channel, error_message)
                except Exception as discord_error:
                    logger.error(
                        f"Failed to send error message to Discord: {discord_error}")
                    # Try to send a simpler error message
                    try:
                        await message.channel.send(
                            "Sorry, I encountered an error and couldn't respond to your mention. Please try again later."
                        )
                    except Exception:
                        logger.error("Failed to send fallback error message")

            except Exception as e:
                logger.error(f"Unexpected error in mention handler: {e}")
                error_message = (
                    "Sorry, I encountered an unexpected error while processing your mention. Please try again later."
                )

                try:
                    await message_service.send_channel_reply(message.channel, error_message)
                except Exception as discord_error:
                    logger.error(
                        f"Failed to send error message to Discord: {discord_error}")
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
        return await queue_service.queue_mention(
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
            f"""Mention by {
                message.author} in #{
                message.channel}: {
                message.content}"""
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
            async for msg in message.channel.history(limit=INCLUDE_NUM_CHATLINES):
                history_msgs.append(msg)
            history_msgs.reverse()

            if history_msgs:
                self._history_cache[channel_id] = {
                    "timestamp": current_time,
                    "oldest_message": history_msgs[0]
                }

        # Get user legend for the channel
        legend_section = await get_mention_legend(message.channel, bot_user)

        # Prepare system prompt
        channel_system_prompt = state_service.get_system_prompt(
            message.channel.id)
        if channel_system_prompt:
            current_channel_system_prompt = channel_system_prompt
        else:
            current_channel_system_prompt = get_system_prompt()

        current_channel_system_prompt += (
            f"In the channel you are <@{bot_user.id}>!\n\n"
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
            timestamp_str = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
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
                text_lines.append(
                    f"[Replying to message ID: {
                        msg.reference.message_id}]")

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
            # No need for fallback handling here since we always prepend the
            # sender prefix above

            text_payload = [{"type": "text", "text": final_text}]
            file_payloads = []

            # 2. Add native image and file components
            for attach in msg.attachments:
                try:
                    # We only convert attachments for user messages
                    if role == "user":
                        file_data_url = await attachment_to_base64_data_url(attach)
                        if attach.content_type and attach.content_type.startswith(
                                'image/'):
                            file_payloads.append(
                                {"type": "image_url", "image_url": {"url": file_data_url}}
                            )
                        else:
                            file_payloads.append(
                                {"type": "file", "file": {"url": file_data_url}}
                            )
                except Exception as e:
                    logger.error(
                        f"Failed to convert attachment context for msg {
                            msg.id}: {e}")
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
                        image_data_url = await url_to_base64_data_url(embed_url)
                        file_payloads.append(
                            {"type": "image_url", "image_url": {"url": image_data_url}}
                        )
                    except Exception as ex:
                        logger.warning(
                            f"Failed to fetch embed preview context for msg {
                                msg.id}: {ex}")

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


# Global handler instance
mention_handler = MentionHandler()
