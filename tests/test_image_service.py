"""Tests for the Gemini image models: option checks, model lookup, and the GenerateContent call."""

import base64
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from google.genai import types

from src.services.gemini_service import GeminiService, GeminiServiceError
from src.services.image_service import (
    IMAGE_MODELS,
    ImageOptionError,
    ImageOptions,
    check_options,
    generate_image,
    resolve_image_model,
)

BANANA = IMAGE_MODELS["gemini-nano-banana-2.1"]
PRO = IMAGE_MODELS["gemini-3-pro-image"]
LITE = IMAGE_MODELS["gemini-3.1-flash-lite-image"]


class TestOptions(unittest.TestCase):
    def test_banana_takes_every_option(self):
        check_options(BANANA, ImageOptions(aspect_ratio="1:8", size="4K", thinking="medium", search="web+image"))

    def test_each_model_rejects_what_it_cant_do(self):
        cases = [
            (PRO, ImageOptions(aspect_ratio="8:1"), "banana-pro doesn't support aspect ratio 8:1"),
            (PRO, ImageOptions(thinking="high"), "banana-pro doesn't take a thinking level."),
            (PRO, ImageOptions(search="web+image"), "banana-pro doesn't support search web+image; it takes web."),
            (LITE, ImageOptions(size="2K"), "banana-2-lite doesn't support size 2K; it takes 1K."),
            (LITE, ImageOptions(thinking="medium"), "banana-2-lite doesn't support thinking level medium"),
            (LITE, ImageOptions(search="web"), "banana-2-lite doesn't take a search."),
        ]
        for model, options, message in cases:
            with self.subTest(model=model.name, options=options):
                with self.assertRaises(ImageOptionError) as ctx:
                    check_options(model, options)
                self.assertIn(message, str(ctx.exception))

    def test_describe_lists_only_what_was_set(self):
        self.assertEqual(ImageOptions().describe(), "")
        self.assertEqual(ImageOptions(aspect_ratio="9:16", thinking="high", search="web").describe(),
                         "9:16, thinking high, search web")


class TestResolve(unittest.TestCase):
    def state(self, saved):
        state_service = MagicMock()
        state_service.get_image_model.return_value = saved
        return state_service

    def test_given_model_wins_then_channel_then_default(self):
        self.assertIs(resolve_image_model(self.state("gemini-3-pro-image"), 1, None, "banana-2-lite"), LITE)
        self.assertIs(resolve_image_model(self.state("gemini-3-pro-image"), 1, None), PRO)
        self.assertIs(resolve_image_model(self.state(None), 1, "gemini-3.1-flash-lite-image"), LITE)

    def test_retired_models_are_skipped(self):
        self.assertIs(resolve_image_model(self.state("seedream"), 1, "seedream"), BANANA)


class TestGenerateImage(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_bad_options_before_calling_gemini(self):
        gemini_service = MagicMock()
        gemini_service.generate_image = AsyncMock()
        with self.assertRaises(ImageOptionError):
            await generate_image(prompt="x", model=PRO, gemini_service=gemini_service,
                                 options=ImageOptions(thinking="high"))
        gemini_service.generate_image.assert_not_awaited()

    async def test_caps_reference_images(self):
        gemini_service = MagicMock()
        gemini_service.generate_image = AsyncMock()
        await generate_image(prompt="x", model=BANANA, gemini_service=gemini_service, images_b64=["a"] * 20)
        self.assertEqual(len(gemini_service.generate_image.await_args.kwargs["images_b64"]), 14)


def _part(text=None, data=None, thought=None):
    return SimpleNamespace(text=text, thought=thought,
                           inline_data=SimpleNamespace(data=data) if data else None)


def _response(parts, usage=None):
    return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=parts))], usage_metadata=usage)


class TestGeminiImageCall(unittest.IsolatedAsyncioTestCase):
    def make_service(self, response):
        service = GeminiService(api_key="test-key")
        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(return_value=response)
        service._client = client
        return service, client.aio.models.generate_content

    @patch("src.services.gemini_service.record_cost", new_callable=AsyncMock)
    async def test_sends_options_and_skips_draft_images(self, record_cost):
        usage = SimpleNamespace(
            prompt_token_count=1000, candidates_token_count=1200, thoughts_token_count=100,
            candidates_tokens_details=[
                SimpleNamespace(modality=types.MediaModality.IMAGE, token_count=1120),
                SimpleNamespace(modality=types.MediaModality.TEXT, token_count=80),
            ],
        )
        service, generate = self.make_service(_response([
            _part(data=b"draft", thought=True),
            _part(text="thinking out loud", thought=True),
            _part(text=" Here you go. "),
            _part(data=b"final"),
        ], usage))
        png = base64.b64encode(b"\x89PNG rest").decode()

        result = await service.generate_image(
            "a cat", BANANA, [png],
            ImageOptions(aspect_ratio="9:16", size="2K", thinking="high", search="web+image"))

        self.assertEqual((result.image, result.text), (b"final", "Here you go."))
        expected_cost = (1000 * 1.50 + 1120 * 30.0 + 180 * 7.50) / 1_000_000
        self.assertAlmostEqual(result.cost, expected_cost)
        record_cost.assert_awaited_once()
        kwargs = generate.await_args.kwargs
        self.assertEqual(kwargs["model"], "gemini-nano-banana-2.1")
        self.assertEqual(kwargs["contents"][0].inline_data.mime_type, "image/png")
        config = kwargs["config"]
        self.assertEqual((config.image_config.aspect_ratio, config.image_config.image_size), ("9:16", "2K"))
        self.assertEqual(config.thinking_config.thinking_level, types.ThinkingLevel.HIGH)
        self.assertIsNotNone(config.tools[0].google_search.search_types.image_search)
        self.assertEqual(config.response_modalities, ["TEXT", "IMAGE"])

    @patch("src.services.gemini_service.record_cost", new_callable=AsyncMock)
    async def test_defaults_leave_config_unset(self, _record_cost):
        service, generate = self.make_service(_response([_part(data=b"img")]))

        result = await service.generate_image("a cat", PRO)

        self.assertEqual((result.image, result.text, result.cost), (b"img", "", 0.0))
        config = generate.await_args.kwargs["config"]
        self.assertIsNone(config.image_config.aspect_ratio)
        self.assertIsNone(config.thinking_config)
        self.assertIsNone(config.tools)

    async def test_no_image_is_an_error(self):
        service, _ = self.make_service(_response([_part(text="I can't draw that.")]))
        with self.assertRaises(GeminiServiceError):
            await service.generate_image("a cat", BANANA)


if __name__ == "__main__":
    unittest.main()
