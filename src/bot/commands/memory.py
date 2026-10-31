"""
Memory Commands - /memory (view and edit your own memory), /fillmemory (rebuild from the
last week), /fillsummaries (rebuild only the channel summaries) and /botstate (see the
bot's own mood, interests, wonderings and follow-ups).
"""

import io
import logging
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import discord
from discord import app_commands

from src.services.memory_service import (
    FILL_DAYS, JOB_TIMEZONE, SELF_ASPECTS, SELF_REFRESH_INTERVAL, MemoryService, load_follow_ups,
)
from src.utils.async_utils import safe_run
from src.utils.discord_error_utils import safe_defer

logger = logging.getLogger(__name__)

# Profiles mention other people by <@id>; showing one shouldn't ping them.
NO_PINGS = discord.AllowedMentions.none()
DISCORD_MESSAGE_LIMIT = 2000


def format_memory(user_id: int, profile: str) -> str:
    """The /memory show reply."""
    if not profile:
        return f"I don't remember anything about <@{user_id}> yet."
    return f"**What I remember about <@{user_id}>**\n\n{profile}"



# Discord's limit on one embed field's value.
EMBED_FIELD_LIMIT = 1024
SELF_ASPECT_EMOJI = {"mood": "🌤️", "interests": "✨", "wondering": "🤔"}


def _bullets(name: str, value: str) -> str:
    """An aspect's text as a bullet list: interests split on semicolons, wonderings after each question."""
    if name == "mood":
        return value
    parts = value.split(";") if name == "interests" else re.split(r"(?<=\?)\s+", value)
    return "\n".join(f"• {part.strip()}" for part in parts if part.strip())


def _chunk_lines(lines: List[str], limit: int = EMBED_FIELD_LIMIT) -> List[str]:
    """Join lines into as few blocks as fit in an embed field each."""
    blocks: List[str] = []
    for line in lines:
        line = line[:limit]
        if blocks and len(blocks[-1]) + 1 + len(line) <= limit:
            blocks[-1] += "\n" + line
        else:
            blocks.append(line)
    return blocks


def build_self_embed(guild_name: str, state: Dict[str, str], today: date) -> discord.Embed:
    """The /botstate reply: each aspect as its own section, then the follow-ups, then refresh times."""
    embed = discord.Embed(title=f"🧠 My inner life in {guild_name}", color=discord.Color.blurple())
    for name, (label, _limit) in SELF_ASPECTS.items():
        value = state.get(name)
        if not value:
            continue
        since = (state.get(f"{name}_updated") or "")[:10]
        heading = f"{SELF_ASPECT_EMOJI.get(name, '•')} {label}" + (f" · since {since}" if since else "")
        embed.add_field(name=heading, value=_bullets(name, value)[:EMBED_FIELD_LIMIT], inline=False)

    follow_ups = load_follow_ups(state, today)
    lines = [f"`#{i['id']}` <@{i['user_id']}>: {i['about']} · *{i['added']}*" for i in follow_ups]
    for index, block in enumerate(_chunk_lines(lines)):
        heading = f"📌 Follow-ups ({len(follow_ups)})" if index == 0 else "📌 Follow-ups (continued)"
        embed.add_field(name=heading, value=block, inline=False)

    if not embed.fields:
        embed.description = "Nothing yet. It gets worked out from recent chat within a few minutes of starting."

    refreshed = state.get("refreshed_at")
    if refreshed:
        when = datetime.fromisoformat(refreshed).replace(tzinfo=timezone.utc)
        next_at = when + SELF_REFRESH_INTERVAL
        embed.add_field(
            name="🔄 Refreshes",
            value=f"Last <t:{int(when.timestamp())}:R>, next about <t:{int(next_at.timestamp())}:R>",
            inline=False,
        )
    embed.set_footer(text="Refreshed every 6 hours from recent chat and the news; I can also change it myself.")
    return embed


async def resolve_target(
    interaction: discord.Interaction, user: Optional[discord.Member]
) -> Optional[discord.abc.User]:
    """Whose memory a /memory command acts on: the caller, or ``user`` when the caller is an admin.

    Returns ``None`` (after telling the caller privately) when a non-admin names someone else.
    Call before deferring, so the refusal can be private.
    """
    if user is None or user.id == interaction.user.id:
        return interaction.user
    if interaction.user.guild_permissions.administrator:
        return user
    await interaction.response.send_message(
        "❌ Only administrators can view or change someone else's memory.", ephemeral=True
    )
    return None


