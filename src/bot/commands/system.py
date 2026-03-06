"""
System Commands - Handles system/admin Discord commands
"""

import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.app_commands import Choice

from src.config import get_system_prompt, DEFAULT_MODEL
from src.utils.discord_helper import get_mention_legend
from src.utils.async_utils import safe_run

logger = logging.getLogger(__name__)


class SystemCommands:
    """Handles system/admin Discord commands."""

    def __init__(
        self,
        bot: discord.ext.commands.Bot,
        *,
        queue_service_instance,
        state_service_instance,
        auto_update_service=None,
        system_prompt_loader=get_system_prompt,
        default_model: str = DEFAULT_MODEL,
        mention_legend_provider=get_mention_legend,
    ):
        self.bot = bot
        self._queue_service = queue_service_instance
        self._state_service = state_service_instance
        self._auto_update_service = auto_update_service
        self._system_prompt_loader = system_prompt_loader
        self._default_model = default_model
        self._mention_legend_provider = mention_legend_provider

    def setup_commands(self) -> None:
        """Set up all system-related commands."""
        self.bot.tree.add_command(self._create_model_command())
        self.bot.tree.add_command(self._create_systemprompt_group())
        self.bot.tree.add_command(self._create_help_command())
        self.bot.tree.add_command(self._create_restart_command())

    def _create_model_command(self) -> app_commands.Command:
        """Create the /model command."""

        @app_commands.command(name="model",
                              description="View or set OpenAI model")
        @app_commands.describe(model="Model name to use")
        @app_commands.choices(
            model=[
                Choice(
                    name="google/gemini-2.5-flash",
                    value="google/gemini-2.5-flash"),
                Choice(
                    name="bytedance-seed/seed-2.0-mini",
                    value="bytedance-seed/seed-2.0-mini"),
                Choice(
                    name="bytedance-seed/seed-1.6-flash",
                    value="bytedance-seed/seed-1.6-flash"),
                Choice(
                    name="qwen/qwen3.5-flash-02-23",
                    value="qwen/qwen3.5-flash-02-23"),
                Choice(
                    name="anthropic/claude-haiku-4.5 (search, web aware)",
                    value="anthropic/claude-haiku-4.5"),
            ])
        async def model_command(
                interaction: discord.Interaction,
                model: Optional[str] = None):
            # Immediately defer the interaction to avoid Discord's 3-second
            # timeout
            await interaction.response.defer(ephemeral=False, thinking=True)

            # Queue the command for FIFO processing
            queued = await self._queue_service.queue_command(interaction, self._handle_model_command, model)

            if not queued:
                logger.warning(
                    f"Failed to queue model command from {interaction.user} in #{interaction.channel} - queue may be full"
                )
                await interaction.followup.send(
                    "Sorry, the bot is currently busy. Please try again in a moment.", ephemeral=True
                )

        return model_command

    def _create_systemprompt_group(self) -> app_commands.Group:
        """Create the /systemprompt command group."""
        systemprompt_group = app_commands.Group(
            name="systemprompt",
            description="Manage channel-specific system prompt")

        @systemprompt_group.command(name="set",
                                    description="View or set the system prompt for this channel")
        @app_commands.describe(
            prompt_text="The new system prompt. Omit to view current prompt.")
        async def systemprompt_set(
                interaction: discord.Interaction,
                prompt_text: Optional[str] = None):
            # Immediately defer the interaction to avoid Discord's 3-second
            # timeout
            await interaction.response.defer(ephemeral=True, thinking=True)

            # Queue the command for FIFO processing
            queued = await self._queue_service.queue_command(interaction, self._handle_systemprompt_set, prompt_text)

            if not queued:
                logger.warning(
                    f"Failed to queue systemprompt set command from {interaction.user} in #{interaction.channel} - queue may be full"
                )
                await interaction.followup.send(
                    "Sorry, the bot is currently busy. Please try again in a moment.", ephemeral=True
                )

        @systemprompt_group.command(
            name="reset",
            description="Reset the system prompt for this channel to the default")
        async def systemprompt_reset(interaction: discord.Interaction):
            # Immediately defer the interaction to avoid Discord's 3-second
            # timeout
            await interaction.response.defer(ephemeral=True, thinking=True)

            # Queue the command for FIFO processing
            queued = await self._queue_service.queue_command(interaction, self._handle_systemprompt_reset)

            if not queued:
                logger.warning(
                    f"Failed to queue systemprompt reset command from {interaction.user} in #{interaction.channel} - queue may be full"
                )
                await interaction.followup.send(
                    "Sorry, the bot is currently busy. Please try again in a moment.", ephemeral=True
                )

        @systemprompt_group.command(
            name="view",
            description="View the composed base system prompt for this channel")
        async def systemprompt_view(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True, thinking=True)

            # Fire-and-forget: read-only, no state mutation
            asyncio.create_task(
                safe_run(interaction, self._handle_systemprompt_view, interaction)
            )

        return systemprompt_group

    async def _handle_model_command(self,
                                    interaction: discord.Interaction,
                                    model: Optional[str] = None) -> None:
        """
        Handle the /model command.

        Args:
            interaction: The Discord interaction
            model: Optional model name to set
        """
        channel_id = interaction.channel.id
        # Interaction already deferred in slash command handler

        # Mark channel as active
        self._state_service.mark_channel_active(channel_id)

        if model:
            self._state_service.set_model(channel_id, model)
            logger.info(f"[/model] Channel {channel_id}: model set to {model}")
            await interaction.followup.send(f"Model set to `{model}`.", ephemeral=True)
        else:
            current_model = self._state_service.get_model(
                channel_id) or self._default_model
            await interaction.followup.send(f"Model is `{current_model}`.", ephemeral=True)

    async def _handle_systemprompt_set(
            self,
            interaction: discord.Interaction,
            prompt_text: Optional[str] = None) -> None:
        """
        Handle the /systemprompt set command.

        Args:
            interaction: The Discord interaction
            prompt_text: Optional new system prompt text
        """
        channel_id = interaction.channel.id
        # Interaction already deferred in slash command handler

        # Mark channel as active
        self._state_service.mark_channel_active(channel_id)

        legend_section = await self._mention_legend_provider(interaction.channel, self.bot.user)

        if prompt_text:
            # Set new system prompt (no longer stored in conversation arrays)
            self._state_service.set_system_prompt(channel_id, prompt_text)

            logger.info(
                f"[/systemprompt set] Channel {channel_id}: system prompt updated.")
            await interaction.followup.send(
                "System prompt updated for this channel. The new prompt will be used for future messages and context.",
                ephemeral=True,
            )
        else:
            # Show current system prompt
            current_prompt = self._state_service.get_system_prompt(channel_id)
            if not current_prompt:
                current_prompt = self._system_prompt_loader() + "\n" + legend_section
            logger.info(
                f"[/systemprompt set] Channel {channel_id}: displayed current system prompt.")
            await interaction.followup.send(
                f"Current system prompt for this channel:\n```\n{current_prompt}\n```", ephemeral=True
            )

    async def _handle_systemprompt_view(
            self, interaction: discord.Interaction) -> None:
        """
        Handle the /systemprompt view command.
        Shows the composed base system prompt used for channel-scoped bot behavior.

        Args:
            interaction: The Discord interaction
        """
        channel_id = interaction.channel.id

        legend_section = await self._mention_legend_provider(interaction.channel, self.bot.user)

        # Compose the channel base prompt with the current mention legend.
        channel_system_prompt = self._state_service.get_system_prompt(channel_id)
        if channel_system_prompt:
            full_prompt = channel_system_prompt + "\n" + legend_section
        else:
            full_prompt = self._system_prompt_loader() + "\n" + legend_section

        logger.info(
            f"[/systemprompt view] Channel {channel_id}: displayed full composed system prompt.")

        # Discord has a 2000-char message limit; use a code block
        content = f"**Full system prompt for this channel (as sent to the model):**\n```\n{full_prompt}\n```"
        if len(content) > 2000:
            # Truncate if too long for Discord
            max_prompt_len = 2000 - len("**Full system prompt for this channel (as sent to the model):**\n```\n\n```\n*[truncated]*")
            content = f"**Full system prompt for this channel (as sent to the model):**\n```\n{full_prompt[:max_prompt_len]}\n```\n*[truncated]*"

        await interaction.followup.send(content, ephemeral=True)

    async def _handle_systemprompt_reset(
            self, interaction: discord.Interaction) -> None:
        """
        Handle the /systemprompt reset command.

        Args:
            interaction: The Discord interaction
        """
        channel_id = interaction.channel.id
        # Interaction already deferred in slash command handler

        # Mark channel as active
        self._state_service.mark_channel_active(channel_id)

        # Remove custom prompt (system prompts no longer stored in conversation
        # arrays)
        self._state_service.clear_system_prompt(channel_id)
        logger.info(
            f"[/systemprompt reset] Channel {channel_id}: custom prompt removed, reverting to default.")

        logger.info(
            f"[/systemprompt reset] Channel {channel_id}: system prompt reset to default.")
        await interaction.followup.send("System prompt for this channel has been reset to the default.", ephemeral=True)

    def _create_restart_command(self) -> app_commands.Command:
        """Create the /restart command."""

        @app_commands.command(name="restart",
                              description="Restart the bot (admin only)")
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
            queued = await self._queue_service.queue_command(interaction, self._handle_restart_command)

            if not queued:
                logger.warning(
                    f"Failed to queue restart command from {interaction.user} in #{interaction.channel} - queue may be full"
                )
                await interaction.followup.send(
                    "Sorry, the bot is currently busy. Please try again in a moment.", ephemeral=True
                )
        return restart_command

    def _create_help_command(self) -> app_commands.Command:
        """Create the /help command."""

        @app_commands.command(name="help",
                              description="Get help with bot commands privately")
        async def help_command(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True, thinking=True)

            # Fire-and-forget: help is stateless, no need to queue
            asyncio.create_task(
                safe_run(interaction, self._handle_help_command, interaction)
            )
        return help_command

    async def _handle_help_command(
            self, interaction: discord.Interaction) -> None:
        """Handle the /help command."""
        logger.info(
            f"[/help] Help requested by {interaction.user} in #{interaction.channel}")

        help_text = (
            "**🤖 AI Bot Commands Help**\n\n"
            "**Chat & Conversations:**\n"
            "`@BotName [message]` - Mention the bot for context-aware responses in the channel\n\n"
            "**Image Generation & Editing:**\n"
            "`/draw [prompt]` - Generate an image with the AI\n"
            "`/edit [prompt] [edit_image]` - Edit an existing image (supports up to 4 images for seedream/qwen/pruna)\n"
            "`/drawmodel [model]` - View or set the default image generation model\n"
            "`/editmodel [model]` - View or set the default image editing model\n\n"
            "**System & Settings:**\n"
            "`/model [model]` - View or set the AI language model (e.g., anthropic/claude-haiku-4.5)\n"
            "`/systemprompt set [prompt]` - Set a custom AI personality for this channel\n"
            "`/systemprompt view` - View the current custom personality\n"
            "`/systemprompt reset` - Return to the default personality\n\n"
            "**Server Features:**\n"
            "`/interject set|view|reset` - Manage bot interjections for this channel\n"
            "`/death set|view|reset` - Manage global death announcements (death channel only)\n\n"
            "*Note: Commands may be queued if the bot is busy processing other requests.*")
        await interaction.followup.send(help_text, ephemeral=True)

    async def _handle_restart_command(
            self, interaction: discord.Interaction) -> None:
        """
        Handle the /restart command.

        Args:
            interaction: The Discord interaction
        """
        logger.info(
            f"[/restart] Manual restart requested by {interaction.user} in #{interaction.channel}"
        )

        # Send confirmation message
        await interaction.followup.send(
            "\U0001f504 Restarting bot... I'll be back shortly with any available updates!", ephemeral=False
        )

        # Trigger restart through auto-update service
        if not self._auto_update_service:
            await interaction.followup.send("❌ Restart service is not configured.", ephemeral=True)
            return

        success = await self._auto_update_service.trigger_restart(manual=True)

        if not success:
            logger.error("Failed to trigger restart")
            await interaction.followup.send("\u274c Failed to trigger restart. Please check the logs.", ephemeral=True)
