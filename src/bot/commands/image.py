"""
Image Commands - /draw, /edit and /imagemodel, on Gemini's image models.
"""

import base64
import io
import logging
from typing import List, Optional

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands
from google.genai.errors import APIError

from src.services.gemini_service import GeminiServiceError
from src.services.image_service import (
    DRAW_ASPECT_RATIO,
    IMAGE_MODELS,
    IMAGE_SIZES,
    THINKING_LEVELS,
    ImageModel,
    ImageOptionError,
    ImageOptions,
    find_image_model,
    generate_image,
    resolve_image_model,
)
from src.utils.async_utils import safe_run
from src.utils.discord_error_utils import best_effort_typing, safe_defer

logger = logging.getLogger(__name__)

DISCORD_MESSAGE_LIMIT = 2000

MODEL_CHOICES = [Choice(name=m.name, value=m.id) for m in IMAGE_MODELS.values()]
ASPECT_RATIO_CHOICES = [
    Choice(name=name, value=name.split()[0]) for name in (
        "16:9 landscape (/draw default)", "9:16 portrait", "1:1 square", "4:3", "3:4", "3:2", "2:3",
        "5:4", "4:5", "21:9 ultrawide", "4:1 banner (banana-2.1)", "1:4 tall (banana-2.1)",
        "8:1 strip (banana-2.1)", "1:8 tall strip (banana-2.1)",
    )
]
SIZE_CHOICES = [Choice(name=f"{s} (banana-2.1, banana-pro)" if s != "1K" else "1K (default)", value=s)
                for s in IMAGE_SIZES]
THINKING_CHOICES = [Choice(name=f"{level} (banana-2.1)", value=level) for level in THINKING_LEVELS]
SEARCH_CHOICES = [
    Choice(name="web (banana-2.1, banana-pro)", value="web"),
    Choice(name="web + images (banana-2.1)", value="web+image"),
]

OPTION_DESCRIPTIONS = {
    "model": "Image model (default: this channel's, see /imagemodel)",
    "aspect_ratio": "Shape of the image",
    "size": "Resolution: 1K, 2K or 4K (bigger costs more)",
    "thinking": "How hard the model plans the image first",
    "search": "Ground the image in Google search results (for real places, people, events)",
}


