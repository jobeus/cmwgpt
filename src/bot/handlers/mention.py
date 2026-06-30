"""
Mention Handler - Handles bot mentions and context preparation
"""

import logging
import time
from typing import Any, Awaitable, Callable, Dict, List

import discord

from src.utils.discord_helper import get_mention_legend, attachment_to_base64_data_url, url_to_base64_data_url, transcribe_audio_attachment
from src.services.openai_service import OpenAIServiceError
from src.services.gemini_service import GeminiServiceError, is_gemini_model
from src.services.completion_dispatch import dispatch_completion, HybridConfig
from src.utils.chat_context import build_chat_context
from src.utils.downloader_utils import fetch_all_url_content
from src.config import load_system_prompt

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
        gemini_service: Any = None,
        mention_legend_provider: Callable[[discord.TextChannel, discord.User], Awaitable[str]] = get_mention_legend,
        attachment_converter: Callable[[discord.Attachment], Awaitable[str]] = attachment_to_base64_data_url,
        url_converter: Callable[[str], Awaitable[str]] = url_to_base64_data_url,
        url_content_fetcher: Callable[[str], Awaitable[tuple[str, List[Dict[str, Any]]]]] = fetch_all_url_content,
    ):
        self._state_service = state_service
        self._openai_service = openai_service
        self._gemini_service = gemini_service
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

                recent_messages, system_prompt = await self._prepare_mention_context(message, bot_user, model=model)
                logger.info(
                    f"Context prepared for mention by {message.author}, sending to {'Gemini' if is_gemini_model(model) else 'OpenRouter'}...")
                channel_id = message.channel.id

                # Identity threaded into every model call for this mention.
                identity = {"channel_id": channel_id, "discord_user_id": message.author.id}

                hybrid_cfg = None
                if model == "hybrid":
                    hybrid_summary_prompt = (
                        f"\n\nYou are a chat CONTEXT GATHERER only, you are not participating in the chat:\n"
                        f"Do not reply directly to the user. Instead, review the entire chat buffer provided. "
                        f"You must summarize all the available information, perform any web searches needed to enrich the context, "
                        f"and extract the main points or questions.\n"
                        f"CRITICAL: Be sure to include the <@Discord_User_IDs> of the participants in your summary so the final responder knows exactly who said what, and clearly point out who is asking what question or mentioning the bot at the very end of the chat log.\n"
                        f"CRITICAL: When referring to the bot in your summary, speak directly to the final responder as 'YOU' (e.g. 'User <@ID> asked YOU a question'). Do NOT refer to the bot in the third person (like '<@{bot_user.id}>' or 'the bot') so the final responder doesn't get confused.\n\n"
                        f"CRITICAL: Explain what YOU, i.e. '<@{bot_id}>' has already responded to so we don't get 3 replies in a row about the same answer. You can mention you've responded to X Y Z before for context but don't imply we need another answer to the same thing!"
                        f"CRITICAL: DO NOT MAKE THINGS UP, IF YOU CAN'T SEE CONTENT OR A VIDEO OR DON'T KNOW SOMETHING, SAY SO. No hallucinating allowed!!!\n\n"
                        f"CRITICAL: Include relevant times and dates!\n\n"
                        f"Provide a *detailed*, unfiltered, comprehensive briefing of information from the channel conversation, any relevant urls or summaries (summarized by you), "
                        f"search results you found to do with the conversation + mention, and who asked what (YOU ARE *NOT* REPLYING IN THE CHANNEL -- you are SUMMARIZING THE CONVERSATION TO ANOTHER AI AGENT). "
                        f"Another AI model will use this briefing to write the final response."
                    )
                    hybrid_cfg = HybridConfig(
                        summary_prompt=hybrid_summary_prompt,
                        phase2_system_prompt=load_system_prompt(prompt_path="system_prompt_hybrid.txt"),
                        build_phase2_text=lambda s: f"Here is the context and gathered search results for the current conversation. Please use this information to write the final response to the channel. Remember your personality from the system prompt. CRITICAL: You ARE the bot in this conversation. If the summary refers to YOU, the bot, or <@{bot_user.id}>, it is referring to YOU. Do not refer to yourself in the third person.\n\nContext & Search Results from Chat Summarization Agent:\n{s}",
                    )

                reply_content, cost = await dispatch_completion(
                    model=model,
                    messages=recent_messages,
                    system_prompt=system_prompt,
                    openai_service=self._openai_service,
                    gemini_service=self._gemini_service,
                    identity=identity,
                    hybrid=hybrid_cfg,
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
                else:
                    # Regular text response
                    reply_text = str(reply_content)
                    files_to_upload = []

                if cost > 0:
                    reply_text = f"[${cost:.3f}] {reply_text}"

                if files_to_upload:
                    await self._message_service.send_channel_reply_with_files(message.channel, reply_text, files_to_upload)
                else:
                    await self._message_service.send_channel_reply(message.channel, reply_text)

            except (OpenAIServiceError, GeminiServiceError) as e:
                logger.error(f"❌ API error in mention handler:\\n{e}")
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
        self, message: discord.Message, bot_user: discord.User, model: str = None
    ) -> tuple[List[Dict[str, Any]], str]:
        """
        Prepares the message list and system prompt for OpenAI context in case of a mention.

        Args:
            message: The Discord message containing the mention
            bot_user: The bot user object
            model: The model to use (helps determine history size)

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

        limit_lines = self._include_num_chatlines

        if cache_entry and (current_time - cache_entry["timestamp"]) <= 600:
            oldest_message = cache_entry["oldest_message"]
            history_msgs.append(oldest_message)
            
            # Fetch up to 40 messages after the oldest message
            async for msg in message.channel.history(limit=40, after=oldest_message):
                history_msgs.append(msg)

            if len(history_msgs) >= 40:
                # The gap is too large (40+ messages since last bot interaction).
                # To save cost and avoid giant context windows, we abandon the old
                # interaction and start a fresh context from the most recent messages.
                logger.info(f"Massive message gap detected in #{message.channel.name}. Resetting context.")
                history_msgs = []
                if self._gemini_service:
                    self._gemini_service.clear_channel_cache(channel_id)
                    
                async for msg in message.channel.history(limit=limit_lines):
                    history_msgs.append(msg)
                history_msgs.reverse()
                
                if history_msgs:
                    self._history_cache[channel_id] = {
                        "timestamp": current_time,
                        "oldest_message": history_msgs[0]
                    }
            else:
                # Update timestamp for this channel
                self._history_cache[channel_id]["timestamp"] = current_time
        else:
            # History window reset — also clear Gemini conversation cache
            if self._gemini_service:
                self._gemini_service.clear_channel_cache(channel_id)

            async for msg in message.channel.history(limit=limit_lines):
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

        # Build the native multimodal messages array. The mention path enriches
        # user messages with fetched URL content via the injected fetcher.
        chat_context = await build_chat_context(
            history_msgs,
            bot_user.id,
            attachment_converter=self._attachment_converter,
            url_converter=self._url_converter,
            transcriber=transcribe_audio_attachment,
            url_content_fetcher=self._url_content_fetcher,
        )

        return chat_context, current_channel_system_prompt
