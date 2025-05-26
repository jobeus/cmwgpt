"""
Mention Handler - Handles bot mentions and context preparation
"""

import json
import logging
from typing import List, Dict, Any

import discord

from src.config import SYSTEM_PROMPT, INCLUDE_NUM_CHATLINES
from src.bot_state import channel_system_prompts
from src.utils.discord_helper import get_mention_legend
from src.services.openai_service import openai_service
from src.services.message_service import message_service


logger = logging.getLogger(__name__)


class MentionHandler:
    """Handles bot mentions and context preparation."""

    async def handle_mention(self, message: discord.Message, bot_user: discord.User, model: str) -> None:
        """
        Handle a bot mention by preparing context and sending a reply.

        Args:
            message: The Discord message containing the mention
            bot_user: The bot user object
            model: The model to use for the response
        """
        async with message.channel.typing():
            chat_msgs = await self._prepare_mention_context(message, bot_user)
            reply_content = openai_service.get_chat_completion(model=model, messages=chat_msgs)
            await message_service.send_channel_reply(message.channel, reply_content)

    async def _prepare_mention_context(self, message: discord.Message, bot_user: discord.User) -> List[Dict[str, Any]]:
        """
        Prepares the message list for OpenAI context in case of a mention.

        Args:
            message: The Discord message containing the mention
            bot_user: The bot user object

        Returns:
            List of message dictionaries for OpenAI API
        """
        logger.info(
            f"Mention by {
                message.author} in #{
                message.channel}: {
                message.content}"
        )

        # Gather message history
        history_msgs = []
        async for msg in message.channel.history(limit=INCLUDE_NUM_CHATLINES):
            history_msgs.append(msg)
        history_msgs.reverse()  # oldest first

        # Get user legend for the channel
        legend_section = await get_mention_legend(message.channel)

        # Prepare system prompt
        current_channel_system_prompt = channel_system_prompts.get(message.channel.id, SYSTEM_PROMPT)
        current_channel_system_prompt += (
            f"In the channel your ID is: <@{bot_user.id}> and included are the last "
            f"{INCLUDE_NUM_CHATLINES} messages from the channel in JSON format. "
            f"You can read all of these messages. You've been mentioned in the very last "
            f"message in the JSON array (but you may have been asked things before, and "
            f"answered things before, that's ok! just respond to the LAST thing asked or "
            f"@mentioned to you though please. You are expected to reply, but less "
            f"metaphysics and more straight up answers like a user on a 30 year old IRC "
            f"board and not a talkative robot. Respond with only your the content of your reply.\n\n"
            f"{legend_section}\n\n"
        )

        # Prepare conversation context
        ask_preamble = (
            "Conversation lines are below and represent the last "
            f"{INCLUDE_NUM_CHATLINES} chat lines in the chat. The last one mentions you "
            f"but feel free to read all the context, then answer the very last line in the "
            f"following array ONLY. History is provided in this json array format with "
            f"{{ 'user':'<id>', 'says': '<content of message>'}}:"
        )

        chat_context = [{"role": "system", "content": current_channel_system_prompt}]

        # Build chat history
        chat_history = []
        for msg in history_msgs:
            chat_history.append({"user": f"<@{msg.author.id}>", "says": msg.content})

        chat_context.append({"role": "user", "content": ask_preamble + "\n\n" + json.dumps(chat_history)})

        return chat_context


# Global handler instance
mention_handler = MentionHandler()
