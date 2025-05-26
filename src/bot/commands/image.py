"""
Image Commands - Handles image generation Discord commands
"""

import io
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.app_commands import Choice
from openai import BadRequestError

from src.config import DEFAULT_IMAGE_MODEL
from src.services.openai_service import openai_service
from src.services.message_service import message_service


logger = logging.getLogger(__name__)


class ImageCommands:
    """Handles image-related Discord commands."""

    def __init__(self, bot: discord.ext.commands.Bot):
        self.bot = bot

    def setup_commands(self) -> None:
        """Set up all image-related commands."""
        self.bot.tree.add_command(self._create_draw_command())

    def _create_draw_command(self) -> app_commands.Command:
        """Create the /draw command."""

        @app_commands.command(name="draw", description="Generate an image from a prompt")
        @app_commands.describe(
            prompt="Prompt for image generation",
            edit_image="Optional image to edit",
            model="Optional image model to use",
        )
        @app_commands.choices(
            model=[
                Choice(name="gpt-image-1", value="gpt-image-1"),
                Choice(name="dall-e-2", value="dall-e-2"),
                Choice(name="dall-e-3", value="dall-e-3"),
            ]
        )
        async def draw(
            interaction: discord.Interaction,
            prompt: str,
            edit_image: Optional[discord.Attachment] = None,
            model: str = DEFAULT_IMAGE_MODEL,
        ):
            await self._handle_draw_command(interaction, prompt, edit_image, model)

        return draw

    async def _handle_draw_command(
        self,
        interaction: discord.Interaction,
        prompt: str,
        edit_image: Optional[discord.Attachment] = None,
        model: str = DEFAULT_IMAGE_MODEL,
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
        logger.info(f"[/draw] Channel {channel_id} Prompt: {prompt} Model: {model} Edit? {bool(edit_image)}")

        # Acknowledge the interaction and show typing
        await interaction.response.defer(ephemeral=False, thinking=True)

        async with interaction.channel.typing():
            try:
                if edit_image:
                    logger.info(f"[/draw] Channel {channel_id}: editing image {edit_image.filename}")

                # Generate the image
                img_bytes = openai_service.generate_image(prompt=prompt, model=model, edit_image=edit_image)

                # Log success and create Discord file
                logger.info(f"[/draw] Channel {channel_id}: image generated")
                file = discord.File(io.BytesIO(img_bytes), filename="image.png")

                # Send the result
                if edit_image:
                    content = message_service.format_attachment_message(edit_image, prompt)
                else:
                    content = message_service.format_prompt_message(prompt)

                await interaction.followup.send(content=content, file=file)

            except BadRequestError as e:
                logger.error(f"OpenAI API BadRequestError in draw command: {e}")
                error_message = (
                    f"{message_service.format_prompt_message(prompt)}\n\n"
                    f"Sorry, your request was rejected by the safety system. Details: "
                    f"{e.error.message if e.error else 'No specific error message provided by API.'}"
                )
                await interaction.followup.send(content=error_message)
            except Exception as e:
                logger.error(f"Unexpected error in draw command: {e}")
                error_message = (
                    f"{message_service.format_prompt_message(prompt)}\n\n"
                    f"Sorry, there was an error generating your image. Please try again later."
                )
                await interaction.followup.send(content=error_message)
