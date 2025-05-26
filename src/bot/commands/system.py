"""
System Commands - Handles system/admin Discord commands
"""

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.app_commands import Choice

from src.config import get_system_prompt, DEFAULT_MODEL
from src.utils.discord_helper import get_mention_legend
from src.services.queue_service import queue_service
from src.services.state_service import state_service
from src.services.auto_update_service import auto_update_service


logger = logging.getLogger(__name__)


class SystemCommands:
    """Handles system/admin Discord commands."""

    def __init__(self, bot: discord.ext.commands.Bot):
        self.bot = bot

    def setup_commands(self) -> None:
        """Set up all system-related commands."""
        self.bot.tree.add_command(self._create_model_command())
        self.bot.tree.add_command(self._create_systemprompt_group())
        self.bot.tree.add_command(self._create_restart_command())

    def _create_model_command(self) -> app_commands.Command:
        """Create the /model command."""

        @app_commands.command(name="model", description="View or set OpenAI model")
        @app_commands.describe(model="Model name to use")
        @app_commands.choices(
            model=[
                Choice(name="gpt-4.1-mini", value="gpt-4.1-mini"),
                Choice(name="gpt-4.1-nano", value="gpt-4.1-nano"),
                Choice(name="gpt-4o-mini", value="gpt-4o-mini"),
            ]
        )
        async def model_command(interaction: discord.Interaction, model: Optional[str] = None):
            # Immediately defer the interaction to avoid Discord's 3-second
            # timeout
            await interaction.response.defer(ephemeral=False, thinking=True)

            # Queue the command for FIFO processing
            queued = await queue_service.queue_command(interaction, self._handle_model_command, model)

            if not queued:
                logger.warning(
                    f"Failed to queue model command from {
                        interaction.user} in #{
                        interaction.channel} - queue may be full"
                )
                await interaction.followup.send(
                    "Sorry, the bot is currently busy. Please try again in a moment.", ephemeral=True
                )

        return model_command

    def _create_systemprompt_group(self) -> app_commands.Group:
        """Create the /systemprompt command group."""
        systemprompt_group = app_commands.Group(
            name="systemprompt", description="Manage channel-specific system prompt"
        )

        @systemprompt_group.command(name="set", description="View or set the system prompt for this channel")
        @app_commands.describe(prompt_text="The new system prompt. Omit to view current prompt.")
        async def systemprompt_set(interaction: discord.Interaction, prompt_text: Optional[str] = None):
            # Immediately defer the interaction to avoid Discord's 3-second
            # timeout
            await interaction.response.defer(ephemeral=True, thinking=True)

            # Queue the command for FIFO processing
            queued = await queue_service.queue_command(interaction, self._handle_systemprompt_set, prompt_text)

            if not queued:
                logger.warning(
                    f"Failed to queue systemprompt set command from {
                        interaction.user} in #{
                        interaction.channel} - queue may be full"
                )
                await interaction.followup.send(
                    "Sorry, the bot is currently busy. Please try again in a moment.", ephemeral=True
                )

        @systemprompt_group.command(name="reset", description="Reset the system prompt for this channel to the default")
        async def systemprompt_reset(interaction: discord.Interaction):
            # Immediately defer the interaction to avoid Discord's 3-second
            # timeout
            await interaction.response.defer(ephemeral=True, thinking=True)

            # Queue the command for FIFO processing
            queued = await queue_service.queue_command(interaction, self._handle_systemprompt_reset)

            if not queued:
                logger.warning(
                    f"Failed to queue systemprompt reset command from {
                        interaction.user} in #{
                        interaction.channel} - queue may be full"
                )
                await interaction.followup.send(
                    "Sorry, the bot is currently busy. Please try again in a moment.", ephemeral=True
                )

        return systemprompt_group

    async def _handle_model_command(self, interaction: discord.Interaction, model: Optional[str] = None) -> None:
        """
        Handle the /model command.

        Args:
            interaction: The Discord interaction
            model: Optional model name to set
        """
        channel_id = interaction.channel.id
        # Interaction already deferred in slash command handler

        # Mark channel as active
        state_service.mark_channel_active(channel_id)

        if model:
            state_service.set_model(channel_id, model)
            logger.info(f"[/model] Channel {channel_id}: model set to {model}")
            await interaction.followup.send(f"Model set to `{model}`.", ephemeral=True)
        else:
            current_model = state_service.get_model(channel_id) or DEFAULT_MODEL
            await interaction.followup.send(f"Model is `{current_model}`.", ephemeral=True)

    async def _handle_systemprompt_set(
        self, interaction: discord.Interaction, prompt_text: Optional[str] = None
    ) -> None:
        """
        Handle the /systemprompt set command.

        Args:
            interaction: The Discord interaction
            prompt_text: Optional new system prompt text
        """
        channel_id = interaction.channel.id
        # Interaction already deferred in slash command handler

        # Mark channel as active
        state_service.mark_channel_active(channel_id)

        legend_section = await get_mention_legend(interaction.channel)

        if prompt_text:
            # Set new system prompt (no longer stored in conversation arrays)
            state_service.set_system_prompt(channel_id, prompt_text)

            logger.info(f"[/systemprompt set] Channel {channel_id}: system prompt updated.")
            await interaction.followup.send(
                "System prompt updated for this channel. The new prompt will be used for future messages and context.",
                ephemeral=True,
            )
        else:
            # Show current system prompt
            current_prompt = state_service.get_system_prompt(channel_id)
            if not current_prompt:
                current_prompt = get_system_prompt() + "\n" + legend_section
            logger.info(f"[/systemprompt set] Channel {channel_id}: displayed current system prompt.")
            await interaction.followup.send(
                f"Current system prompt for this channel:\n```\n{current_prompt}\n```", ephemeral=True
            )

    async def _handle_systemprompt_reset(self, interaction: discord.Interaction) -> None:
        """
        Handle the /systemprompt reset command.

        Args:
            interaction: The Discord interaction
        """
        channel_id = interaction.channel.id
        # Interaction already deferred in slash command handler

        # Mark channel as active
        state_service.mark_channel_active(channel_id)

        # Remove custom prompt (system prompts no longer stored in conversation
        # arrays)
        state_service.clear_system_prompt(channel_id)
        logger.info(f"[/systemprompt reset] Channel {channel_id}: custom prompt removed, reverting to default.")

        logger.info(f"[/systemprompt reset] Channel {channel_id}: system prompt reset to default.")
        await interaction.followup.send("System prompt for this channel has been reset to the default.", ephemeral=True)

    def _create_restart_command(self) -> app_commands.Command:
        """Create the /restart command."""

        @app_commands.command(name="restart", description="Restart the bot (admin only)")
        async def restart_command(interaction: discord.Interaction):
            # Check if user has administrator permissions
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "❌ You need administrator permissions to use this command.", ephemeral=True
                )
                return

            # Immediately defer the interaction to avoid Discord's 3-second
            # timeout
            await interaction.response.defer(ephemeral=False, thinking=True)

            # Queue the command for FIFO processing
            queued = await queue_service.queue_command(interaction, self._handle_restart_command)

            if not queued:
                logger.warning(
                    f"Failed to queue restart command from {
                        interaction.user} in #{
                        interaction.channel} - queue may be full"
                )
                await interaction.followup.send(
                    "Sorry, the bot is currently busy. Please try again in a moment.", ephemeral=True
                )

        return restart_command

    async def _handle_restart_command(self, interaction: discord.Interaction) -> None:
        """
        Handle the /restart command.

        Args:
            interaction: The Discord interaction
        """
        logger.info(
            f"[/restart] Manual restart requested by {
                interaction.user} in #{
                interaction.channel}"
        )

        # Send confirmation message
        await interaction.followup.send(
            "🔄 Restarting bot... I'll be back shortly with any available updates!", ephemeral=False
        )

        # Trigger restart through auto-update service
        success = await auto_update_service.trigger_restart(manual=True)

        if not success:
            logger.error("Failed to trigger restart")
            await interaction.followup.send("❌ Failed to trigger restart. Please check the logs.", ephemeral=True)
