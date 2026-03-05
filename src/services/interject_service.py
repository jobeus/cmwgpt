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
import re
import time
from datetime import datetime, timezone
from typing import Optional, Any

import discord
from discord.ext import commands

from src.config import get_system_prompt, DEFAULT_MODEL
from src.services.openai_service import openai_service
from src.services.message_service import message_service
from src.services.state_service import state_service
from src.utils.discord_helper import get_mention_legend

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

# Pattern to strip cost prefixes like [$0.011] from the start of bot messages
COST_PREFIX_PATTERN = re.compile(r'^\[\$[\d.]+(?:\s*@\s*[^\]]+)?\]\s*')

# File to persist daily interjection counts
STATE_FILE = ".cache/cmwgpt_interject_counts.json"


class InterjectService:
    """Event-driven service that occasionally interjects in active channels."""

    def __init__(self):
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
        legend_section = await get_mention_legend(channel, bot_user)

        # Use the default system prompt (or channel override)
        channel_system_prompt = state_service.get_system_prompt(channel.id)
        system_prompt = channel_system_prompt or get_system_prompt()

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

        # Build chat context (simplified, text-only)
        chat_context: list[dict] = []
        for msg in context_messages:
            role = "assistant" if msg.author.id == bot_id else "user"
            timestamp_str = msg.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            text = f"[{timestamp_str}] [{msg.id}] <@{msg.author.id}>:"
            if msg.content:
                content = msg.content
                if role == "assistant":
                    content = COST_PREFIX_PATTERN.sub("", content)
                text += f" {content}"

            chat_context.append(
                {"role": role, "content": [{"type": "text", "text": text}]}
            )

        # Get the model for this channel
        model = state_service.get_model(channel.id) or DEFAULT_MODEL

        try:
            reply_content = await openai_service.get_chat_completion(
                model=model,
                messages=chat_context,
                system_prompt=system_prompt,
                bot_id=bot_id,
                discord_user_id=context_messages[-1].author.id if context_messages else None,
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
                await message_service.send_channel_reply(channel, reply_text)
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
        settings = state_service.get_interject_settings(channel_id)
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
        # Handle string keys from JSON loading
        current_count = self._daily_tracker["counts"].get(str(channel_id), self._daily_tracker["counts"].get(channel_id, 0))
        return current_count >= daily_max

    def _increment_daily_count(self, channel_id: int) -> None:
        """Increment the daily interjection counter for the channel."""
        today = datetime.now().strftime("%Y-%m-%d")
        channel_key = str(channel_id) # consistently use strings for json
        
        if self._daily_tracker["date"] != today:
            self._daily_tracker = {"date": today, "counts": {channel_key: 1}}
        else:
            current = self._daily_tracker["counts"].get(channel_key, self._daily_tracker["counts"].get(channel_id, 0))
            self._daily_tracker["counts"][channel_key] = current + 1
            
        self._save_state()

    def get_daily_status(self, channel_id: int) -> tuple[int, int]:
        """Return the current daily count and maximum cap for the given channel."""
        today = datetime.now().strftime("%Y-%m-%d")
        if self._daily_tracker["date"] != today:
             return 0, self._get_setting(channel_id, "daily_max", MAX_INTERJECTIONS_PER_DAY)
             
        daily_max = self._get_setting(channel_id, "daily_max", MAX_INTERJECTIONS_PER_DAY)
        current_count = self._daily_tracker["counts"].get(str(channel_id), self._daily_tracker["counts"].get(channel_id, 0))
        return current_count, daily_max

    def _save_state(self) -> None:
        """Save the daily tracker to disk."""
        try:
            os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self._daily_tracker, f)
        except (OSError, ValueError) as exc:
            logger.error(f"Failed to save interject counts state: {exc}")
            
    def _load_state(self) -> None:
        """Load daily tracker from disk."""
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                self._daily_tracker = json.load(f)
            logger.info("Loaded daily interject counts from state file")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.error(f"Failed to load interject counts state: {exc}")

    @staticmethod
    def _roll_chance(chance: int) -> bool:
        """Roll a random number and return True if we should interject."""
        return random.randint(1, 100) <= chance


# Global service instance
interject_service = InterjectService()
