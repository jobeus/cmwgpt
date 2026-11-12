"""Tests for ImageCommands with injected dependencies."""

import base64
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.bot.commands.image import DISCORD_MESSAGE_LIMIT, ImageCommands
from src.services.gemini_service import GeminiServiceError
from src.services.image_service import IMAGE_MODELS, ImageOptions, ImageResult


class _AsyncTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _attachment(data: bytes, url: str = "https://cdn.example/a.png"):
    attachment = MagicMock()
    attachment.read = AsyncMock(return_value=data)
    attachment.url = url
    return attachment


class TestImageCommands(unittest.IsolatedAsyncioTestCase):
    def make_commands(self, *, saved_model=None, result=None, error=None):
        state_service = MagicMock()
        state_service.get_image_model.return_value = saved_model
        message_service = MagicMock()
        gemini_service = MagicMock()
        gemini_service.generate_image = AsyncMock(
            return_value=result or ImageResult(image=b"png-bytes", text="", cost=0.1234), side_effect=error)
        interaction = MagicMock()
        interaction.channel.id = 321
        interaction.channel.typing.return_value = _AsyncTyping()
        interaction.user.id = 654
        interaction.followup.send = AsyncMock()
        commands = ImageCommands(
            MagicMock(),
            state_service=state_service,
            message_service=message_service,
            gemini_service=gemini_service,
            default_image_model="gemini-nano-banana-2.1",
        )
        return commands, state_service, gemini_service, interaction

    async def test_draw_uses_channel_model_and_options(self):
        commands, state_service, gemini_service, interaction = self.make_commands(saved_model="gemini-3-pro-image")
        ref = _attachment(b"ref")

        await commands._handle_image_command(
            interaction, "a cat", [ref], None, ImageOptions(aspect_ratio="9:16", size="2K"), edit=False)

        state_service.mark_channel_active.assert_called_once_with(321)
        gemini_service.generate_image.assert_awaited_once_with(
            prompt="a cat",
            model=IMAGE_MODELS["gemini-3-pro-image"],
            images_b64=[base64.b64encode(b"ref").decode("ascii")],
            options=ImageOptions(aspect_ratio="9:16", size="2K"),
        )
        kwargs = interaction.followup.send.await_args.kwargs
        self.assertEqual(kwargs["content"], "> [$0.123 @ banana-pro, 9:16, 2K] a cat")
        self.assertEqual(kwargs["file"].filename, "image.png")

    async def test_draw_skips_unknown_saved_model(self):
        commands, _, gemini_service, interaction = self.make_commands(saved_model="seedream")

        await commands._handle_image_command(interaction, "a cat", [], None, ImageOptions(), edit=False)

        self.assertEqual(gemini_service.generate_image.await_args.kwargs["model"].id, "gemini-nano-banana-2.1")

    async def test_draw_posts_what_the_model_said(self):
        commands, _, _, interaction = self.make_commands(
            result=ImageResult(image=b"png", text="Here's your cat.", cost=0.05))

        await commands._handle_image_command(interaction, "a cat", [], None, ImageOptions(), edit=False)

        self.assertEqual(interaction.followup.send.await_args.kwargs["content"],
                         "> [$0.050 @ banana-2.1] a cat\n\nHere's your cat.")

    async def test_long_model_text_is_cut_to_fit(self):
        commands, _, _, interaction = self.make_commands(
            result=ImageResult(image=b"png", text="x" * 5000, cost=0.05))

        await commands._handle_image_command(interaction, "a cat", [], None, ImageOptions(), edit=False)

        content = interaction.followup.send.await_args.kwargs["content"]
        self.assertEqual(len(content), DISCORD_MESSAGE_LIMIT)
        self.assertTrue(content.endswith("…"))

    async def test_edit_sends_every_image_with_the_edited_one_first(self):
        commands, _, gemini_service, interaction = self.make_commands()
        first, second = _attachment(b"one", "https://cdn.example/1.png"), _attachment(b"two", "https://cdn.example/2.png")

        await commands._handle_image_command(
            interaction, "make it red", [first, second], "gemini-3.1-flash-lite-image", ImageOptions(), edit=True)

        kwargs = gemini_service.generate_image.await_args.kwargs
        self.assertEqual(kwargs["model"].id, "gemini-3.1-flash-lite-image")
        self.assertEqual(kwargs["images_b64"], [base64.b64encode(b"one").decode("ascii"),
                                                base64.b64encode(b"two").decode("ascii")])
        sent = interaction.followup.send.await_args.kwargs
        self.assertEqual(sent["content"],
                         "> https://cdn.example/1.png\n> https://cdn.example/2.png\n"
                         "> [$0.123 @ banana-2-lite] make it red")
        self.assertEqual(sent["file"].filename, "edited.png")

    async def test_unsupported_option_is_reported_without_calling_the_model(self):
        commands, _, gemini_service, interaction = self.make_commands()

        await commands._handle_image_command(
            interaction, "a cat", [], "gemini-3-pro-image", ImageOptions(thinking="high"), edit=False)

        gemini_service.generate_image.assert_not_awaited()
        self.assertEqual(interaction.followup.send.await_args.kwargs["content"],
                         "> a cat\n\nSorry, I couldn't make that image: banana-pro doesn't take a thinking level.")

    async def test_gemini_errors_are_reported(self):
        commands, _, _, interaction = self.make_commands(error=GeminiServiceError("No image returned from Gemini."))

        await commands._handle_image_command(interaction, "a cat", [], None, ImageOptions(), edit=False)

        self.assertIn("No image returned from Gemini.", interaction.followup.send.await_args.kwargs["content"])

    async def test_unexpected_errors_get_a_generic_message(self):
        commands, _, _, interaction = self.make_commands(error=RuntimeError("secret internals"))

        await commands._handle_image_command(interaction, "a cat", [], None, ImageOptions(), edit=False)

        content = interaction.followup.send.await_args.kwargs["content"]
        self.assertNotIn("secret internals", content)
        self.assertIn("Something unexpected went wrong", content)

    async def test_error_send_failure_is_swallowed(self):
        commands, _, _, interaction = self.make_commands(error=GeminiServiceError("boom"))
        interaction.followup.send.side_effect = RuntimeError("discord down")

        await commands._handle_image_command(interaction, "a cat", [], None, ImageOptions(), edit=False)

        interaction.followup.send.assert_awaited_once()

    async def test_imagemodel_sets_model(self):
        commands, state_service, _, interaction = self.make_commands()

        await commands._handle_imagemodel_command(interaction, "gemini-3-pro-image")

        state_service.set_image_model.assert_called_once_with(321, "gemini-3-pro-image")
        interaction.followup.send.assert_awaited_once_with("Image model set to `banana-pro`.", ephemeral=True)

    async def test_imagemodel_reports_current_model(self):
        commands, state_service, _, interaction = self.make_commands(saved_model="qwen")

        await commands._handle_imagemodel_command(interaction)

        state_service.set_image_model.assert_not_called()
        interaction.followup.send.assert_awaited_once_with("Image model is `banana-2.1`.", ephemeral=True)

    def test_setup_commands_registers_all_image_commands(self):
        commands, _, _, _ = self.make_commands()

        commands.setup_commands()

        names = [c.args[0].name for c in commands.bot.tree.add_command.call_args_list]
        self.assertEqual(names, ["draw", "edit", "imagemodel"])

    def test_model_choices_are_the_gemini_models(self):
        commands, _, _, _ = self.make_commands()
        model = next(p for p in commands._create_draw_command().parameters if p.name == "model")

        self.assertEqual([c.value for c in model.choices], list(IMAGE_MODELS))

    async def test_draw_wrapper_defaults_to_widescreen_and_collects_images(self):
        commands, _, _, interaction = self.make_commands()
        interaction.response.defer = AsyncMock()
        image = MagicMock()
        draw = commands._create_draw_command()

        with patch("src.bot.commands.image.safe_run", new=AsyncMock()) as mock_safe_run:
            await draw.callback(interaction, "a prompt", None, "4K", None, None, "web", None, image)

        interaction.response.defer.assert_awaited_once_with(ephemeral=False, thinking=True)
        mock_safe_run.assert_awaited_once_with(
            interaction, commands._handle_image_command, interaction, "a prompt", [image], None,
            ImageOptions(aspect_ratio="16:9", size="4K", search="web"), edit=False)

    async def test_edit_wrapper_keeps_the_image_shape(self):
        commands, _, _, interaction = self.make_commands()
        interaction.response.defer = AsyncMock()
        edit_image, extra = MagicMock(), MagicMock()
        edit = commands._create_edit_command()

        with patch("src.bot.commands.image.safe_run", new=AsyncMock()) as mock_safe_run:
            await edit.callback(interaction, "fix it", edit_image, None, None, "gemini-3-pro-image", None, None, extra)

        mock_safe_run.assert_awaited_once_with(
            interaction, commands._handle_image_command, interaction, "fix it", [edit_image, extra],
            "gemini-3-pro-image", ImageOptions(), edit=True)

    async def test_imagemodel_wrapper_defers_and_awaits_safe_run(self):
        commands, _, _, interaction = self.make_commands()
        interaction.response.defer = AsyncMock()

        with patch("src.bot.commands.image.safe_run", new=AsyncMock()) as mock_safe_run:
            await commands._create_imagemodel_command().callback(interaction, "gemini-3-pro-image")

        mock_safe_run.assert_awaited_once_with(
            interaction, commands._handle_imagemodel_command, interaction, "gemini-3-pro-image")


if __name__ == "__main__":
    unittest.main()