class MemoryCommands:
    """Handles the /memory group, /fillmemory and /fillsummaries."""

    def __init__(
        self,
        bot: discord.ext.commands.Bot,
        *,
        memory_service: MemoryService,
        memory_store,
    ):
        self.bot = bot
        self._memory_service = memory_service
        self._store = memory_store

    def setup_commands(self) -> None:
        self.bot.tree.add_command(self._create_memory_group())
        self.bot.tree.add_command(self._create_fillmemory_command())
        self.bot.tree.add_command(self._create_fillsummaries_command())
        self.bot.tree.add_command(self._create_botstate_command())

    def _create_memory_group(self) -> app_commands.Group:
        group = app_commands.Group(name="memory", description="See or change what the bot remembers about you")
        user_help = "Admins only: act on this person instead of yourself"

        @group.command(name="show", description="Privately show what the bot remembers about you")
        @app_commands.describe(user=user_help)
        async def memory_show(interaction: discord.Interaction, user: Optional[discord.Member] = None):
            target = await resolve_target(interaction, user)
            if target is None or not await safe_defer(interaction, ephemeral=True, thinking=True):
                return
            await safe_run(interaction, self._handle_show, interaction, target)

        @group.command(name="correct", description="Fix something the bot remembers about you")
        @app_commands.describe(
            correction="What's wrong and what's right, like 'I sold the Tacoma, I drive a Ranger now'",
            user=user_help,
        )
        async def memory_correct(
            interaction: discord.Interaction, correction: str, user: Optional[discord.Member] = None
        ):
            target = await resolve_target(interaction, user)
            if target is None or not await safe_defer(interaction, ephemeral=True, thinking=True):
                return
            await safe_run(interaction, self._handle_correct, interaction, target, correction)

        @group.command(name="clear", description="Make the bot forget everything about you")
        async def memory_clear(interaction: discord.Interaction):
            if not await safe_defer(interaction, ephemeral=True, thinking=True):
                return
            await safe_run(interaction, self._handle_clear, interaction)

        return group

    def _create_fillmemory_command(self) -> app_commands.Command:
        @app_commands.command(
            name="fillmemory",
            description=f"Rebuild everyone's memory from the last {FILL_DAYS} days of chat (admin only)",
        )
        async def fillmemory_command(interaction: discord.Interaction):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "❌ You need administrator permissions to use this command.", ephemeral=True
                )
                return
            if not await safe_defer(interaction, ephemeral=False, thinking=True):
                return
            await safe_run(interaction, self._handle_fill, interaction)

        return fillmemory_command

    def _create_fillsummaries_command(self) -> app_commands.Command:
        @app_commands.command(
            name="fillsummaries",
            description=f"Rebuild every channel's 'lately' summary from the last {FILL_DAYS} days (admin only)",
        )
        async def fillsummaries_command(interaction: discord.Interaction):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "❌ You need administrator permissions to use this command.", ephemeral=True
                )
                return
            if not await safe_defer(interaction, ephemeral=False, thinking=True):
                return
            await safe_run(interaction, self._handle_fill_summaries, interaction)

        return fillsummaries_command

    def _create_botstate_command(self) -> app_commands.Command:
        @app_commands.command(
            name="botstate",
            description="Privately see the bot's own mood, interests, wonderings and follow-ups (admin only)",
        )
        async def botstate_command(interaction: discord.Interaction):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "❌ You need administrator permissions to use this command.", ephemeral=True
                )
                return
            if not await safe_defer(interaction, ephemeral=True, thinking=True):
                return
            await safe_run(interaction, self._handle_botstate, interaction)

        return botstate_command

    async def _handle_botstate(self, interaction: discord.Interaction) -> None:
        state = await self._store.get_self_state(interaction.guild_id)
        today = datetime.now(timezone.utc).astimezone(JOB_TIMEZONE).date()
        embed = build_self_embed(getattr(interaction.guild, "name", "this server"), state, today)
        await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=NO_PINGS)

    async def _send_memory(self, interaction: discord.Interaction, text: str, *, updated: bool = False) -> None:
        """Privately send a memory reply, as a .md attachment when it's too long for one Discord message.

        Kept in Discord rather than on a pastebin, since the reply is meant to be private.
        """
        prefix = "Updated 👍 " if updated else ""
        if len(text) <= DISCORD_MESSAGE_LIMIT:
            await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_PINGS)
            return
        file = discord.File(io.BytesIO(text.encode("utf-8")), filename="memory.md")
        await interaction.followup.send(
            f"{prefix}It's too long for one Discord message, so here it is as a file.",
            file=file,
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )

    async def _handle_show(self, interaction: discord.Interaction, target: discord.abc.User) -> None:
        profile = await self._store.get(interaction.guild_id, target.id)
        await self._send_memory(interaction, format_memory(target.id, profile))

    async def _handle_correct(
        self, interaction: discord.Interaction, target: discord.abc.User, correction: str
    ) -> None:
        correction = correction.strip()
        if not correction:
            await interaction.followup.send("That correction is empty.", ephemeral=True)
            return
        if not self._memory_service.available:
            await interaction.followup.send(
                "Memory is off: the memory model has no API key configured.", ephemeral=True
            )
            return
        try:
            profile, _ = await self._memory_service.correct(
                interaction.guild_id, target.id, getattr(target, "display_name", str(target)), correction
            )
        except ValueError:
            logger.exception(f"[/memory correct] unusable model reply for {target}")
            await interaction.followup.send(
                "I couldn't apply that correction, so nothing changed. Try rewording it.", ephemeral=True
            )
            return
        logger.info(f"[/memory correct] {interaction.user} corrected memory for {target}")
        await self._send_memory(interaction, f"Updated. {format_memory(target.id, profile)}", updated=True)

    async def _handle_clear(self, interaction: discord.Interaction) -> None:
        await self._store.clear_user(interaction.guild_id, interaction.user.id)
        logger.info(f"[/memory clear] {interaction.user} cleared their memory")
        await interaction.followup.send(
            "Cleared everything I remembered about you. I'll start learning again from new chat.", ephemeral=True
        )

    async def _handle_fill(self, interaction: discord.Interaction) -> None:
        totals = await self._run_fill(
            interaction,
            "/fillmemory",
            f"Rebuilding everyone's memory from the last {FILL_DAYS} days of chat. "
            f"This takes a few minutes; I'll post progress here.",
            self._memory_service.fill,
        )
        if totals:
            await interaction.channel.send(
                f"Memory rebuilt: read {totals['messages']} messages over {totals['days']} days and "
                f"updated {len(totals['users_updated'])} people (${totals['cost']:.3f})."
            )

    async def _handle_fill_summaries(self, interaction: discord.Interaction) -> None:
        totals = await self._run_fill(
            interaction,
            "/fillsummaries",
            f"Rebuilding every channel's summary from the last {FILL_DAYS} days of chat. "
            f"People's memories aren't touched. I'll post progress here.",
            self._memory_service.fill_channel_summaries,
        )
        if totals:
            await interaction.channel.send(
                f"Channel summaries rebuilt: read {totals['messages']} messages over {totals['days']} days "
                f"in {totals['channels']} channels (${totals['cost']:.3f})."
            )

    async def _run_fill(
        self, interaction: discord.Interaction, name: str, intro: str, run: Any
    ) -> Optional[dict]:
        """Run a fill job, posting progress in the channel. Returns its totals, or ``None`` if it didn't finish."""
        if not self._memory_service.available:
            await interaction.followup.send("Memory is off: the memory model has no API key configured.")
            return None
        if self._memory_service.busy:
            await interaction.followup.send("A memory run is already going. Try again when it finishes.")
            return None

        await interaction.followup.send(intro)
        channel = interaction.channel

        async def progress(text: str) -> None:
            try:
                await channel.send(text)
            except discord.HTTPException as e:
                logger.warning(f"Couldn't post {name} progress: {e}")

        try:
            return await run(interaction.guild, progress=progress)
        except RuntimeError as e:
            await channel.send(str(e))
        except Exception:
            logger.exception(f"{name} failed")
            await channel.send("The rebuild failed partway through; the logs have details.")
        return None
