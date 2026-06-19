"""
Interject Service - Monitors channels and occasionally interjects in conversations.

This service hooks into on_message events and checks whether there's been enough
organic text-only conversation activity to warrant the bot chiming in. It uses
configurable thresholds, cooldowns, and a daily cap to avoid being obnoxious.
"""

import asyncio
import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import discord
from discord.ext import commands

from src.config import get_system_prompt, get_config
from src.services.completion_dispatch import dispatch_completion, HybridConfig
from src.utils.chat_context import build_chat_context
from src.utils.discord_helper import get_mention_legend, attachment_to_base64_data_url, url_to_base64_data_url, transcribe_audio_attachment

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurable constants
# ---------------------------------------------------------------------------

# Minimum number of distinct non-bot authors in the qualifying streak
MIN_UNIQUE_AUTHORS = 2

# Minimum qualifying (text-only, no bot mention) messages in the streak
MIN_MESSAGES = 10

# Only messages within this many minutes from *now* count
ACTIVITY_WINDOW_MINUTES = 15

# Percentage chance (0-100) to interject when conditions are met
INTERJECT_CHANCE_PERCENT = 100  # 100 for testing, reduce after

# Per-channel cooldown in minutes after interjecting or failing the roll
COOLDOWN_MINUTES = 30

# Number of recent messages to include as AI context when generating a reply
CONTEXT_LINES = 20

# Maximum interjections per calendar day (Local Server Time). Resets at midnight.
MAX_INTERJECTIONS_PER_DAY = 10

# If True, messages with embeds or attachments break the streak even if they
# also contain text.
EXCLUDE_EMBEDS = True

# File to persist daily interjection counts
STATE_FILE = ".cache/cmwgpt_interject_counts.json"


