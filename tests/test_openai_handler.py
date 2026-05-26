"""Unit tests for OpenAI service module."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

from src.services.openai_service import OpenAIService, OpenAIServiceError, openai_service

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(
            os.path.dirname(
                os.path.abspath(__file__))),
        "src"))


class TestOpenAIHandler(unittest.IsolatedAsyncioTestCase):
    """Test OpenAI service functionality."""

    def setUp(self):
        """Set up test environment with mock client."""
        self.mock_client = AsyncMock()
        self.mock_client.default_headers = {}
        openai_service.set_client(self.mock_client)
        openai_service.set_bot_id_loader(None)

    async def test_get_chat_completion_success(self):
        """Test successful chat completion."""
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Hello! How can I help you today?"
        mock_response.choices = [mock_choice]
        self.mock_client.chat.completions.create.return_value = mock_response

        model = "anthropic/claude-haiku-4.5"
        messages = [{"role": "user", "content": "Hello"}]
        system_prompt = "You are a helpful assistant."

        result, _ = await openai_service.get_chat_completion(model, messages, system_prompt)

        self.assertEqual(result, "Hello! How can I help you today?")

        expected_input = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Hello"}
        ]
        self.mock_client.chat.completions.create.assert_called_once_with(
            model=model,
            messages=expected_input,
            extra_body={
                "provider": {
                    "sort": "price"
                },
                "plugins": [{
                    "id": "web",
                    "engine": "native"
                }]
            }
        )

    async def test_get_chat_completion_different_models(self):
        """Test chat completion with different models."""
        models_to_test = ["gpt-5", "gpt-5-mini", "gpt-5-nano"]

        for model in models_to_test:
            with self.subTest(model=model):
                mock_response = MagicMock()
                mock_choice = MagicMock()
                mock_choice.message.content = f"Response from {model}"
                mock_response.choices = [mock_choice]
                self.mock_client.chat.completions.create.return_value = mock_response

                messages = [{"role": "user", "content": "Test"}]

                result, _ = await openai_service.get_chat_completion(model, messages)

                self.assertEqual(result, f"Response from {model}")

    async def test_chat_completion_empty_messages(self):
        """Test chat completion with empty messages list."""
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "I'm ready to help!"
        mock_response.choices = [mock_choice]
        self.mock_client.chat.completions.create.return_value = mock_response

        result, _ = await openai_service.get_chat_completion("google/gemini-2.5-flash", [])
        self.assertEqual(result, "I'm ready to help!")

    async def test_chat_completion_complex_messages(self):
        """Test chat completion with complex message structure."""
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Complex response"
        mock_response.choices = [mock_choice]
        self.mock_client.chat.completions.create.return_value = mock_response

        messages = [
            {
                "role": "user",
                "content": '[{"type": "input_text", "text": "Hello"}, {"type": "input_image", "image_url": "http://example.com/image.jpg"}]',
            },
            {"role": "assistant", "content": '[{"type": "output_text", "text": "I can see your image!"}]'},
            {"role": "user", "content": '[{"type": "input_text", "text": "What do you think?"}]'},
        ]
        system_prompt = "You are a helpful assistant."

        result, _ = await openai_service.get_chat_completion("google/gemini-2.5-flash", messages, system_prompt)

        self.assertEqual(result, "Complex response")

    @patch("src.services.openai_service.clean_openai_response", return_value="cleaned")
    async def test_chat_completion_uses_injected_bot_id_loader(self, mock_clean):
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "<@12345> Hello"
        mock_response.choices = [mock_choice]
        self.mock_client.chat.completions.create.return_value = mock_response
        openai_service.set_bot_id_loader(lambda: 12345)

        result, _ = await openai_service.get_chat_completion("gpt-5-mini", [{"role": "user", "content": "hi"}])

        self.assertEqual(result, "cleaned")
        mock_clean.assert_called_once_with("<@12345> Hello", bot_id=12345)

    async def test_get_chat_completion_filters_audio_blocks(self):
        """Test that get_chat_completion filters out audio_url blocks from messages."""
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Filtered response"
        mock_response.choices = [mock_choice]
        self.mock_client.chat.completions.create.return_value = mock_response

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Listen to this image:"},
                    {"type": "image_url", "image_url": {"url": "http://example.com/image.jpg"}},
                    {"type": "audio_url", "audio_url": {"url": "data:audio/ogg;base64,AAAA"}},
                ],
            }
        ]

        result, _ = await openai_service.get_chat_completion("gpt-test", messages)

        self.assertEqual(result, "Filtered response")
        
        # Verify that only text and image_url parts were kept, and audio_url was removed
        expected_content = [
            {"type": "text", "text": "Listen to this image:"},
            {"type": "image_url", "image_url": {"url": "http://example.com/image.jpg"}}
        ]
        
        self.mock_client.chat.completions.create.assert_called_once()
        call_kwargs = self.mock_client.chat.completions.create.call_args.kwargs
        self.assertEqual(call_kwargs["messages"][0]["content"], expected_content)


def make_response(text=None, *, error=None, cost=0.0, http_response=None):
    response = SimpleNamespace(
        choices=[] if text is None else [SimpleNamespace(message=SimpleNamespace(content=text))],
        error=error,
        usage=SimpleNamespace(model_extra={"cost_details": {"upstream_inference_cost": cost}}),
        http_response=http_response,
    )
    response.model_dump = lambda: {"text": text, "error": error}
    return response


class FakeAPIError(Exception):
    def __init__(self, message=None, request=None, body=None):
        super().__init__(message)
        self.message = message
        self.request = request
        self.body = body


class FakeBadRequestError(Exception):
    def __init__(self, message="bad request"):
        super().__init__(message)
        self.message = message


class FakeRateLimitError(Exception):
    pass


class FakeAuthenticationError(Exception):
    pass


class FakeAPIConnectionError(Exception):
    pass


class TestOpenAIServiceBranches(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = OpenAIService()
        self.client = MagicMock()
        self.client.default_headers = {"X-Test": "1"}
        self.client.api_key = "api-key"
        self.client.chat.completions.create = AsyncMock()
        self.service.set_client(self.client)

    def test_get_client_uses_testing_stub_and_real_client_factory(self):
        service = OpenAIService()

        with patch("src.services.openai_service.IS_TESTING", True):
            client = service.get_client()
            self.assertTrue(callable(client.anything))

        service = OpenAIService()
        fake_client = MagicMock()
        with patch("src.services.openai_service.IS_TESTING", False), patch(
            "src.services.openai_service.AsyncOpenAI", return_value=fake_client
        ) as mock_factory:
            client = service.get_client()

        self.assertIs(client, fake_client)
        mock_factory.assert_called_once()

    async def test_close_resets_client_on_success_and_failure(self):
        closing_client = MagicMock()
        closing_client.close = AsyncMock()
        self.service.set_client(closing_client)

        await self.service.close()
        self.assertIsNone(self.service._client)

        failing_client = MagicMock()
        failing_client.close = AsyncMock(side_effect=RuntimeError("boom"))
        self.service.set_client(failing_client)

        await self.service.close()
        self.assertIsNone(self.service._client)

    def test_dump_bad_request_writes_json_and_script(self):
        with patch("builtins.open", mock_open()) as mock_file, patch(
            "src.services.openai_service.json.dump"
        ) as mock_dump, patch("os.chmod") as mock_chmod, patch(
            "src.services.openai_service.tempfile.gettempdir", return_value="/tmp"
        ):
            self.service._dump_bad_request({"model": "x"}, self.client)

        mock_dump.assert_called_once()
        mock_chmod.assert_called_once_with("/tmp/bad_request.sh", 0o700)
        script_handle = mock_file()
        written = "".join(call.args[0] for call in script_handle.write.call_args_list)
        self.assertIn("curl https://openrouter.ai/api/v1/chat/completions", written)
        self.assertIn("Authorization: Bearer api-key", written)

    def test_dump_bad_request_uses_config_key_skips_omit_headers_and_logs_failures(self):
        omit_value = type("OmitHeader", (), {})()
        client = MagicMock()
        client.default_headers = {"X-Test": "1", "X-Skip": omit_value}
        client.api_key = None

        with patch("src.services.openai_service.OPENROUTER_API_KEY", "config-key"), patch(
            "builtins.open", mock_open()
        ) as mock_file, patch("src.services.openai_service.json.dump"), patch("os.chmod"), patch(
            "src.services.openai_service.tempfile.gettempdir", return_value="/tmp"
        ):
            self.service._dump_bad_request({"model": "x"}, client)

        written = "".join(call.args[0] for call in mock_file().write.call_args_list)
        self.assertIn("Authorization: Bearer config-key", written)
        self.assertNotIn("X-Skip", written)

        with patch("builtins.open", side_effect=OSError("disk full")), patch(
            "src.services.openai_service.logger.error"
        ) as mock_error:
            self.service._dump_bad_request({"model": "x"}, client)

        self.assertIn("Failed to dump bad request", mock_error.call_args.args[0])

    async def test_get_chat_completion_prefixes_cost(self):
        http_response = SimpleNamespace(
            request=SimpleNamespace(
                headers={"X-Req": "1"},
                content=b'{"messages":[]}',
                url="https://openrouter.ai/api/v1/chat/completions",
                method="POST",
            ),
            status_code=200,
            headers={"X-Resp": "1"},
        )
        self.client.chat.completions.create.return_value = make_response(
            "hello",
            cost=1.2345,
            http_response=http_response,
        )

        with patch("src.services.openai_service.clean_openai_response", return_value="cleaned"):
            result, _ = await self.service.get_chat_completion(
                "gpt-test",
                [{"role": "user", "content": "hi"}],
                discord_user_id=42,
                channel_id=7,
            )

        self.assertEqual(result, "cleaned")
        self.client.chat.completions.create.assert_awaited_once()

    async def test_get_chat_completion_preserves_invalid_json_and_uses_state_service_bot_id(self):
        self.client.chat.completions.create.return_value = make_response("hello")
        state_service = SimpleNamespace(bot_id=555)
        messages = [
            {"role": "system", "content": "skip me"},
            {"role": "user", "content": "[not-json"},
            {"role": "user", "content": [{"type": "text", "text": "hello\nworld"}]},
        ]

        with patch("src.services.openai_service.clean_openai_response", return_value="cleaned") as mock_clean:
            result, _ = await self.service.get_chat_completion(
                "gpt-test", messages, system_prompt="system", state_service=state_service
            )

        self.assertEqual(result, "cleaned")
        payload = self.client.chat.completions.create.await_args.kwargs["messages"]
        self.assertEqual(payload[1]["content"], "[not-json")
        self.assertEqual(payload[2]["content"], [{"type": "text", "text": "hello\nworld"}])
        mock_clean.assert_called_once_with("hello", bot_id=555)

    async def test_get_chat_completion_handles_bot_id_loader_and_cost_parse_failures(self):
        class BadCostDict(dict):
            def get(self, key, default=None):
                raise RuntimeError("bad cost")

        response = make_response("hello")
        response.usage = SimpleNamespace(model_extra={"cost_details": BadCostDict()})
        self.client.chat.completions.create.return_value = response
        self.service.set_bot_id_loader(lambda: (_ for _ in ()).throw(RuntimeError("no bot")))

        with patch("src.services.openai_service.clean_openai_response", return_value="cleaned") as mock_clean, \
            patch("src.services.openai_service.logger.warning") as mock_warning:
            result, _ = await self.service.get_chat_completion("gpt-test", [{"role": "user", "content": "hi"}])

        self.assertEqual(result, "cleaned")
        mock_clean.assert_called_once_with("hello", bot_id=None)
        warning_messages = [call.args[0] for call in mock_warning.call_args_list]
        self.assertTrue(any("Failed to get bot_id" in msg for msg in warning_messages))
        self.assertTrue(any("Failed to parse cost" in msg for msg in warning_messages))

    async def test_get_chat_completion_retries_rate_limit_then_succeeds(self):
        self.client.chat.completions.create.side_effect = [
            FakeRateLimitError("slow down"),
            make_response("hello"),
        ]

        with patch("src.services.openai_service.RateLimitError", FakeRateLimitError), patch(
            "src.services.openai_service.clean_openai_response", return_value="done"
        ), patch(
            "src.services.openai_service.asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            result, _ = await self.service.get_chat_completion("gpt-test", [{"role": "user", "content": "hi"}])

        self.assertEqual(result, "done")
        mock_sleep.assert_awaited_once_with(1.0)

    async def test_get_chat_completion_raises_after_rate_limit_retries_exhausted(self):
        self.client.chat.completions.create.side_effect = [
            FakeRateLimitError("slow down 1"),
            FakeRateLimitError("slow down 2"),
            FakeRateLimitError("slow down 3"),
        ]

        with patch("src.services.openai_service.RateLimitError", FakeRateLimitError), patch(
            "src.services.openai_service.asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            with self.assertRaises(OpenAIServiceError) as ctx:
                await self.service.get_chat_completion("gpt-test", [{"role": "user", "content": "hi"}])

        self.assertIn("Rate limit exceeded after 3 attempts", str(ctx.exception))
        self.assertEqual(mock_sleep.await_count, 2)

    async def test_get_chat_completion_handles_authentication_errors(self):
        self.client.chat.completions.create.side_effect = FakeAuthenticationError("bad key")

        with patch("src.services.openai_service.AuthenticationError", FakeAuthenticationError):
            with self.assertRaises(OpenAIServiceError) as ctx:
                await self.service.get_chat_completion("gpt-test", [{"role": "user", "content": "hi"}])

        self.assertIn("Authentication failed", str(ctx.exception))

    async def test_get_chat_completion_raises_after_connection_retries_exhausted(self):
        self.client.chat.completions.create.side_effect = [
            FakeAPIConnectionError("offline 1"),
            FakeAPIConnectionError("offline 2"),
            FakeAPIConnectionError("offline 3"),
        ]

        with patch("src.services.openai_service.APIConnectionError", FakeAPIConnectionError), patch(
            "src.services.openai_service.asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            with self.assertRaises(OpenAIServiceError) as ctx:
                await self.service.get_chat_completion("gpt-test", [{"role": "user", "content": "hi"}])

        self.assertIn("Connection failed after 3 attempts", str(ctx.exception))
        self.assertEqual(mock_sleep.await_count, 2)

    async def test_get_chat_completion_retries_retryable_soft_error_then_succeeds(self):
        self.client.chat.completions.create.side_effect = [
            make_response(error={"code": 429, "message": "busy"}),
            make_response("hi"),
        ]

        with patch("src.services.openai_service.APIError", FakeAPIError), patch(
            "src.services.openai_service.clean_openai_response", return_value="ok"
        ), patch(
            "src.services.openai_service.asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            result, _ = await self.service.get_chat_completion("gpt-test", [{"role": "user", "content": "hi"}])

        self.assertEqual(result, "ok")
        mock_sleep.assert_awaited_once_with(1.0)

    async def test_get_chat_completion_raises_after_api_error_retries_exhausted(self):
        self.client.chat.completions.create.side_effect = [
            FakeAPIError("upstream 1"),
            FakeAPIError("upstream 2"),
            FakeAPIError("upstream 3"),
        ]

        with patch("src.services.openai_service.APIError", FakeAPIError), patch.object(
            self.service, "_dump_bad_request"
        ) as mock_dump, patch("src.services.openai_service.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            with self.assertRaises(OpenAIServiceError) as ctx:
                await self.service.get_chat_completion("gpt-test", [{"role": "user", "content": "hi"}])

        self.assertIn("OpenAI API error after 3 attempts", str(ctx.exception))
        self.assertEqual(mock_sleep.await_count, 2)
        mock_dump.assert_called_once()

    async def test_get_chat_completion_raises_for_non_retryable_soft_error(self):
        self.client.chat.completions.create.return_value = make_response(
            error={"code": 400, "message": "bad request"}
        )

        with patch.object(self.service, "_dump_bad_request") as mock_dump:
            with self.assertRaises(OpenAIServiceError) as ctx:
                await self.service.get_chat_completion("gpt-test", [{"role": "user", "content": "hi"}])

        self.assertIn("Non-retryable soft-error 400", str(ctx.exception))
        mock_dump.assert_called_once()

    async def test_get_chat_completion_handles_bad_request_errors(self):
        self.client.chat.completions.create.side_effect = FakeBadRequestError("payload invalid")

        with patch("src.services.openai_service.BadRequestError", FakeBadRequestError), patch.object(
            self.service, "_dump_bad_request"
        ) as mock_dump:
            with self.assertRaises(OpenAIServiceError) as ctx:
                await self.service.get_chat_completion("gpt-test", [{"role": "user", "content": "hi"}])

        self.assertIn("Invalid request: payload invalid", str(ctx.exception))
        mock_dump.assert_called_once()

    async def test_get_chat_completion_returns_fallback_string_when_no_choices_exist(self):
        self.client.chat.completions.create.return_value = make_response(text=None)

        with patch.object(self.service, "_dump_bad_request") as mock_dump:
            result, _ = await self.service.get_chat_completion("gpt-test", [{"role": "user", "content": "hi"}])

        self.assertEqual(result, "Failed to get a response from the model.")
        mock_dump.assert_called_once()

    async def test_get_chat_completion_retries_empty_response_then_raises(self):
        self.client.chat.completions.create.return_value = make_response("")

        with patch.object(self.service, "_dump_bad_request") as mock_dump, patch(
            "src.services.openai_service.asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            with self.assertRaises(OpenAIServiceError) as ctx:
                await self.service.get_chat_completion("gpt-test", [{"role": "user", "content": "hi"}])

        self.assertIn("Unexpected error: The model API returned an empty response.", str(ctx.exception))
        self.assertEqual(mock_sleep.await_count, 2)
        mock_dump.assert_called_once()


if __name__ == "__main__":
    unittest.main()
