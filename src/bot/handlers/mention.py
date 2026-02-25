"""
Mention Handler - Handles bot mentions and context preparation
"""

import json
import logging
from typing import List, Dict, Any

import discord

from src.config import get_system_prompt, INCLUDE_NUM_CHATLINES
from src.services.state_service import state_service
from src.utils.discord_helper import get_mention_legend
from src.services.openai_service import openai_service, OpenAIServiceError
from src.services.message_service import message_service
from src.services.queue_service import queue_service

logger = logging.getLogger(__name__)


class MentionHandler:
    """Handles bot mentions and context preparation."""

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
                        message.author}, sending to OpenAI...")
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
        async for msg in message.channel.history(limit=INCLUDE_NUM_CHATLINES):
            history_msgs.append(msg)
        history_msgs.reverse()  # oldest first

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
            f"Here are the last {INCLUDE_NUM_CHATLINES} messages from the channel in JSON format. "
            f"You can read all of these messages. You've been mentioned in the very last "
            f"message in the JSON array (but you may have been asked things before, and "
            f"answered things before, that's ok! just respond to the very last element in the JSON array "
            f"please, you'll notice you were @mentioned by someone saying  <@{bot_user.id}>. "
            f"You are expected to reply, but less "
            f"metaphysics and more straight up answers like a user on a 30 year old IRC "
            f"board and not a talkative robot. Respond with only your the content of your reply.\n\n"
            f"{legend_section}\n\n"
        )

        # Prepare conversation context
        ask_preamble = (
            "Conversation lines are below and represent the last "
            f"{INCLUDE_NUM_CHATLINES} chat lines in the chat in order from oldest to newest. "
            f"The newest one at the bottom mentions you "
            f"but feel free to read all the context provided, then answer the very last line in the "
            f"following array ONLY. Each line of history is provided in a json array format like this: "
            "{ 'user':'<@ID>', 'says': '<content of message>' }"
        )

        # No system prompt in chat_context - will be passed separately
        chat_context = []

        # Build chat history
        chat_history = []
        for msg in history_msgs:
            chat_history.append(
                {"user": f"<@{msg.author.id}>", "says": msg.content})

        chat_context.append(
            {"role": "user", "content": ask_preamble + "\n\n" + json.dumps(chat_history)})

        return chat_context, current_channel_system_prompt


# Global handler instance
mention_handler = MentionHandler()
