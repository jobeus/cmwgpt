"""Tests for GeminiService content conversion, cost estimation, and response handling."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from google.genai import errors as genai_errors

from src.services.gemini_service import (
    REQUEST_TIMEOUT_SECONDS,
    GeminiService,
    GeminiServiceError,
    _is_retryable,
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


def _api_error(code):
    return genai_errors.APIError(code, {"error": {"message": f"err {code}", "status": "X"}})


def _ok_interaction(text="ok"):
    return SimpleNamespace(
        outputs=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(total_input_tokens=1, total_output_tokens=1, total_thought_tokens=0, total_cached_tokens=0),
        id="interaction-ok",
    )


class TestIsRetryable(unittest.TestCase):
    def test_api_errors_retry_only_on_429_and_5xx(self):
        self.assertTrue(_is_retryable(_api_error(429)))
        self.assertTrue(_is_retryable(_api_error(500)))
        self.assertTrue(_is_retryable(_api_error(503)))
        self.assertFalse(_is_retryable(_api_error(400)))
        self.assertFalse(_is_retryable(_api_error(403)))

    def test_transport_timeouts_and_connection_errors_are_retryable(self):
        request = httpx.Request("POST", "https://example.test")
        self.assertTrue(_is_retryable(httpx.ConnectError("down", request=request)))
        self.assertTrue(_is_retryable(httpx.ReadTimeout("slow", request=request)))
        self.assertTrue(_is_retryable(asyncio.TimeoutError()))
        self.assertTrue(_is_retryable(ConnectionResetError()))

    def test_other_errors_are_not_retryable_even_if_message_looks_transient(self):
        self.assertFalse(_is_retryable(RuntimeError("503 Service Unavailable")))
        self.assertFalse(_is_retryable(ValueError("429 rate limit")))


class TestGeminiService(unittest.IsolatedAsyncioTestCase):
    def make_service(self):
        service = GeminiService(api_key="test-key")
        return service

    # ── Content conversion (Interactions API _build_input) ──

    def test_build_input_simple_text_messages(self):
        service = self.make_service()
        messages = [
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
        ]
        # No cached interaction -> has_prev_interaction = False
        result = service._build_input(messages, has_prev_interaction=False)

        # Expected: single consolidated text part containing full transcript in order,
        # with NO missed context wrapper because has_prev_interaction=False.
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "text")
        self.assertEqual(
            result[0]["text"],
            "User: Hello!\n\nAssistant: Hi there!\n\nUser: How are you?"
        )

    def test_build_input_simple_text_messages_with_prev_interaction(self):
        service = self.make_service()
        messages = [
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
            {"role": "user", "content": "Are you there?"},
        ]
        # Cached interaction exists -> has_prev_interaction = True
        result = service._build_input(messages, has_prev_interaction=True)

        # Expected: single consolidated text part containing the missed context wrapper
        # around the messages sent since the last bot response.
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "text")
        expected_text = (
            "The following messages occurred since your last response:\n\n"
            "User: How are you?\n\n"
            "--- End missed context ---\n\n"
            "User: Are you there?"
        )
        self.assertEqual(result[0]["text"], expected_text)

    def test_build_input_multimodal_messages(self):
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
        result = service._build_input(messages, has_prev_interaction=False)

        # Only one message, so no history transcript
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["type"], "text")
        self.assertEqual(result[0]["text"], "User: What is this?")
        self.assertEqual(result[1]["type"], "image")
        self.assertEqual(result[1]["data"], "AAAA")
        self.assertEqual(result[1]["mime_type"], "image/png")

    def test_build_input_remote_image_url(self):
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
        result = service._build_input(messages, has_prev_interaction=False)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]["type"], "image")
        self.assertEqual(result[1]["uri"], "https://example.com/img.jpg")

    def test_build_input_history_multimodal(self):
        service = self.make_service()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Look at this:"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ],
            },
            {
                "role": "assistant",
                "content": "A nice image!",
            },
            {
                "role": "user",
                "content": "Indeed.",
            }
        ]
        result = service._build_input(messages, has_prev_interaction=False)
        
        # Verify that the image in history is preserved as a native image part!
        image_parts = [p for p in result if p["type"] == "image"]
        self.assertEqual(len(image_parts), 1)
        self.assertEqual(image_parts[0]["data"], "AAAA")

    def test_build_input_audio_messages(self):
        service = self.make_service()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Listen to this:"},
                    {"type": "audio_url", "audio_url": {"url": "data:audio/ogg;base64,AAAA"}},
                ],
            }
        ]
        result = service._build_input(messages, has_prev_interaction=False)

        # Only one message, so no history transcript
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["type"], "text")
        self.assertEqual(result[0]["text"], "User: Listen to this:")
        self.assertEqual(result[1]["type"], "audio")
        self.assertEqual(result[1]["data"], "AAAA")
        self.assertEqual(result[1]["mime_type"], "audio/ogg")

    def test_build_input_remote_audio_url_and_unsupported_mime(self):
        service = self.make_service()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Listen"},
                    {"type": "audio_url", "audio_url": {"url": "https://example.com/audio.mp3"}},
                    {"type": "audio_url", "audio_url": {"url": "data:audio/unknown;base64,BBBB"}},
                ],
            }
        ]
        result = service._build_input(messages, has_prev_interaction=False)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[1]["type"], "audio")
        self.assertEqual(result[1]["uri"], "https://example.com/audio.mp3")
        self.assertEqual(result[1]["mime_type"], "audio/ogg")
        
        self.assertEqual(result[2]["type"], "audio")
        self.assertEqual(result[2]["data"], "BBBB")
        # Should fallback to audio/ogg for unsupported type
        self.assertEqual(result[2]["mime_type"], "audio/ogg")

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
        input_cost = 1000 * PRICE_INPUT_PER_M / 1_000_000
        output_cost = (500 + 200) * PRICE_OUTPUT_PER_M / 1_000_000
        expected = input_cost + output_cost
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
        input_cost = 500 * PRICE_INPUT_PER_M / 1_000_000
        output_cost = 100 * PRICE_OUTPUT_PER_M / 1_000_000
        cached_cost = 5000 * PRICE_CACHED_PER_M / 1_000_000
        expected = input_cost + output_cost + cached_cost
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
        self.assertEqual(call_kwargs["model"], "gemini-3.8-flash")
        self.assertEqual(call_kwargs["store"], True)
        self.assertIn({"type": "google_search"}, call_kwargs["tools"])

    async def test_get_chat_completion_with_disable_cache(self):
        service = self.make_service()
        # Seed cache
        service._update_interaction_cache(123, "seeded-id")
        
        text_output = SimpleNamespace(type="text", text="Cache bypassed answer")
        interaction = SimpleNamespace(
            outputs=[text_output],
            usage=SimpleNamespace(total_input_tokens=5, total_output_tokens=5, total_thought_tokens=0, total_cached_tokens=0),
            id="new-interaction-id",
        )
        mock_create = AsyncMock(return_value=interaction)
        mock_client = MagicMock()
        mock_client.aio.interactions.create = mock_create
        service._client = mock_client

        await service.get_chat_completion(
            model="google",
            messages=[{"role": "user", "content": "Hi"}],
            system_prompt="",
            channel_id=123,
            disable_cache=True,
        )

        call_kwargs = mock_create.call_args.kwargs
        # previous_interaction_id should NOT be passed to interactions.create when disable_cache is True
        self.assertNotIn("previous_interaction_id", call_kwargs)
        
        # Cache should NOT be updated to the new interaction id
        self.assertEqual(service._get_previous_interaction_id(123), "seeded-id")

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

    # ── Retries ──

    def _service_with_create(self, create):
        service = self.make_service()
        mock_client = MagicMock()
        mock_client.aio.interactions.create = create
        service._client = mock_client
        return service

    async def test_retryable_error_is_retried_then_succeeds(self):
        create = AsyncMock(side_effect=[_api_error(503), _ok_interaction("recovered")])
        service = self._service_with_create(create)

        with patch("src.services.gemini_service.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            result, _ = await service.get_chat_completion(
                model="google", messages=[{"role": "user", "content": "Hi"}])

        self.assertEqual(result, "recovered")
        self.assertEqual(create.await_count, 2)
        mock_sleep.assert_awaited_once_with(1.0)

    async def test_retryable_error_exhausts_attempts(self):
        create = AsyncMock(side_effect=_api_error(429))
        service = self._service_with_create(create)

        with patch("src.services.gemini_service.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            with self.assertRaises(GeminiServiceError) as ctx:
                await service.get_chat_completion(
                    model="google", messages=[{"role": "user", "content": "Hi"}])

        self.assertIn("after 3 attempts", str(ctx.exception))
        self.assertEqual(create.await_count, 3)
        self.assertEqual(mock_sleep.await_count, 2)

    async def test_non_retryable_error_raises_immediately(self):
        create = AsyncMock(side_effect=_api_error(400))
        service = self._service_with_create(create)

        with patch("src.services.gemini_service.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            with self.assertRaises(GeminiServiceError):
                await service.get_chat_completion(
                    model="google", messages=[{"role": "user", "content": "Hi"}])

        create.assert_awaited_once()
        mock_sleep.assert_not_awaited()

    async def test_strips_bot_mention_via_bot_id_loader(self):
        create = AsyncMock(return_value=_ok_interaction("<@42> hello"))
        service = self._service_with_create(create)
        service.set_bot_id_loader(lambda: 42)

        result, _ = await service.get_chat_completion(
            model="google", messages=[{"role": "user", "content": "Hi"}])

        self.assertNotIn("<@42>", result)
        self.assertIn("hello", result)

    # ── Client lifecycle ──

    def test_get_client_sets_http_timeout_in_ms(self):
        service = self.make_service()
        with patch("google.genai.Client") as mock_client_cls:
            client = service._get_client()
            self.assertIs(service._get_client(), client)

        mock_client_cls.assert_called_once()
        kwargs = mock_client_cls.call_args.kwargs
        self.assertEqual(kwargs["api_key"], "test-key")
        self.assertEqual(kwargs["http_options"].timeout, int(REQUEST_TIMEOUT_SECONDS * 1000))

    async def test_close_awaits_async_aclose_and_resets_client(self):
        service = self.make_service()
        client = MagicMock()
        client.aio.aclose = AsyncMock()
        service._client = client

        await service.close()

        client.aio.aclose.assert_awaited_once_with()
        self.assertIsNone(service._client)
        await service.close()  # no client: no-op

    async def test_close_swallows_errors(self):
        service = self.make_service()
        client = MagicMock()
        client.aio.aclose = AsyncMock(side_effect=RuntimeError("boom"))
        service._client = client

        await service.close()

        self.assertIsNone(service._client)


if __name__ == "__main__":
    unittest.main()
