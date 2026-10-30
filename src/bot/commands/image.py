"""
Image Commands - Handles image generation Discord commands
"""

import base64
import io
import logging
from typing import List, Optional

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands
from src.services.openai_service import OpenAIServiceError
from src.services.image_service import GEMINI_IMAGE_MODELS, LEGACY_MODEL_IDS, generate_image, resolve_draw_model
from src.services.runpod_service import RunpodServiceError
from src.utils.async_utils import safe_run
from src.utils.discord_error_utils import best_effort_typing, safe_defer

logger = logging.getLogger(__name__)

# Model choices for the image commands: the Gemini ones and seedream always, the rest only
# when the Runpod models are enabled.
BASE_CHOICES = [
    Choice(name="banana-pro", value="gemini-3-pro-image"),
    Choice(name="banana-2.1", value="gemini-nano-banana-2.1"),
    Choice(name="seedream", value="seedream"),
]
RUNPOD_DRAW_CHOICES = [
    Choice(name=name, value=name) for name in ("z-image", "wan-2.6", "pruna", "qwen", "flux")
]
RUNPOD_EDIT_CHOICES = [Choice(name=name, value=name) for name in ("qwen", "pruna")]


class ImageCommands:
    """Handles image-related Discord commands."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        state_service,
        message_service,
        runpod_service,
        gemini_service,
        default_draw_model: str,
        default_edit_model: str,
        enable_runpod_models: bool,
    ):
        self.bot = bot
        self._state_service = state_service
        self._message_service = message_service
        self._runpod_service = runpod_service
        self._gemini_service = gemini_service
        self._default_draw_model = default_draw_model
        self._default_edit_model = default_edit_model
        self._enable_runpod_models = enable_runpod_models

    def _model_choices(self, runpod_choices: List[Choice[str]]) -> List[Choice[str]]:
        return BASE_CHOICES + (runpod_choices if self._enable_runpod_models else [])

    def setup_commands(self) -> None:
        """Set up all image-related commands."""
        self.bot.tree.add_command(self._create_draw_command())
        self.bot.tree.add_command(self._create_drawmodel_command())
        self.bot.tree.add_command(self._create_edit_command())
        self.bot.tree.add_command(self._create_editmodel_command())

    def _create_draw_command(self) -> app_commands.Command:
        """Create the /draw command."""

        model_choices = self._model_choices(RUNPOD_DRAW_CHOICES)

        @app_commands.command(name="draw",
                              description="Generate an image from a prompt")
        @app_commands.describe(
            prompt="Prompt for image generation",
            model="Optional image model to use",
        )
        @app_commands.choices(
            model=model_choices
        )
        async def draw(
            interaction: discord.Interaction,
            prompt: str,
            image1: Optional[discord.Attachment] = None,
            image2: Optional[discord.Attachment] = None,
            image3: Optional[discord.Attachment] = None,
            image4: Optional[discord.Attachment] = None,
            model: Optional[str] = None,
        ):
            # Immediately defer the interaction to avoid Discord's 3-second
            # timeout
            if not await safe_defer(interaction, ephemeral=False, thinking=True):
                return

            await safe_run(interaction, self._handle_draw_command, interaction, prompt, image1, image2, image3, image4, model)

        return draw

    async def _handle_draw_command(
        self,
        interaction: discord.Interaction,
        prompt: str,
        image1: Optional[discord.Attachment] = None,
        image2: Optional[discord.Attachment] = None,
        image3: Optional[discord.Attachment] = None,
        image4: Optional[discord.Attachment] = None,
        model: Optional[str] = None,
    ) -> None:
        """
        Handle the /draw command.

        Args:
            interaction: The Discord interaction
            prompt: The image generation prompt
            model: The model to use for generation
        """
        channel_id = interaction.channel.id

        self._state_service.mark_channel_active(channel_id)

        active_model = resolve_draw_model(
            self._state_service, channel_id, self._default_draw_model, model)

        logger.info(
            f"[/draw] Channel {channel_id} Prompt: {prompt} Model: {active_model}")

        # Interaction already deferred in slash command handler
        async with best_effort_typing(interaction.channel):
            try:
                images_b64 = []
                if active_model in GEMINI_IMAGE_MODELS:
                    for img in [image1, image2, image3, image4]:
                        if img:
                            img_bytes = await img.read()
                            images_b64.append(base64.b64encode(img_bytes).decode('utf-8'))

                img_bytes, cost = await generate_image(
                    prompt=prompt,
                    model=active_model,
                    gemini_service=self._gemini_service,
                    runpod_service=self._runpod_service,
                    images_b64=images_b64,
                    discord_user_id=interaction.user.id,
                    discord_channel_id=channel_id,
                )

                # Log success and create Discord file
                logger.info(f"[/draw] Channel {channel_id}: image generated")
                file = discord.File(
                    io.BytesIO(img_bytes),
                    filename="image.png")

                # Send the result
                content = self._message_service.format_prompt_message(prompt)

                if cost is not None:
                    # prepend cost and model to the prompt text
                    content = content.replace(
                        "> ", f"> [${cost:.3f} @ {active_model}] ", 1)

                await interaction.followup.send(content=content, file=file)

            except RunpodServiceError as e:
                logger.error(f"Runpod API error in draw command: {e}")
                error_message = (
                    f"{self._message_service.format_prompt_message(prompt)}\n\n"
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
                    f"{self._message_service.format_prompt_message(prompt)}\n\n"
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
                    f"{self._message_service.format_prompt_message(prompt)}\n\n"
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

        model_choices = self._model_choices(RUNPOD_DRAW_CHOICES)

        @app_commands.command(name="drawmodel",
                              description="View or set the default drawing model")
        @app_commands.describe(model="Model name to use")
        @app_commands.choices(
            model=model_choices
        )
        async def drawmodel_command(
                interaction: discord.Interaction,
                model: Optional[str] = None):
            if not await safe_defer(interaction, ephemeral=False, thinking=True):
                return

            await safe_run(interaction, self._handle_drawmodel_command, interaction, model)
        return drawmodel_command

    async def _handle_drawmodel_command(self,
                                        interaction: discord.Interaction,
                                        model: Optional[str] = None) -> None:
        """Handle the /drawmodel command."""
        channel_id = interaction.channel.id
        self._state_service.mark_channel_active(channel_id)

        if model:
            self._state_service.set_draw_model(channel_id, model)
            logger.info(
                f"[/drawmodel] Channel {channel_id}: draw model set to {model}")
            await interaction.followup.send(f"Default draw model set to `{model}`.", ephemeral=True)
        else:
            current_model = self._state_service.get_draw_model(
                channel_id) or self._default_draw_model
            await interaction.followup.send(f"Default draw model is `{current_model}`.", ephemeral=True)

    def _create_edit_command(self) -> app_commands.Command:
        """Create the /edit command."""

        edit_model_choices = self._model_choices(RUNPOD_EDIT_CHOICES)

        @app_commands.command(name="edit",
                              description="Edit an image with a prompt")
        @app_commands.describe(
            prompt="Prompt for image editing",
            edit_image="Image to edit",
            image2="Optional 2nd Image (seedream only)",
            image3="Optional 3rd Image (seedream only)",
            image4="Optional 4th Image (seedream only)",
            model="Optional edit model to use",
        )
        @app_commands.choices(
            model=edit_model_choices
        )
        async def edit(
            interaction: discord.Interaction,
            prompt: str,
            edit_image: discord.Attachment,
            image2: Optional[discord.Attachment] = None,
            image3: Optional[discord.Attachment] = None,
            image4: Optional[discord.Attachment] = None,
            model: Optional[str] = None,
        ):
            if not await safe_defer(interaction, ephemeral=False, thinking=True):
                return

            await safe_run(interaction, self._handle_edit_command, interaction, prompt, edit_image, image2, image3, image4, model)

        return edit

    async def _handle_edit_command(
        self,
        interaction: discord.Interaction,
        prompt: str,
        edit_image: discord.Attachment,
        image2: Optional[discord.Attachment] = None,
        image3: Optional[discord.Attachment] = None,
        image4: Optional[discord.Attachment] = None,
        model: Optional[str] = None,
    ) -> None:
        """Handle the /edit command."""
        channel_id = interaction.channel.id
        self._state_service.mark_channel_active(channel_id)

        active_model = model or self._state_service.get_edit_model(
            channel_id) or self._default_edit_model
        active_model = LEGACY_MODEL_IDS.get(active_model, active_model)

        logger.info(
            f"[/edit] Channel {channel_id} Prompt: {prompt} Model: {active_model} Edit? True")

        async with best_effort_typing(interaction.channel):
            try:
                logger.info(
                    f"[/edit] Channel {channel_id}: editing image {edit_image.filename}")

                cost = None
                if active_model in GEMINI_IMAGE_MODELS:
                    # Gather base64 images (just edit_image for edit command as per instructions)
                    images_b64 = []
                    img_bytes = await edit_image.read()
                    images_b64.append(base64.b64encode(img_bytes).decode('utf-8'))
                        
                    img_bytes, cost = await self._gemini_service.edit_image(
                        prompt=prompt,
                        model=active_model,
                        images_b64=images_b64,
                        discord_user_id=interaction.user.id,
                        discord_channel_id=channel_id
                    )
                elif self._runpod_service.has_edit_model(active_model):
                    # Gather base64 images
                    images_b64 = []
                    for img in [edit_image, image2, image3, image4]:
                        if img:
                            img_bytes = await img.read()
                            images_b64.append(
                                base64.b64encode(img_bytes).decode('utf-8'))

                    img_bytes, cost = await self._runpod_service.edit_image(
                        prompt=prompt,
                        model=active_model,
                        images=images_b64,
                        discord_user_id=interaction.user.id,
                        discord_channel_id=channel_id
                    )
                else:
                    await interaction.followup.send(content=f"Sorry, model {active_model} does not support editing.")
                    return

                # Log success and create Discord file
                logger.info(f"[/edit] Channel {channel_id}: image generated")
                file = discord.File(
                    io.BytesIO(img_bytes),
                    filename="edited.png")

                embed = discord.Embed()
                embed.set_image(url="attachment://edited.png")

                # Send the result
                valid_images = [
                    img for img in [
                        edit_image,
                        image2,
                        image3,
                        image4] if img]
                cost_prefix = f"[${cost:.3f} @ {active_model}] " if cost is not None else ""
                formatted_prompt = f"{cost_prefix}{prompt}"
                content = self._message_service.format_attachment_message(
                    valid_images, formatted_prompt)
                await interaction.followup.send(content=content, file=file, embed=embed)

            except OpenAIServiceError as e:
                logger.error(f"OpenAI API error in edit command: {e}")
                error_message = (
                    f"{self._message_service.format_prompt_message(prompt)}\n\n"
                    f"Sorry, I encountered an error while editing your image: {str(e)}"
                )
                try:
                    await interaction.followup.send(content=error_message)
                except Exception as discord_error:
                    logger.error(
                        f"Failed to send error message to Discord: {discord_error}")
                    try:
                        await interaction.followup.send(
                            content="Sorry, I encountered an error editing your image. Please try again later."
                        )
                    except Exception:
                        logger.error("Failed to send fallback error message")

            except Exception as e:
                logger.error(f"Unexpected error in edit command: {e}")
                error_message = (
                    f"{self._message_service.format_prompt_message(prompt)}\n\n"
                    f"Sorry, there was an unexpected error editing your image. Please try again later."
                )
                try:
                    await interaction.followup.send(content=error_message)
                except Exception as discord_error:
                    logger.error(
                        f"Failed to send error message to Discord: {discord_error}")
                    try:
                        await interaction.followup.send(
                            content="Sorry, I encountered an error editing your image. Please try again later."
                        )
                    except Exception:
                        logger.error("Failed to send fallback error message")

    def _create_editmodel_command(self) -> app_commands.Command:
        """Create the /editmodel command."""

        edit_model_choices = self._model_choices(RUNPOD_EDIT_CHOICES)

        @app_commands.command(name="editmodel",
                              description="View or set the default drawing edit model")
        @app_commands.describe(model="Model name to use")
        @app_commands.choices(
            model=edit_model_choices
        )
        async def editmodel_command(
                interaction: discord.Interaction,
                model: Optional[str] = None):
            if not await safe_defer(interaction, ephemeral=False, thinking=True):
                return

            await safe_run(interaction, self._handle_editmodel_command, interaction, model)
        return editmodel_command

    async def _handle_editmodel_command(self,
                                        interaction: discord.Interaction,
                                        model: Optional[str] = None) -> None:
        """Handle the /editmodel command."""
        channel_id = interaction.channel.id
        self._state_service.mark_channel_active(channel_id)

        if model:
            self._state_service.set_edit_model(channel_id, model)
            logger.info(
                f"[/editmodel] Channel {channel_id}: edit model set to {model}")
            await interaction.followup.send(f"Default edit model set to `{model}`.", ephemeral=True)
        else:
            current_model = self._state_service.get_edit_model(
                channel_id) or self._default_edit_model
            await interaction.followup.send(f"Default edit model is `{current_model}`.", ephemeral=True)
