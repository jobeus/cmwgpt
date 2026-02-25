"""
Image Commands - Handles image generation Discord commands
"""

import io
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands
from src.config import DEFAULT_IMAGE_MODEL, DEFAULT_DRAW_MODEL, RUNPOD_IO_API_KEY
from src.services.openai_service import openai_service, OpenAIServiceError
from src.services.runpod_service import runpod_service, RunpodServiceError
from src.services.message_service import message_service
from src.services.queue_service import queue_service
from src.services.state_service import state_service

logger = logging.getLogger(__name__)


class ImageCommands:
    """Handles image-related Discord commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def setup_commands(self) -> None:
        """Set up all image-related commands."""
        self.bot.tree.add_command(self._create_draw_command())
        self.bot.tree.add_command(self._create_drawmodel_command())

    def _create_draw_command(self) -> app_commands.Command:
        """Create the /draw command."""

        model_choices = [
            Choice(name="gpt-image-1.5", value="gpt-image-1.5"),
        ]
        
        if RUNPOD_IO_API_KEY:
            model_choices.extend([
                Choice(name="z-image", value="z-image"),
                Choice(name="wan-2.6", value="wan-2.6"),
                Choice(name="pruna", value="pruna"),
                Choice(name="seedream", value="seedream"),
                Choice(name="qwen", value="qwen"),
                Choice(name="flux", value="flux"),
            ])

        @app_commands.command(name="draw",
                              description="Generate an image from a prompt")
        @app_commands.describe(
            prompt="Prompt for image generation",
            edit_image="Optional image to edit",
            model="Optional image model to use",
        )
        @app_commands.choices(
            model=model_choices
        )
        async def draw(
            interaction: discord.Interaction,
            prompt: str,
            edit_image: Optional[discord.Attachment] = None,
            model: Optional[str] = None,
        ):
            # Immediately defer the interaction to avoid Discord's 3-second
            # timeout
            await interaction.response.defer(ephemeral=False, thinking=True)

            # Queue the command for FIFO processing
            queued = await queue_service.queue_command(
                interaction, self._handle_draw_command, prompt, edit_image, model
            )

            if not queued:
                logger.warning(
                    f"""Failed to queue draw command from {
                        interaction.user} in #{
                        interaction.channel} - queue may be full"""
                )
                await interaction.followup.send(
                    "Sorry, the bot is currently busy. Please try again in a moment.", ephemeral=True
                )

        return draw

    async def _handle_draw_command(
        self,
        interaction: discord.Interaction,
        prompt: str,
        edit_image: Optional[discord.Attachment] = None,
        model: Optional[str] = None,
    ) -> None:
        """
        Handle the /draw command.

        Args:
            interaction: The Discord interaction
            prompt: The image generation prompt
            edit_image: Optional image to edit
            model: The model to use for generation
        """
        channel_id = interaction.channel.id
        
        state_service.mark_channel_active(channel_id)
        
        # Use provided model, or channel default, or global default
        active_model = model or state_service.get_draw_model(channel_id) or DEFAULT_DRAW_MODEL

        logger.info(
            f"[/draw] Channel {channel_id} Prompt: {prompt} Model: {active_model} Edit? {bool(edit_image)}")

        # Interaction already deferred in slash command handler
        async with interaction.channel.typing():
            try:
                if edit_image:
                    logger.info(
                        f"[/draw] Channel {channel_id}: editing image {edit_image.filename}")

                # Generate the image
                if runpod_service.has_model(active_model):
                    if edit_image:
                        await interaction.followup.send(
                            content="Sorry, the selected Runpod model does not support image editing.",
                        )
                        return
                    img_bytes = await runpod_service.generate_image(prompt=prompt, model=active_model)
                else:
                    img_bytes = await openai_service.generate_image(prompt=prompt, model=active_model, edit_image=edit_image)

                # Log success and create Discord file
                logger.info(f"[/draw] Channel {channel_id}: image generated")
                file = discord.File(
                    io.BytesIO(img_bytes),
                    filename="image.png")

                # Send the result
                if edit_image:
                    content = message_service.format_attachment_message(
                        edit_image, prompt)
                else:
                    content = message_service.format_prompt_message(prompt)

                await interaction.followup.send(content=content, file=file)

            except RunpodServiceError as e:
                logger.error(f"Runpod API error in draw command: {e}")
                error_message = (
                    f"{message_service.format_prompt_message(prompt)}\n\n"
                    f"Sorry, I encountered an error while generating your image: {str(e)}"
                )
                try:
                    await interaction.followup.send(content=error_message)
                except Exception as discord_error:
                    logger.error(
                        f"Failed to send error message to Discord: {discord_error}")
                    try:
                        await interaction.followup.send(
                            content="Sorry, I encountered an error generating your image. Please try again later."
                        )
                    except Exception:
                        logger.error("Failed to send fallback error message")

            except OpenAIServiceError as e:
                logger.error(f"OpenAI API error in draw command: {e}")
                error_message = (
                    f"{message_service.format_prompt_message(prompt)}\n\n"
                    f"Sorry, I encountered an error while generating your image: {str(e)}"
                )
                try:
                    await interaction.followup.send(content=error_message)
                except Exception as discord_error:
                    logger.error(
                        f"Failed to send error message to Discord: {discord_error}")
                    # Try to send a simpler error message
                    try:
                        await interaction.followup.send(
                            content="Sorry, I encountered an error generating your image. Please try again later."
                        )
                    except Exception:
                        logger.error("Failed to send fallback error message")

            except Exception as e:
                logger.error(f"Unexpected error in draw command: {e}")
                error_message = (
                    f"{message_service.format_prompt_message(prompt)}\n\n"
                    f"Sorry, there was an unexpected error generating your image. Please try again later."
                )
                try:
                    await interaction.followup.send(content=error_message)
                except Exception as discord_error:
                    logger.error(
                        f"Failed to send error message to Discord: {discord_error}")
                    # Try to send a simpler error message
                    try:
                        await interaction.followup.send(
                            content="Sorry, I encountered an error generating your image. Please try again later."
                        )
                    except Exception:
                        logger.error("Failed to send fallback error message")
    
    def _create_drawmodel_command(self) -> app_commands.Command:
        """Create the /drawmodel command."""
        
        model_choices = [
            Choice(name="gpt-image-1.5", value="gpt-image-1.5"),
        ]
        
        if RUNPOD_IO_API_KEY:
            model_choices.extend([
                Choice(name="z-image", value="z-image"),
                Choice(name="wan-2.6", value="wan-2.6"),
                Choice(name="pruna", value="pruna"),
                Choice(name="seedream", value="seedream"),
                Choice(name="qwen", value="qwen"),
                Choice(name="flux", value="flux"),
            ])

        @app_commands.command(name="drawmodel",
                              description="View or set the default drawing model")
        @app_commands.describe(model="Model name to use")
        @app_commands.choices(
            model=model_choices
        )
        async def drawmodel_command(
                interaction: discord.Interaction,
                model: Optional[str] = None):
            await interaction.response.defer(ephemeral=False, thinking=True)
            queued = await queue_service.queue_command(interaction, self._handle_drawmodel_command, model)
            if not queued:
                logger.warning(
                    f"Failed to queue drawmodel command from {interaction.user} in #{interaction.channel} - queue may be full"
                )
                await interaction.followup.send(
                    "Sorry, the bot is currently busy. Please try again in a moment.", ephemeral=True
                )
        return drawmodel_command

    async def _handle_drawmodel_command(self,
                                        interaction: discord.Interaction,
                                        model: Optional[str] = None) -> None:
        """Handle the /drawmodel command."""
        channel_id = interaction.channel.id
        state_service.mark_channel_active(channel_id)

        if model:
            state_service.set_draw_model(channel_id, model)
            logger.info(f"[/drawmodel] Channel {channel_id}: draw model set to {model}")
            await interaction.followup.send(f"Default draw model set to `{model}`.", ephemeral=True)
        else:
            current_model = state_service.get_draw_model(channel_id) or DEFAULT_DRAW_MODEL
            await interaction.followup.send(f"Default draw model is `{current_model}`.", ephemeral=True)
