"""Tests for GeminiService content conversion, cost estimation, and response handling."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.gemini_service import (
    GeminiService,
    GeminiServiceError,
    is_gemini_model,
    get_thinking_level,
    PRICE_INPUT_PER_M,
    PRICE_OUTPUT_PER_M,
    PRICE_CACHED_PER_M,
)


class TestGeminiModelHelpers(unittest.TestCase):
    def test_is_gemini_model_true(self):
        self.assertTrue(is_gemini_model("google"))
        self.assertTrue(is_gemini_model("google-high"))

    def test_is_gemini_model_false(self):
        self.assertFalse(is_gemini_model("gpt-4"))
        self.assertFalse(is_gemini_model("anthropic/claude-3"))
        self.assertFalse(is_gemini_model("google/gemini-2"))

    def test_get_thinking_level(self):
        self.assertEqual(get_thinking_level("google-high"), "high")
        self.assertIsNone(get_thinking_level("google"))
        self.assertIsNone(get_thinking_level("gpt-4"))


class TestGeminiService(unittest.IsolatedAsyncioTestCase):
    def make_service(self):
        service = GeminiService(api_key="test-key")
        return service

    # ── Content conversion ──

    def test_convert_simple_text_messages(self):
        service = self.make_service()
        messages = [
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
        ]
        result = service._convert_messages_to_input(messages, "Be helpful.")

        # System prompt becomes first user+model pair
        self.assertEqual(result[0]["role"], "user")
        self.assertIn("Be helpful.", result[0]["content"])
        self.assertEqual(result[1]["role"], "model")

        # Then the actual messages
        self.assertEqual(result[2]["role"], "user")
        self.assertEqual(result[2]["content"], "Hello!")
        self.assertEqual(result[3]["role"], "model")  # assistant -> model
        self.assertEqual(result[3]["content"], "Hi there!")
        self.assertEqual(result[4]["role"], "user")
        self.assertEqual(result[4]["content"], "How are you?")

    def test_convert_multimodal_messages(self):
        service = self.make_service()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is this?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ],
            }
        ]
        result = service._convert_messages_to_input(messages, "")

        # No system prompt pair when empty
        user_turn = result[0]
        self.assertEqual(user_turn["role"], "user")
        self.assertEqual(len(user_turn["content"]), 2)
        self.assertEqual(user_turn["content"][0]["type"], "text")
        self.assertEqual(user_turn["content"][1]["type"], "image")
        self.assertEqual(user_turn["content"][1]["data"], "AAAA")
        self.assertEqual(user_turn["content"][1]["mime_type"], "image/png")

    def test_convert_remote_image_url(self):
        service = self.make_service()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/img.jpg"}},
                ],
            }
        ]
        result = service._convert_messages_to_input(messages, "")
        img_part = result[0]["content"][1]
        self.assertEqual(img_part["type"], "image")
        self.assertEqual(img_part["uri"], "https://example.com/img.jpg")

    def test_convert_skips_system_role_messages(self):
        service = self.make_service()
        messages = [
            {"role": "system", "content": "You are a bot"},
            {"role": "user", "content": "Hello"},
        ]
        result = service._convert_messages_to_input(messages, "System prompt")

        # System role message is skipped, only system prompt pair + user msg
        roles = [r["role"] for r in result]
        self.assertEqual(roles, ["user", "model", "user"])

    def test_convert_skips_empty_messages(self):
        service = self.make_service()
        messages = [
            {"role": "user", "content": ""},
            {"role": "user", "content": "Hello"},
        ]
        result = service._convert_messages_to_input(messages, "")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], "Hello")

    # ── Cost estimation ──

    def test_cost_estimation_basic(self):
        service = self.make_service()
        usage = SimpleNamespace(
            total_input_tokens=1000,
            total_output_tokens=500,
            total_thought_tokens=200,
            total_cached_tokens=0,
        )
        cost = service._estimate_cost(usage)
        expected = (
            1000 * PRICE_INPUT_PER_M / 1_000_000
            + (500 + 200) * PRICE_OUTPUT_PER_M / 1_000_000
            + 0
        )
        self.assertAlmostEqual(cost, expected)

    def test_cost_estimation_with_cached(self):
        service = self.make_service()
        usage = SimpleNamespace(
            total_input_tokens=500,
            total_output_tokens=100,
            total_thought_tokens=0,
            total_cached_tokens=5000,
        )
        cost = service._estimate_cost(usage)
        expected = (
            500 * PRICE_INPUT_PER_M / 1_000_000
            + 100 * PRICE_OUTPUT_PER_M / 1_000_000
            + 5000 * PRICE_CACHED_PER_M / 1_000_000
        )
        self.assertAlmostEqual(cost, expected)

    def test_cost_estimation_none_usage(self):
        service = self.make_service()
        self.assertEqual(service._estimate_cost(None), 0.0)

    def test_cost_estimation_dict_usage(self):
        service = self.make_service()
        usage = {
            "total_input_tokens": 100,
            "total_output_tokens": 50,
            "total_thought_tokens": 10,
            "total_cached_tokens": 0,
        }
        cost = service._estimate_cost(usage)
        self.assertGreater(cost, 0.0)

    # ── get_chat_completion ──

    async def test_get_chat_completion_text_response(self):
        service = self.make_service()
        text_output = SimpleNamespace(type="text", text="Hello from Gemini!")
        thought_output = SimpleNamespace(type="thought", summary="I thought about it")
        usage = SimpleNamespace(
            total_input_tokens=10,
            total_output_tokens=5,
            total_thought_tokens=3,
            total_cached_tokens=0,
        )
        interaction = SimpleNamespace(outputs=[thought_output, text_output], usage=usage, id="test-interaction-123")

        mock_create = AsyncMock(return_value=interaction)
        mock_client = MagicMock()
        mock_client.aio.interactions.create = mock_create
        service._client = mock_client

        result, _ = await service.get_chat_completion(
            model="google",
            messages=[{"role": "user", "content": "Hi"}],
            system_prompt="Be helpful",
            channel_id=123,
            discord_user_id=456,
        )

        self.assertIsInstance(result, str)
        self.assertIn("Hello from Gemini!", result)
        mock_create.assert_awaited_once()
        call_kwargs = mock_create.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "gemini-3.1-flash-lite-preview")
        self.assertEqual(call_kwargs["store"], True)
        self.assertIn({"type": "google_search"}, call_kwargs["tools"])

    async def test_get_chat_completion_with_thinking_level(self):
        service = self.make_service()
        text_output = SimpleNamespace(type="text", text="Deep answer")
        interaction = SimpleNamespace(
            outputs=[text_output],
            usage=SimpleNamespace(total_input_tokens=5, total_output_tokens=10, total_thought_tokens=50, total_cached_tokens=0),
            id="test-thinking-456",
        )
        mock_create = AsyncMock(return_value=interaction)
        mock_client = MagicMock()
        mock_client.aio.interactions.create = mock_create
        service._client = mock_client

        await service.get_chat_completion(
            model="google-high",
            messages=[{"role": "user", "content": "Think hard"}],
            system_prompt="",
            thinking_level="high",
        )

        call_kwargs = mock_create.call_args.kwargs
        self.assertEqual(call_kwargs["generation_config"]["thinking_level"], "high")

    async def test_get_chat_completion_image_response(self):
        import base64

        service = self.make_service()
        img_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        img_b64 = base64.b64encode(img_bytes).decode()
        text_output = SimpleNamespace(type="text", text="Here's an image")
        image_output = SimpleNamespace(type="image", data=img_b64, mime_type="image/png")
        interaction = SimpleNamespace(
            outputs=[text_output, image_output],
            usage=SimpleNamespace(total_input_tokens=10, total_output_tokens=20, total_thought_tokens=0, total_cached_tokens=0),
            id="test-image-789",
        )
        mock_create = AsyncMock(return_value=interaction)
        mock_client = MagicMock()
        mock_client.aio.interactions.create = mock_create
        service._client = mock_client

        result, _ = await service.get_chat_completion(
            model="google",
            messages=[{"role": "user", "content": "Draw something"}],
            system_prompt="",
        )

        self.assertIsInstance(result, dict)
        self.assertIn("text", result)
        self.assertIn("files", result)
        self.assertEqual(len(result["files"]), 1)
        self.assertEqual(result["files"][0].filename, "gemini_output.png")

    async def test_get_chat_completion_empty_response_raises(self):
        service = self.make_service()
        # Response with only a thought, no text
        thought_output = SimpleNamespace(type="thought", summary="just thinking")
        interaction = SimpleNamespace(
            outputs=[thought_output],
            usage=SimpleNamespace(total_input_tokens=5, total_output_tokens=0, total_thought_tokens=10, total_cached_tokens=0),
            id="test-empty-000",
        )
        mock_create = AsyncMock(return_value=interaction)
        mock_client = MagicMock()
        mock_client.aio.interactions.create = mock_create
        service._client = mock_client

        with self.assertRaises(GeminiServiceError):
            await service.get_chat_completion(
                model="google",
                messages=[{"role": "user", "content": "Hi"}],
                system_prompt="",
            )

    async def test_get_chat_completion_google_search_output_skipped(self):
        service = self.make_service()
        search_output = SimpleNamespace(type="google_search_result")
        text_output = SimpleNamespace(type="text", text="The answer is 42")
        interaction = SimpleNamespace(
            outputs=[search_output, text_output],
            usage=SimpleNamespace(total_input_tokens=10, total_output_tokens=5, total_thought_tokens=0, total_cached_tokens=0),
            id="test-search-111",
        )
        mock_create = AsyncMock(return_value=interaction)
        mock_client = MagicMock()
        mock_client.aio.interactions.create = mock_create
        service._client = mock_client

        result, _ = await service.get_chat_completion(
            model="google",
            messages=[{"role": "user", "content": "What is?"}],
            system_prompt="",
        )

        self.assertIn("The answer is 42", result)
        # Should NOT include google_search_result in text
        self.assertNotIn("google_search_result", result)


if __name__ == "__main__":
    unittest.main()