class InterjectService:
    """Event-driven service that occasionally interjects in active channels."""

    def __init__(
        self,
        *,
        state_service,
        openai_service,
        message_service,
        system_prompt_loader: Callable[[], str] = get_system_prompt,
        default_model: Optional[str] = None,
        state_file: str = STATE_FILE,
        mention_legend_provider: Callable[[discord.TextChannel, discord.User], Awaitable[str]] = get_mention_legend,
        gemini_service=None,
    ):
        self._state_service = state_service
        self._openai_service = openai_service
        self._gemini_service = gemini_service
        self._message_service = message_service
        self._system_prompt_loader = system_prompt_loader
        self._default_model = get_config().default_model if default_model is None else default_model
        self._state_file = state_file
        self._mention_legend_provider = mention_legend_provider
        self._bot: Optional[commands.Bot] = None
        # channel_id -> timestamp (epoch) when cooldown expires
        self._cooldowns: dict[int, float] = {}
        # Track daily interjections: {"date": "YYYY-MM-DD", "counts": dict[int, int]}
        self._daily_tracker: dict[str, Any] = {"date": "", "counts": {}}
        # Lock to prevent concurrent interjection attempts
        self._lock = asyncio.Lock()
        self._running = False

        # Load state from file if it exists
        self._load_state()

    def set_bot(self, bot: commands.Bot) -> None:
        """Set the Discord bot instance."""
        self._bot = bot

    def start(self) -> None:
        """Mark the service as running."""
        self._running = True
        logger.info("Interject service started")

    def stop(self) -> None:
        """Mark the service as stopped."""
        self._running = False
        logger.info("Interject service stopped")

    # ------------------------------------------------------------------
    # Public entry point — called from client._handle_message()
    # ------------------------------------------------------------------

    async def on_new_message(self, message: discord.Message) -> None:
        """
        Lightweight hook called on every new message. Performs cheap checks
        first and only hits the Discord API when conditions look promising.
        """
        if not self._running or not self._bot or not self._bot.user:
            return

        # --- Cheap bail-outs (no API calls) ---

        # Ignore bots and DMs
        if message.author.bot:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return

        # Ignore messages that mention the bot (those go through the mention handler)
        if self._bot.user in message.mentions:
            return

        bot_id = self._bot.user.id
        channel = message.channel
        channel_id = channel.id

        # Check per-channel cooldown
        if self._is_on_cooldown(channel_id):
            return

        # Check daily cap
        if self._is_daily_cap_reached(channel_id):
            return

        # --- Acquire lock so only one interjection attempt runs at a time ---
        if self._lock.locked():
            return  # Another check is already in progress, skip

        async with self._lock:
            # Re-check after acquiring lock (state may have changed)
            if self._is_on_cooldown(channel_id) or self._is_daily_cap_reached(channel_id):
                return

            try:
                should = await self._check_channel_activity(channel, bot_id)
                if not should:
                    return

                # Roll the dice
                chance = self._get_setting(channel_id, "chance", INTERJECT_CHANCE_PERCENT)
                if not self._roll_chance(chance):
                    logger.debug(
                        f"Interject roll failed for #{channel.name}, applying cooldown"
                    )
                    self._apply_cooldown(channel_id)
                    return

                # Generate and send an interjection
                await self._do_interject(channel, bot_id)

            except Exception as e:
                logger.error(f"Error in interject service for #{channel.name}: {e}")

    # ------------------------------------------------------------------
    # Activity check
    # ------------------------------------------------------------------

    async def _check_channel_activity(
        self, channel: discord.TextChannel, bot_id: int
    ) -> bool:
        """
        Fetch the last MIN_MESSAGES + 1 messages and verify every single one
        qualifies. Any non-qualifying message breaks the streak.

        Returns True if conditions are met for interjection.
        """
        min_messages = self._get_setting(channel.id, "min_messages", MIN_MESSAGES)
        window_mins = self._get_setting(channel.id, "window_mins", ACTIVITY_WINDOW_MINUTES)
        exclude_embeds = self._get_setting(channel.id, "exclude_embeds", EXCLUDE_EMBEDS)
        min_authors = self._get_setting(channel.id, "min_authors", MIN_UNIQUE_AUTHORS)

        fetch_limit = min_messages + 1
        cutoff = time.time() - (window_mins * 60)
        unique_authors: set[int] = set()
        qualifying_count = 0

        messages: list[discord.Message] = []
        async for msg in channel.history(limit=fetch_limit):
            messages.append(msg)

        # Walk newest-to-oldest (history returns newest first)
        for msg in messages:
            # Must be within the activity window
            msg_epoch = msg.created_at.replace(tzinfo=timezone.utc).timestamp()
            if msg_epoch < cutoff:
                return False  # Too old — streak broken

            # Must not be from a bot
            if msg.author.bot:
                return False

            # Must not mention the bot
            if any(u.id == bot_id for u in msg.mentions):
                return False

            # Must not have embeds or attachments (if configured)
            if exclude_embeds and (msg.embeds or msg.attachments):
                return False

            # Must have actual text content
            if not msg.content or not msg.content.strip():
                return False

            unique_authors.add(msg.author.id)
            qualifying_count += 1

        # Need at least MIN_MESSAGES qualifying messages
        if qualifying_count < min_messages:
            return False

        # Need enough unique authors
        if len(unique_authors) < min_authors:
            return False

        return True

    # ------------------------------------------------------------------
    # Interjection generation
    # ------------------------------------------------------------------

    async def _do_interject(
        self, channel: discord.TextChannel, bot_id: int
    ) -> None:
        """Build context from recent messages and send an AI-generated reply."""
        logger.info(f"💬 Interjecting in #{channel.name}")

        # Fetch context messages
        context_lines_cfg = self._get_setting(channel.id, "context_lines", CONTEXT_LINES)

        context_messages: list[discord.Message] = []
        async for msg in channel.history(limit=context_lines_cfg):
            context_messages.append(msg)
        context_messages.reverse()  # Oldest first

        # Build the user legend
        bot_user = self._bot.user
        legend_section = await self._mention_legend_provider(channel, bot_user)

        # Use the default system prompt (or channel override)
        channel_system_prompt = self._state_service.get_system_prompt(channel.id)
        system_prompt = channel_system_prompt or self._system_prompt_loader()

        system_prompt += (
            f"\nIn the channel you are <@{bot_id}>!\n\n"
            f"You will receive a chat history containing user messages and your own assistant messages. "
            f"Each message is prefixed with its timestamp, message ID, and the sender's Discord ID "
            f"(e.g. `[2024-01-01 12:00:00] [123456789] <@12345>: ...`). "
            f"You are jumping into the conversation voluntarily — nobody mentioned you. "
            f"Be casual, brief, and natural. Don't announce yourself or explain why you're talking. "
            f"Just contribute to the conversation like any other participant would. "
            f"Respond with ONLY the text content of your reply, "
            f"without prefixing it with your own ID or message ID.\n"
            f"CRITICAL INSTRUCTION: DO NOT start your own messages with a `[timestamp] [message ID]` prefix. "
            f"Just write your text directly as a speaker.\n"
            f"CRITICAL INSTRUCTION: When @mentioning other users in your reply, use THEIR Discord ID "
            f"from the chat history — never use your own ID <@{bot_id}> to mention someone else.\n\n"
            f"{legend_section}\n\n"
        )

        # Build chat context (multimodal with audio/image support). The interject
        # path does not enrich messages with fetched URL content.
        chat_context = await build_chat_context(
            context_messages,
            bot_id,
            attachment_converter=attachment_to_base64_data_url,
            url_converter=url_to_base64_data_url,
            transcriber=transcribe_audio_attachment,
        )

        # Get the model for this channel
        model = self._state_service.get_model(channel.id) or self._default_model

        # Identity threaded into every model call for this interjection. Unlike a
        # mention, channel_id is included so Gemini gets per-channel interaction
        # caching (multi-turn continuity) here too.
        identity = {
            "channel_id": channel.id,
            "bot_id": bot_id,
            "discord_user_id": context_messages[-1].author.id if context_messages else None,
        }

        hybrid_cfg = None
        if model == "hybrid":
            hybrid_summary_prompt = (
                f"\n\nYou are a chat CONTEXT GATHERER only, you are not participating in the chat:\n"
                f"Do not reply directly to the user. Instead, review the entire chat buffer provided. "
                f"You must summarize all the available information, perform any web searches needed to enrich the context, "
                f"and extract the main points or questions.\n"
                f"CRITICAL: Be sure to include the <@Discord_User_IDs> of the participants in your summary so the final responder knows exactly who said what, and clearly point out what the current topic is.\n"
                f"CRITICAL: When referring to the bot in your summary, speak directly to the final responder as 'YOU' (e.g. 'User <@ID> asked YOU a question'). Do NOT refer to the bot in the third person (like '<@{bot_id}>' or 'the bot') so the final responder doesn't get confused.\n\n"
                f"CRITICAL: DO NOT MAKE THINGS UP, IF YOU CAN'T SEE CONTENT OR A VIDEO OR DON'T KNOW SOMETHING, SAY SO. No hallucinating allowed!!!\n\n"
                f"CRITICAL: You are to act as a casual viewer to the conversation. Note if there is a place for another bot to interject to add to the current conversation from an outside perspective, but if you have nothing that would add or change the current conversation happening between others just return an empty response.\n\n"
                f"CRITICAL: Include relevant times and dates!\n\n"
                f"Provide a *detailed*, unfiltered, comprehensive briefing of information from the channel conversation, any relevant urls or summaries (summarized by you), "
                f"search results you found to do with the conversation, and who said what (YOU ARE *NOT* REPLYING IN THE CHANNEL -- you are SUMMARIZING THE CONVERSATION TO ANOTHER AI AGENT). "
                f"Another AI model will use this briefing to write the final response."
            )
            hybrid_cfg = HybridConfig(
                summary_prompt=hybrid_summary_prompt,
                phase2_system_prompt=system_prompt,
                build_phase2_text=lambda s: f"Here is the context and gathered search results for the current conversation. Please use this information to write an interjection to the channel. Remember your personality and instructions from the system prompt. CRITICAL: You ARE the bot in this conversation. If the summary refers to the bot or <@{bot_id}>, it is referring to YOU. Do not refer to yourself in the third person.\n\nContext & Search Results:\n{s}",
            )

        try:
            reply_content, cost = await dispatch_completion(
                model=model,
                messages=chat_context,
                system_prompt=system_prompt,
                openai_service=self._openai_service,
                gemini_service=self._gemini_service,
                identity=identity,
                hybrid=hybrid_cfg,
                label="Interject",
            )

            if reply_content is None:
                logger.warning("Interject got None from AI, skipping")
                return

            # Handle response format (could be dict with files or plain string)
            if isinstance(reply_content, dict) and "text" in reply_content:
                reply_text = reply_content["text"]
            else:
                reply_text = reply_content

            if reply_text and reply_text.strip():
                if cost > 0:
                    reply_text = f"[${cost:.3f}] {reply_text}"
                await self._message_service.send_channel_reply(channel, reply_text)
                logger.info(f"💬 Interjected in #{channel.name}: {reply_text[:80]}...")

        except Exception as e:
            logger.error(f"Failed to generate interjection for #{channel.name}: {e}")
            return

        # Apply cooldown and increment daily counter
        self._apply_cooldown(channel.id)
        self._increment_daily_count(channel.id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_setting(self, channel_id: int, key: str, default: Any) -> Any:
        """Helper to get a channel-specific interject setting or default."""
        settings = self._state_service.get_interject_settings(channel_id)
        if settings and key in settings:
            return settings[key]
        return default

    def _is_on_cooldown(self, channel_id: int) -> bool:
        """Check if a channel is currently on interjection cooldown."""
        expires = self._cooldowns.get(channel_id, 0)
        return time.time() < expires

    def _apply_cooldown(self, channel_id: int) -> None:
        """Put a channel on cooldown."""
        cooldown = self._get_setting(channel_id, "cooldown", COOLDOWN_MINUTES)
        self._cooldowns[channel_id] = time.time() + (cooldown * 60)

    def _is_daily_cap_reached(self, channel_id: int) -> bool:
        """Check if the daily interjection cap for the channel has been reached."""
        today = datetime.now().strftime("%Y-%m-%d")
        if self._daily_tracker["date"] != today:
            # New day — reset
            self._daily_tracker = {"date": today, "counts": {}}
            self._save_state()

        daily_max = self._get_setting(channel_id, "daily_max", MAX_INTERJECTIONS_PER_DAY)
        return self._get_daily_count(channel_id) >= daily_max

    def _get_daily_count(self, channel_id: int) -> int:
        """Return the persisted daily count for a channel, handling legacy key types."""
        counts = self._daily_tracker["counts"]
        return counts.get(str(channel_id), counts.get(channel_id, 0))

    def _increment_daily_count(self, channel_id: int) -> None:
        """Increment the daily interjection counter for the channel."""
        today = datetime.now().strftime("%Y-%m-%d")
        channel_key = str(channel_id)  # Persist JSON-friendly string keys.

        if self._daily_tracker["date"] != today:
            self._daily_tracker = {"date": today, "counts": {channel_key: 1}}
        else:
            current = self._get_daily_count(channel_id)
            self._daily_tracker["counts"][channel_key] = current + 1

        self._save_state()

    def get_daily_status(self, channel_id: int) -> tuple[int, int]:
        """Return the current daily count and maximum cap for the given channel."""
        today = datetime.now().strftime("%Y-%m-%d")
        if self._daily_tracker["date"] != today:
            return 0, self._get_setting(channel_id, "daily_max", MAX_INTERJECTIONS_PER_DAY)

        daily_max = self._get_setting(channel_id, "daily_max", MAX_INTERJECTIONS_PER_DAY)
        return self._get_daily_count(channel_id), daily_max

    def _save_state(self) -> None:
        """Save the daily tracker to disk."""
        if not self._state_file:
            return
        try:
            os.makedirs(os.path.dirname(self._state_file) or ".", exist_ok=True)
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(self._daily_tracker, f)
        except (OSError, ValueError) as exc:
            logger.error(f"Failed to save interject counts state: {exc}")

    def _load_state(self) -> None:
        """Load daily tracker from disk."""
        if not self._state_file or not os.path.exists(self._state_file):
            return
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                self._daily_tracker = json.load(f)
            logger.info("Loaded daily interject counts from state file")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.error(f"Failed to load interject counts state: {exc}")

    @staticmethod
    def _roll_chance(chance: int) -> bool:
        """Roll a random number and return True if we should interject."""
        return random.randint(1, 100) <= chance
