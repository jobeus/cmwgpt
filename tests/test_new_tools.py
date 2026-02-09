"""
Tests for OpenAI image generation tool functionality
"""

import unittest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import asyncio
import base64
import io
import discord

from src.services.openai_service import OpenAIService


class TestImageGenerationTool(unittest.TestCase):
    """Test OpenAI image generation tool functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.openai_service = OpenAIService()
        self.openai_service.set_client(AsyncMock())

    def test_handle_image_generation_output_with_base64_result(self):
        """Test handling image generation output with base64 result."""
        # Create fake base64 image data
        fake_image_data = b"fake_image_data"
        fake_b64_data = base64.b64encode(fake_image_data).decode()

        # Mock image generation call response
        mock_response_output = Mock()
        mock_response_output.result = fake_b64_data

        # Test the method
        description, files = asyncio.run(self.openai_service._handle_image_generation_output(mock_response_output))

        # Verify the result
        self.assertIsNone(description)
        self.assertEqual(len(files), 1)
        self.assertIsInstance(files[0], discord.File)

    def test_handle_image_generation_output_with_empty_result(self):
        """Test handling image generation output with empty base64 result."""
        # Mock image generation call response with empty result
        mock_response_output = Mock()
        mock_response_output.result = ""

        # Test the method
        description, files = asyncio.run(self.openai_service._handle_image_generation_output(mock_response_output))

        # Verify the result
        self.assertIsNotNone(description)
        self.assertEqual(description, "🎨 error w/ image generation: no images were generated.")
        self.assertEqual(len(files), 0)

    def test_handle_image_generation_output_decode_error(self):
        """Test handling image generation output with invalid base64 data."""
        # Mock image generation call response with invalid base64
        mock_response_output = Mock()
        mock_response_output.result = "invalid_base64_data"

        # Test the method
        description, files = asyncio.run(self.openai_service._handle_image_generation_output(mock_response_output))

        # Verify the result
        self.assertIsNotNone(description)
        self.assertEqual(description, "🎨 **Image Generation:** Error processing generated images.")
        self.assertEqual(len(files), 0)

    def test_handle_image_generation_output_missing_attribute(self):
        """Test handling image generation output with missing result attribute."""
        mock_response_output = Mock()
        # Don't set result attribute - Mock will return another Mock when accessed
        # But base64.b64decode will fail on a Mock, which catches the exception
        del mock_response_output.result

        # Test the method
        description, files = asyncio.run(self.openai_service._handle_image_generation_output(mock_response_output))

        # Verify the result
        self.assertEqual(description, "🎨 **Image Generation:** Error processing generated images.")
        self.assertEqual(len(files), 0)



    @patch('src.services.openai_service.AsyncOpenAI')
    def test_response_with_only_image_generation(self, mock_openai_class):
        """Test response processing when only image generation output is returned."""
        # Mock OpenAI client
        mock_client = AsyncMock()
        mock_openai_class.return_value = mock_client

        # Mock response with only image generation output
        fake_image_data = b"fake_image_data"
        fake_b64_data = base64.b64encode(fake_image_data).decode()

        mock_response_output = Mock()
        mock_response_output.type = "image_generation_call"
        mock_response_output.result = fake_b64_data

        mock_response = Mock()
        mock_response.output = [mock_response_output]
        mock_response.id = "test_response_id"

        mock_client.responses.create.return_value = mock_response

        # Mock state service
        mock_state_service = Mock()
        mock_state_service.get_response_id.return_value = None

        # Test the method
        result = asyncio.run(
            self.openai_service._handle_openai_response_with_continuity(
                mock_client, "gpt-4", [], "test instructions", [], 12345, mock_state_service
            )
        )

        # Verify the result
        self.assertIsInstance(result, dict)
        self.assertIn("text", result)
        self.assertIn("files", result)
        self.assertEqual(result["text"], "Here are the generated images:")
        self.assertEqual(len(result["files"]), 1)
        self.assertIsInstance(result["files"][0], discord.File)


if __name__ == "__main__":
    unittest.main()
