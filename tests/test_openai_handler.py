"""
Unit tests for OpenAI service module.
Tests OpenAI API integration functionality.
"""

from src.services.openai_service import openai_service, OpenAIServiceError
import unittest
from unittest.mock import MagicMock, AsyncMock
import base64
import sys
import os

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Add src directory for new architecture
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


class TestOpenAIHandler(unittest.IsolatedAsyncioTestCase):
    """Test OpenAI service functionality."""

    def setUp(self):
        """Set up test environment with mock client."""
        self.mock_client = AsyncMock()
        openai_service.set_client(self.mock_client)

    async def test_get_chat_completion_success(self):
        """Test successful chat completion."""
        # Mock response for function calling path
        mock_message = MagicMock()
        mock_message.content = "Hello! How can I help you today?"
        mock_message.function_call = None  # No function call

        mock_choice = MagicMock()
        mock_choice.message = mock_message

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        self.mock_client.chat.completions.create.return_value = mock_response

        # Test parameters
        model = "gpt-4.1-nano"
        messages = [{"role": "user", "content": "Hello"}]
        system_prompt = "You are a helpful assistant."

        # Call function
        result = await openai_service.get_chat_completion(model, messages, system_prompt)

        # Verify result
        self.assertEqual(result, "Hello! How can I help you today?")

        # Verify API call - should include system prompt at the beginning
        expected_messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": "Hello"}]
        self.mock_client.chat.completions.create.assert_called_once_with(
            model=model, messages=expected_messages, functions=unittest.mock.ANY, function_call="auto"
        )

    async def test_get_chat_completion_different_models(self):
        """Test chat completion with different models."""
        models_to_test = ["gpt-4.1-mini", "gpt-4.1-nano", "gpt-4o-mini"]

        for model in models_to_test:
            with self.subTest(model=model):
                # Mock response for function calling path
                mock_message = MagicMock()
                mock_message.content = f"Response from {model}"
                mock_message.function_call = None  # No function call

                mock_choice = MagicMock()
                mock_choice.message = mock_message

                mock_response = MagicMock()
                mock_response.choices = [mock_choice]
                self.mock_client.chat.completions.create.return_value = mock_response

                # Test parameters
                messages = [{"role": "user", "content": "Test"}]

                # Call function
                result = await openai_service.get_chat_completion(model, messages)

                # Verify result
                self.assertEqual(result, f"Response from {model}")

    async def test_generate_image_dalle2_success(self):
        """Test successful image generation with DALL-E 2."""
        # Mock response
        mock_data = MagicMock()
        mock_data.b64_json = base64.b64encode(b"fake_image_data").decode()
        mock_response = MagicMock()
        mock_response.data = [mock_data]
        self.mock_client.images.generate.return_value = mock_response

        # Test parameters
        prompt = "A beautiful sunset"
        model = "dall-e-2"

        # Call function
        result = await openai_service.generate_image(prompt, model)

        # Verify result
        self.assertEqual(result, b"fake_image_data")

        # Verify API call
        self.mock_client.images.generate.assert_called_once_with(
            model=model, prompt=prompt, n=1, response_format="b64_json"
        )

    async def test_generate_image_dalle3_success(self):
        """Test successful image generation with DALL-E 3."""
        # Mock response
        mock_data = MagicMock()
        mock_data.b64_json = base64.b64encode(b"dalle3_image_data").decode()
        mock_response = MagicMock()
        mock_response.data = [mock_data]
        self.mock_client.images.generate.return_value = mock_response

        # Test parameters
        prompt = "A futuristic city"
        model = "dall-e-3"

        # Call function
        result = await openai_service.generate_image(prompt, model)

        # Verify result
        self.assertEqual(result, b"dalle3_image_data")

    async def test_generate_image_custom_model_without_edit(self):
        """Test image generation with custom model (gpt-image-1) without editing."""
        # Mock response
        mock_data = MagicMock()
        mock_data.b64_json = base64.b64encode(b"custom_image_data").decode()
        mock_response = MagicMock()
        mock_response.data = [mock_data]
        self.mock_client.images.generate.return_value = mock_response

        # Test parameters
        prompt = "A custom generated image"
        model = "gpt-image-1"

        # Call function
        result = await openai_service.generate_image(prompt, model)

        # Verify result
        self.assertEqual(result, b"custom_image_data")

        # Verify API call
        self.mock_client.images.generate.assert_called_once_with(model=model, prompt=prompt, n=1, moderation="low")

    async def test_generate_image_with_edit(self):
        """Test image editing functionality."""
        # Mock Discord attachment
        mock_attachment = MagicMock()
        mock_file = MagicMock()
        mock_attachment.to_file.return_value = mock_file

        # Mock response
        mock_data = MagicMock()
        mock_data.b64_json = base64.b64encode(b"edited_image_data").decode()
        mock_response = MagicMock()
        mock_response.data = [mock_data]
        self.mock_client.images.edit.return_value = mock_response

        # Test parameters
        prompt = "Make it more colorful"
        model = "gpt-image-1"

        # Call function
        result = await openai_service.generate_image(prompt, model, edit_image=mock_attachment)

        # Verify result
        self.assertEqual(result, b"edited_image_data")

        # Verify API call
        self.mock_client.images.edit.assert_called_once_with(model=model, image=[mock_file], prompt=prompt)

        # Verify attachment was processed
        mock_attachment.to_file.assert_called_once()

    async def test_generate_image_no_data_error(self):
        """Test error handling when no image data is returned."""
        # Mock response
        mock_data = MagicMock()
        mock_data.b64_json = None
        mock_response = MagicMock()
        mock_response.data = [mock_data]
        self.mock_client.images.generate.return_value = mock_response

        # Test parameters
        prompt = "A test image"
        model = "dall-e-2"

        # Call function and expect error
        with self.assertRaises(OpenAIServiceError) as context:
            await openai_service.generate_image(prompt, model)

        self.assertEqual(str(context.exception), "Image generation failed, no image data returned.")

    async def test_generate_image_no_result_error(self):
        """Test error handling when no result is returned."""
        # Mock client
        self.mock_client.images.generate.return_value = None

        # Test parameters
        prompt = "A test image"
        model = "dall-e-2"

        # Call function and expect error
        with self.assertRaises(OpenAIServiceError) as context:
            await openai_service.generate_image(prompt, model)

        self.assertEqual(str(context.exception), "Image generation failed, no image data returned.")

    async def test_generate_image_base64_decoding(self):
        """Test that base64 decoding works correctly."""
        # Create test image data
        test_data = b"test_image_bytes_12345"
        encoded_data = base64.b64encode(test_data).decode()

        # Mock response
        mock_data = MagicMock()
        mock_data.b64_json = encoded_data
        mock_response = MagicMock()
        mock_response.data = [mock_data]
        self.mock_client.images.generate.return_value = mock_response

        # Test parameters
        prompt = "A test image"
        model = "dall-e-2"

        # Call function
        result = await openai_service.generate_image(prompt, model)

        # Verify correct decoding
        self.assertEqual(result, test_data)

    async def test_chat_completion_empty_messages(self):
        """Test chat completion with empty messages list."""
        # Mock response for function calling path
        mock_message = MagicMock()
        mock_message.content = "I'm ready to help!"
        mock_message.function_call = None  # No function call

        mock_choice = MagicMock()
        mock_choice.message = mock_message

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        self.mock_client.chat.completions.create.return_value = mock_response

        # Test with empty messages
        result = await openai_service.get_chat_completion("gpt-4.1-nano", [])

        # Verify it still works
        self.assertEqual(result, "I'm ready to help!")

    async def test_chat_completion_complex_messages(self):
        """Test chat completion with complex message structure."""
        # Mock response for function calling path
        mock_message = MagicMock()
        mock_message.content = "Complex response"
        mock_message.function_call = None  # No function call

        mock_choice = MagicMock()
        mock_choice.message = mock_message

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        self.mock_client.chat.completions.create.return_value = mock_response

        # Test with complex messages including JSON content (without system
        # prompt in messages)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Hello"},
                    {"type": "input_image", "image_url": "http://example.com/image.jpg"},
                ],
            },
            {"role": "assistant", "content": "I can see your image!"},
            {"role": "user", "content": "What do you think?"},
        ]
        system_prompt = "You are a helpful assistant."

        result = await openai_service.get_chat_completion("gpt-4.1-mini", messages, system_prompt)

        # Verify it handles complex structure
        self.assertEqual(result, "Complex response")

        # Expected messages should have system prompt prepended
        expected_messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Hello"},
                    {"type": "input_image", "image_url": "http://example.com/image.jpg"},
                ],
            },
            {"role": "assistant", "content": "I can see your image!"},
            {"role": "user", "content": "What do you think?"},
        ]
        self.mock_client.chat.completions.create.assert_called_once_with(
            model="gpt-4.1-mini", messages=expected_messages, functions=unittest.mock.ANY, function_call="auto"
        )


if __name__ == "__main__":
    unittest.main()