class ImageCommands:
    """Handles image-related Discord commands."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        state_service,
        message_service,
        gemini_service,
        default_image_model: str,
    ):
        self.bot = bot
        self._state_service = state_service
        self._message_service = message_service
        self._gemini_service = gemini_service
        self._default_image_model = default_image_model

    def setup_commands(self) -> None:
        """Set up all image-related commands."""
        self.bot.tree.add_command(self._create_draw_command())
        self.bot.tree.add_command(self._create_edit_command())
        self.bot.tree.add_command(self._create_imagemodel_command())

    def _create_draw_command(self) -> app_commands.Command:
        """Create the /draw command."""

        @app_commands.command(name="draw", description="Generate an image from a prompt")
        @app_commands.describe(prompt="Prompt for image generation", image1="Optional reference image",
                               **OPTION_DESCRIPTIONS)
        @app_commands.choices(model=MODEL_CHOICES, aspect_ratio=ASPECT_RATIO_CHOICES, size=SIZE_CHOICES,
                              thinking=THINKING_CHOICES, search=SEARCH_CHOICES)
        async def draw(
            interaction: discord.Interaction,
            prompt: str,
            aspect_ratio: Optional[str] = None,
            size: Optional[str] = None,
            model: Optional[str] = None,
            thinking: Optional[str] = None,
            search: Optional[str] = None,
            image1: Optional[discord.Attachment] = None,
            image2: Optional[discord.Attachment] = None,
            image3: Optional[discord.Attachment] = None,
            image4: Optional[discord.Attachment] = None,
            image5: Optional[discord.Attachment] = None,
            image6: Optional[discord.Attachment] = None,
            image7: Optional[discord.Attachment] = None,
            image8: Optional[discord.Attachment] = None,
        ):
            if not await safe_defer(interaction, ephemeral=False, thinking=True):
                return
            images = [i for i in (image1, image2, image3, image4, image5, image6, image7, image8) if i]
            options = ImageOptions(aspect_ratio=aspect_ratio or DRAW_ASPECT_RATIO, size=size,
                                   thinking=thinking, search=search)
            await safe_run(interaction, self._handle_image_command, interaction, prompt, images, model, options,
                           edit=False)

        return draw

    def _create_edit_command(self) -> app_commands.Command:
        """Create the /edit command."""

        @app_commands.command(name="edit", description="Edit an image with a prompt")
        @app_commands.describe(prompt="What to change", edit_image="Image to edit",
                               image2="Optional reference image", **OPTION_DESCRIPTIONS)
        @app_commands.choices(model=MODEL_CHOICES, aspect_ratio=ASPECT_RATIO_CHOICES, size=SIZE_CHOICES,
                              thinking=THINKING_CHOICES, search=SEARCH_CHOICES)
        async def edit(
            interaction: discord.Interaction,
            prompt: str,
            edit_image: discord.Attachment,
            aspect_ratio: Optional[str] = None,
            size: Optional[str] = None,
            model: Optional[str] = None,
            thinking: Optional[str] = None,
            search: Optional[str] = None,
            image2: Optional[discord.Attachment] = None,
            image3: Optional[discord.Attachment] = None,
            image4: Optional[discord.Attachment] = None,
            image5: Optional[discord.Attachment] = None,
            image6: Optional[discord.Attachment] = None,
            image7: Optional[discord.Attachment] = None,
            image8: Optional[discord.Attachment] = None,
        ):
            if not await safe_defer(interaction, ephemeral=False, thinking=True):
                return
            images = [i for i in (edit_image, image2, image3, image4, image5, image6, image7, image8) if i]
            options = ImageOptions(aspect_ratio=aspect_ratio, size=size, thinking=thinking, search=search)
            await safe_run(interaction, self._handle_image_command, interaction, prompt, images, model, options,
                           edit=True)

        return edit

    async def _handle_image_command(
        self,
        interaction: discord.Interaction,
        prompt: str,
        attachments: List[discord.Attachment],
        model: Optional[str],
        options: ImageOptions,
        *,
        edit: bool,
    ) -> None:
        """Handle /draw and /edit. For /edit the first attachment is the image to edit."""
        command = "/edit" if edit else "/draw"
        channel_id = interaction.channel.id
        self._state_service.mark_channel_active(channel_id)
        active_model = resolve_image_model(self._state_service, channel_id, self._default_image_model, model)
        logger.info("[%s] Channel %s Model: %s Options: %s Images: %s Prompt: %s", command, channel_id, active_model.id, options.describe() or 'defaults', len(attachments), prompt)

        async with best_effort_typing(interaction.channel):
            try:
                images_b64 = [base64.b64encode(await a.read()).decode("ascii") for a in attachments]
                result = await generate_image(
                    prompt=prompt,
                    model=active_model,
                    gemini_service=self._gemini_service,
                    images_b64=images_b64,
                    options=options,
                )
            except (ImageOptionError, GeminiServiceError, APIError) as e:
                logger.error("Image error in %s: %s", command, e)
                await self._send_error(interaction, prompt, str(e))
                return
            except Exception as e:
                logger.exception("Unexpected error in %s: %s", command, e)
                await self._send_error(interaction, prompt, "Something unexpected went wrong. Please try again later.")
                return

        logger.info("[%s] Channel %s: image generated", command, channel_id)
        filename = "edited.png" if edit else "image.png"
        header = _result_header(result.cost, active_model, options, prompt)
        if edit:
            header = self._message_service.format_attachment_message(attachments, header)
        else:
            header = self._message_service.format_prompt_message(header)
        await interaction.followup.send(
            content=_with_model_text(header, result.text),
            file=discord.File(io.BytesIO(result.image), filename=filename),
        )

    async def _send_error(self, interaction: discord.Interaction, prompt: str, error: str) -> None:
        content = f"{self._message_service.format_prompt_message(prompt)}\n\nSorry, I couldn't make that image: {error}"
        try:
            await interaction.followup.send(content=content[:DISCORD_MESSAGE_LIMIT])
        except Exception as discord_error:
            logger.error("Failed to send image error to Discord: %s", discord_error)

    def _create_imagemodel_command(self) -> app_commands.Command:
        """Create the /imagemodel command."""

        @app_commands.command(name="imagemodel",
                              description="View or set this channel's image model (for /draw, /edit and the bot)")
        @app_commands.describe(model="Model to use")
        @app_commands.choices(model=MODEL_CHOICES)
        async def imagemodel_command(interaction: discord.Interaction, model: Optional[str] = None):
            if not await safe_defer(interaction, ephemeral=False, thinking=True):
                return
            await safe_run(interaction, self._handle_imagemodel_command, interaction, model)

        return imagemodel_command

    async def _handle_imagemodel_command(self, interaction: discord.Interaction,
                                         model: Optional[str] = None) -> None:
        """Handle the /imagemodel command."""
        channel_id = interaction.channel.id
        self._state_service.mark_channel_active(channel_id)

        chosen = find_image_model(model)
        if chosen:
            self._state_service.set_image_model(channel_id, chosen.id)
            logger.info("[/imagemodel] Channel %s: image model set to %s", channel_id, chosen.id)
            await interaction.followup.send(f"Image model set to `{chosen.name}`.", ephemeral=True)
        else:
            current = resolve_image_model(self._state_service, channel_id, self._default_image_model)
            await interaction.followup.send(f"Image model is `{current.name}`.", ephemeral=True)


def _result_header(cost: float, model: ImageModel, options: ImageOptions, prompt: str) -> str:
    """``[$0.034 @ banana-2.1, 9:16, 2K] prompt``."""
    details = ", ".join(p for p in (model.name, options.describe()) if p)
    return f"[${cost:.3f} @ {details}] {prompt}"


def _with_model_text(header: str, text: str) -> str:
    """The header, then what the model said (cut to fit a Discord message)."""
    header = header[:DISCORD_MESSAGE_LIMIT]
    if not text:
        return header
    room = DISCORD_MESSAGE_LIMIT - len(header) - 2
    if room < 20:
        return header
    if len(text) > room:
        text = text[:room - 1] + "…"
    return f"{header}\n\n{text}"
