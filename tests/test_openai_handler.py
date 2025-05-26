"""
Unit tests for openai_handler.py module.
Tests OpenAI API integration functionality.
"""

from openai_handler import get_chat_completion, generate_image
import unittest
from unittest.mock import patch, MagicMock
import base64
import sys
import os

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestOpenAIHandler(unittest.TestCase):
    """Test openai_handler.py functionality."""

    @patch("openai_handler.get_client")
    def test_get_chat_completion_success(self, mock_get_client):
        """Test successful chat completion."""
        # Mock client and response
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_response = MagicMock()
        mock_response.output_text = "Hello! How can I help you today?"
        mock_client.responses.create.return_value = mock_response

        # Test parameters
        model = "gpt-4.1-nano"
        messages = [{"role": "system", "content": "You are a helpful assistant."}, {
            "role": "user", "content": "Hello"}]

        # Call function
        result = get_chat_completion(model, messages)

        # Verify result
        self.assertEqual(result, "Hello! How can I help you today?")

        # Verify API call
        mock_client.responses.create.assert_called_once_with(
            model=model, input=messages)

    @patch("openai_handler.get_client")
    def test_get_chat_completion_different_models(self, mock_get_client):
        """Test chat completion with different models."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        models_to_test = ["gpt-4.1-mini", "gpt-4.1-nano", "gpt-4o-mini"]

        for model in models_to_test:
            with self.subTest(model=model):
                # Mock response
                mock_response = MagicMock()
                mock_response.output_text = f"Response from {model}"
                mock_client.responses.create.return_value = mock_response

                # Test parameters
                messages = [{"role": "user", "content": "Test"}]

                # Call function
                result = get_chat_completion(model, messages)

                # Verify result
                self.assertEqual(result, f"Response from {model}")

    @patch("openai_handler.get_client")
    def test_generate_image_dalle2_success(self, mock_get_client):
        """Test successful image generation with DALL-E 2."""
        # Mock client and response
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_data = MagicMock()
        mock_data.b64_json = base64.b64encode(b"fake_image_data").decode()
        mock_response = MagicMock()
        mock_response.data = [mock_data]
        mock_client.images.generate.return_value = mock_response

        # Test parameters
        prompt = "A beautiful sunset"
        model = "dall-e-2"

        # Call function
        result = generate_image(prompt, model)

        # Verify result
        self.assertEqual(result, b"fake_image_data")

        # Verify API call
        mock_client.images.generate.assert_called_once_with(
            model=model, prompt=prompt, n=1, response_format="b64_json")

    @patch("openai_handler.get_client")
    def test_generate_image_dalle3_success(self, mock_get_client):
        """Test successful image generation with DALL-E 3."""
        # Mock client and response
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_data = MagicMock()
        mock_data.b64_json = base64.b64encode(b"dalle3_image_data").decode()
        mock_response = MagicMock()
        mock_response.data = [mock_data]
        mock_client.images.generate.return_value = mock_response

        # Test parameters
        prompt = "A futuristic city"
        model = "dall-e-3"

        # Call function
        result = generate_image(prompt, model)

        # Verify result
        self.assertEqual(result, b"dalle3_image_data")

    @patch("openai_handler.get_client")
    def test_generate_image_custom_model_without_edit(self, mock_get_client):
        """Test image generation with custom model (gpt-image-1) without editing."""
        # Mock client and response
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_data = MagicMock()
        mock_data.b64_json = base64.b64encode(b"custom_image_data").decode()
        mock_response = MagicMock()
        mock_response.data = [mock_data]
        mock_client.images.generate.return_value = mock_response

        # Test parameters
        prompt = "A custom generated image"
        model = "gpt-image-1"

        # Call function
        result = generate_image(prompt, model)

        # Verify result
        self.assertEqual(result, b"custom_image_data")

        # Verify API call
        mock_client.images.generate.assert_called_once_with(
            model=model, prompt=prompt, n=1)

    @patch("openai_handler.get_client")
    def test_generate_image_with_edit(self, mock_get_client):
        """Test image editing functionality."""
        # Mock client and response
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock Discord attachment
        mock_attachment = MagicMock()
        mock_file = MagicMock()
        mock_attachment.to_file.return_value = mock_file

        # Mock response
        mock_data = MagicMock()
        mock_data.b64_json = base64.b64encode(b"edited_image_data").decode()
        mock_response = MagicMock()
        mock_response.data = [mock_data]
        mock_client.images.edit.return_value = mock_response

        # Test parameters
        prompt = "Make it more colorful"
        model = "gpt-image-1"

        # Call function
        result = generate_image(prompt, model, edit_image=mock_attachment)

        # Verify result
        self.assertEqual(result, b"edited_image_data")

        # Verify API call
        mock_client.images.edit.assert_called_once_with(
            model=model, image=[mock_file], prompt=prompt)

        # Verify attachment was processed
        mock_attachment.to_file.assert_called_once()

    @patch("openai_handler.get_client")
    def test_generate_image_no_data_error(self, mock_get_client):
        """Test error handling when no image data is returned."""
        # Mock client and response
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_data = MagicMock()
        mock_data.b64_json = None
        mock_response = MagicMock()
        mock_response.data = [mock_data]
        mock_client.images.generate.return_value = mock_response

        # Test parameters
        prompt = "A test image"
        model = "dall-e-2"

        # Call function and expect error
        with self.assertRaises(ValueError) as context:
            generate_image(prompt, model)

        self.assertEqual(str(context.exception),
                         "Image generation failed, no b64_json data returned.")

    @patch("openai_handler.get_client")
    def test_generate_image_no_result_error(self, mock_get_client):
        """Test error handling when no result is returned."""
        # Mock client
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.images.generate.return_value = None

        # Test parameters
        prompt = "A test image"
        model = "dall-e-2"

        # Call function and expect error
        with self.assertRaises(ValueError) as context:
            generate_image(prompt, model)

        self.assertEqual(str(context.exception),
                         "Image generation failed, no b64_json data returned.")

    @patch("openai_handler.get_client")
    def test_generate_image_base64_decoding(self, mock_get_client):
        """Test that base64 decoding works correctly."""
        # Mock client and response
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Create test image data
        test_data = b"test_image_bytes_12345"
        encoded_data = base64.b64encode(test_data).decode()

        # Mock response
        mock_data = MagicMock()
        mock_data.b64_json = encoded_data
        mock_response = MagicMock()
        mock_response.data = [mock_data]
        mock_client.images.generate.return_value = mock_response

        # Test parameters
        prompt = "A test image"
        model = "dall-e-2"

        # Call function
        result = generate_image(prompt, model)

        # Verify correct decoding
        self.assertEqual(result, test_data)

    @patch("openai_handler.get_client")
    def test_chat_completion_empty_messages(self, mock_get_client):
        """Test chat completion with empty messages list."""
        # Mock client and response
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_response = MagicMock()
        mock_response.output_text = "I'm ready to help!"
        mock_client.responses.create.return_value = mock_response

        # Test with empty messages
        result = get_chat_completion("gpt-4.1-nano", [])

        # Verify it still works
        self.assertEqual(result, "I'm ready to help!")

    @patch("openai_handler.get_client")
    def test_chat_completion_complex_messages(self, mock_get_client):
        """Test chat completion with complex message structure."""
        # Mock client and response
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_response = MagicMock()
        mock_response.output_text = "Complex response"
        mock_client.responses.create.return_value = mock_response

        # Test with complex messages including JSON content
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {
                "role": "user",
                "content": '[{"type": "text", "text": "Hello"}, {"type": "image_url", "image_url": {"url": "http://example.com/image.jpg"}}]',
            },
            {"role": "assistant", "content": "I can see your image!"},
            {"role": "user", "content": "What do you think?"},
        ]

        result = get_chat_completion("gpt-4.1-mini", messages)

        # Verify it handles complex structure
        self.assertEqual(result, "Complex response")
        mock_client.responses.create.assert_called_once_with(
            model="gpt-4.1-mini", input=messages)


if __name__ == "__main__":
    unittest.main()
