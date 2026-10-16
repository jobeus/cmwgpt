"""
Memory Commands - /memory (view and edit your own memory) and /fillmemory (rebuild from the last week).
"""

import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.app_commands import Choice

from src.services.memory_service import FILL_DAYS, SELF_NOTES_MAX_CHARS, MemoryService
from src.utils.async_utils import safe_run
from src.utils.discord_error_utils import safe_defer

logger = logging.getLogger(__name__)

# Profiles mention other people by <@id>; showing one shouldn't ping them.
NO_PINGS = discord.AllowedMentions.none()


def format_memory(user_id: int, profile: str, self_notes: str) -> str:
    """The /memory show reply."""
    if not profile and not self_notes:
        return f"I don't remember anything about <@{user_id}> yet."
    sections = [f"**What I remember about <@{user_id}>**"]
    if profile:
        sections.append(f"**Learned from chat:**\n{profile}")
    if self_notes:
        sections.append(f"**You told me:**\n{self_notes}")
    return "\n\n".join(sections)


class MemoryCommands:
    """Handles the /memory group and /fillmemory."""

    def __init__(self, bot: discord.ext.commands.Bot, *, memory_service: MemoryService, memory_store):
        self.bot = bot
        self._memory_service = memory_service
        self._store = memory_store

    def setup_commands(self) -> None:
        self.bot.tree.add_command(self._create_memory_group())
        self.bot.tree.add_command(self._create_fillmemory_command())

    def _create_memory_group(self) -> app_commands.Group:
        group = app_commands.Group(name="memory", description="See or change what the bot remembers about you")

        @group.command(name="show", description="Show what the bot remembers about you")
        async def memory_show(interaction: discord.Interaction):
            if not await safe_defer(interaction, ephemeral=False, thinking=True):
                return
            asyncio.create_task(safe_run(interaction, self._handle_show, interaction))

        @group.command(name="add", description="Tell the bot something to remember about you")
        @app_commands.describe(note="Something about you, like 'I drive a 2019 Tacoma'")
        async def memory_add(interaction: discord.Interaction, note: str):
            if not await safe_defer(interaction, ephemeral=False, thinking=True):
                return
            asyncio.create_task(safe_run(interaction, self._handle_add, interaction, note))

        @group.command(name="clear", description="Forget things about you")
        @app_commands.describe(what="What to forget (default: everything)")
        @app_commands.choices(what=[
            Choice(name="everything", value="all"),
            Choice(name="only what I told you", value="notes"),
        ])
        async def memory_clear(interaction: discord.Interaction, what: Optional[str] = "all"):
            if not await safe_defer(interaction, ephemeral=False, thinking=True):
                return
            asyncio.create_task(safe_run(interaction, self._handle_clear, interaction, what or "all"))

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
            asyncio.create_task(safe_run(interaction, self._handle_fill, interaction))

        return fillmemory_command

    async def _handle_show(self, interaction: discord.Interaction) -> None:
        profile, self_notes = await self._store.get(interaction.guild_id, interaction.user.id)
        await interaction.followup.send(
            format_memory(interaction.user.id, profile, self_notes), allowed_mentions=NO_PINGS
        )

    async def _handle_add(self, interaction: discord.Interaction, note: str) -> None:
        note = " ".join(note.split())
        if not note:
            await interaction.followup.send("That note is empty.")
            return
        _, self_notes = await self._store.get(interaction.guild_id, interaction.user.id)
        updated = f"{self_notes}\n- {note}".strip() if self_notes else f"- {note}"
        if len(updated) > SELF_NOTES_MAX_CHARS:
            await interaction.followup.send(
                f"Your notes are capped at {SELF_NOTES_MAX_CHARS} characters and that would go over "
                f"({len(updated)}). Use `/memory clear` with \"only what I told you\" to start over.",
                allowed_mentions=NO_PINGS,
            )
            return
        await self._store.save_self_notes(interaction.guild_id, interaction.user.id, updated, source="manual")
        logger.info(f"[/memory add] {interaction.user} added a note")
        await interaction.followup.send(f"Got it, <@{interaction.user.id}>. I'll remember: {note}", allowed_mentions=NO_PINGS)

    async def _handle_clear(self, interaction: discord.Interaction, what: str) -> None:
        if what == "notes":
            await self._store.save_self_notes(interaction.guild_id, interaction.user.id, "", source="clear")
            message = "Cleared what you told me. What I've learned from chat is still there."
        else:
            await self._store.clear_user(interaction.guild_id, interaction.user.id)
            message = "Cleared everything I remembered about you. I'll start learning again from new chat."
        logger.info(f"[/memory clear] {interaction.user} cleared {what}")
        await interaction.followup.send(message)

    async def _handle_fill(self, interaction: discord.Interaction) -> None:
        if not self._memory_service.available:
            await interaction.followup.send("Memory is off: the memory model has no API key configured.")
            return
        if self._memory_service.busy:
            await interaction.followup.send("A memory run is already going. Try again when it finishes.")
            return

        await interaction.followup.send(
            f"Rebuilding everyone's memory from the last {FILL_DAYS} days of chat. "
            f"This takes a few minutes; I'll post progress here."
        )
        channel = interaction.channel

        async def progress(text: str) -> None:
            try:
                await channel.send(text)
            except discord.HTTPException as e:
                logger.warning(f"Couldn't post /fillmemory progress: {e}")

        try:
            totals = await self._memory_service.fill(interaction.guild, progress=progress)
        except RuntimeError as e:
            await channel.send(str(e))
            return
        except Exception:
            logger.exception("/fillmemory failed")
            await channel.send("Memory rebuild failed partway through; the logs have details.")
            return
        await channel.send(
            f"Memory rebuilt: read {totals['messages']} messages over {totals['days']} days and "
            f"updated {len(totals['users_updated'])} people (${totals['cost']:.3f})."
        )
