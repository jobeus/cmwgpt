"""
Unit tests for OpenAI service module.
Tests OpenAI API integration functionality.
"""

from src.services.openai_service import openai_service, OpenAIServiceError
import unittest
from unittest.mock import MagicMock, AsyncMock
import sys
import os

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
        openai_service.set_client(self.mock_client)

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

        result = await openai_service.get_chat_completion(model, messages, system_prompt)

        self.assertEqual(result, "Hello! How can I help you today?")
        
        expected_input = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Hello"}
        ]
        self.mock_client.chat.completions.create.assert_called_once_with(
            model=model,
            messages=expected_input,
            extra_body={
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

                result = await openai_service.get_chat_completion(model, messages)

                self.assertEqual(result, f"Response from {model}")

    async def test_chat_completion_empty_messages(self):
        """Test chat completion with empty messages list."""
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "I'm ready to help!"
        mock_response.choices = [mock_choice]
        self.mock_client.chat.completions.create.return_value = mock_response

        result = await openai_service.get_chat_completion("google/gemini-2.5-flash", [])
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

        result = await openai_service.get_chat_completion("google/gemini-2.5-flash", messages, system_prompt)

        self.assertEqual(result, "Complex response")


if __name__ == "__main__":
    unittest.main()
