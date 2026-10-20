"""
Memory Commands - /memory (view and edit your own memory) and /fillmemory (rebuild from the last week).
"""

import logging
from typing import Any, Optional

import discord
from discord import app_commands

from src.services.memory_service import FILL_DAYS, MemoryService
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
    """Handles the /memory group and /fillmemory."""

    def __init__(
        self,
        bot: discord.ext.commands.Bot,
        *,
        memory_service: MemoryService,
        memory_store,
        paste_service: Any = None,
    ):
        self.bot = bot
        self._memory_service = memory_service
        self._store = memory_store
        self._paste_service = paste_service

    def setup_commands(self) -> None:
        self.bot.tree.add_command(self._create_memory_group())
        self.bot.tree.add_command(self._create_fillmemory_command())

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

    async def _send_memory(self, interaction: discord.Interaction, text: str, *, updated: bool = False) -> None:
        """Privately send a memory reply, via paste.rs when it's too long for one Discord message."""
        if len(text) > DISCORD_MESSAGE_LIMIT:
            try:
                if not self._paste_service:
                    raise RuntimeError("paste service is disabled")
                url = await self._paste_service.upload_markdown(text)
                text = f"{'Updated 👍 ' if updated else ''}It's too long for Discord, so here it is: {url}"
            except Exception as e:
                logger.warning(f"Couldn't paste a long memory reply: {e}")
                text = (
                    "Updated 👍 but it's too long to show here and paste.rs isn't cooperating. Try `/memory show` later."
                    if updated
                    else "That memory is too long to show here and paste.rs isn't cooperating. Try again later."
                )
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_PINGS)

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
