"""
Tests for new OpenAI tools: web_search_preview and image_generation
"""

import unittest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import asyncio
import base64
import io
import discord

from src.services.openai_service import OpenAIService


class TestNewOpenAITools(unittest.TestCase):
    """Test new OpenAI tools functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.openai_service = OpenAIService()
        self.openai_service.set_client(AsyncMock())

    def test_handle_web_search_output_with_results(self):
        """Test handling web search output with results."""
        # Mock web search response output
        mock_result1 = Mock()
        mock_result1.title = "Test Title 1"
        mock_result1.url = "https://example.com/1"
        mock_result1.snippet = "This is a test snippet 1"
        
        mock_result2 = Mock()
        mock_result2.title = "Test Title 2"
        mock_result2.url = "https://example.com/2"
        mock_result2.snippet = "This is a test snippet 2"
        
        mock_web_search = Mock()
        mock_web_search.results = [mock_result1, mock_result2]
        
        mock_response_output = Mock()
        mock_response_output.web_search = mock_web_search
        
        # Test the method
        result = asyncio.run(self.openai_service._handle_web_search_output(mock_response_output))
        
        # Verify the result
        self.assertIsNotNone(result)
        self.assertIn("🔍 **Web Search Results:**", result)
        self.assertIn("**Test Title 1**", result)
        self.assertIn("https://example.com/1", result)
        self.assertIn("This is a test snippet 1", result)
        self.assertIn("**Test Title 2**", result)
        self.assertIn("https://example.com/2", result)
        self.assertIn("This is a test snippet 2", result)

    def test_handle_web_search_output_no_results(self):
        """Test handling web search output with no results."""
        mock_web_search = Mock()
        mock_web_search.results = []
        
        mock_response_output = Mock()
        mock_response_output.web_search = mock_web_search
        
        # Test the method
        result = asyncio.run(self.openai_service._handle_web_search_output(mock_response_output))
        
        # Verify the result
        self.assertIsNotNone(result)
        self.assertEqual(result, "🔍 **Web Search:** No results found.")

    def test_handle_web_search_output_missing_attribute(self):
        """Test handling web search output with missing web_search attribute."""
        mock_response_output = Mock()
        # Don't set web_search attribute - Mock will return another Mock when accessed
        del mock_response_output.web_search  # Ensure the attribute doesn't exist

        # Test the method
        result = asyncio.run(self.openai_service._handle_web_search_output(mock_response_output))

        # Verify the result
        self.assertEqual(result, "🔍 **Web Search:** Missing search data.")

    @patch('src.services.openai_service.httpx.AsyncClient')
    def test_handle_image_generation_output_with_url(self, mock_client_class):
        """Test handling image generation output with URL."""
        # Mock HTTP client response
        mock_response = Mock()
        mock_response.content = b"fake_image_data"
        mock_response.raise_for_status = Mock()
        
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client_class.return_value.__aenter__.return_value = mock_client
        
        # Mock image generation response
        mock_image = Mock()
        mock_image.url = "https://example.com/image.png"
        mock_image.b64_json = None
        
        mock_image_gen = Mock()
        mock_image_gen.images = [mock_image]
        
        mock_response_output = Mock()
        mock_response_output.image_generation = mock_image_gen
        
        # Test the method
        description, files = asyncio.run(self.openai_service._handle_image_generation_output(mock_response_output))
        
        # Verify the result
        self.assertIsNotNone(description)
        self.assertIn("🎨 **Generated 1 image:**", description)
        self.assertEqual(len(files), 1)
        self.assertIsInstance(files[0], discord.File)

    def test_handle_image_generation_output_with_base64(self):
        """Test handling image generation output with base64 data."""
        # Create fake base64 image data
        fake_image_data = b"fake_image_data"
        fake_b64_data = base64.b64encode(fake_image_data).decode()
        
        # Mock image generation response
        mock_image = Mock()
        mock_image.url = None
        mock_image.b64_json = fake_b64_data
        
        mock_image_gen = Mock()
        mock_image_gen.images = [mock_image]
        
        mock_response_output = Mock()
        mock_response_output.image_generation = mock_image_gen
        
        # Test the method
        description, files = asyncio.run(self.openai_service._handle_image_generation_output(mock_response_output))
        
        # Verify the result
        self.assertIsNotNone(description)
        self.assertIn("🎨 **Generated 1 image:**", description)
        self.assertEqual(len(files), 1)
        self.assertIsInstance(files[0], discord.File)

    def test_handle_image_generation_output_no_images(self):
        """Test handling image generation output with no images."""
        mock_image_gen = Mock()
        mock_image_gen.images = []
        
        mock_response_output = Mock()
        mock_response_output.image_generation = mock_image_gen
        
        # Test the method
        description, files = asyncio.run(self.openai_service._handle_image_generation_output(mock_response_output))
        
        # Verify the result
        self.assertIsNotNone(description)
        self.assertEqual(description, "🎨 **Image Generation:** No images were generated.")
        self.assertEqual(len(files), 0)

    def test_handle_image_generation_output_missing_attribute(self):
        """Test handling image generation output with missing image_generation attribute."""
        mock_response_output = Mock()
        # Don't set image_generation attribute - Mock will return another Mock when accessed
        del mock_response_output.image_generation  # Ensure the attribute doesn't exist

        # Test the method
        description, files = asyncio.run(self.openai_service._handle_image_generation_output(mock_response_output))

        # Verify the result
        self.assertEqual(description, "🎨 **Image Generation:** Missing image generation data.")
        self.assertEqual(len(files), 0)

    def test_handle_image_generation_output_multiple_images(self):
        """Test handling image generation output with multiple images."""
        # Create fake base64 image data for multiple images
        fake_image_data1 = b"fake_image_data_1"
        fake_b64_data1 = base64.b64encode(fake_image_data1).decode()
        
        fake_image_data2 = b"fake_image_data_2"
        fake_b64_data2 = base64.b64encode(fake_image_data2).decode()
        
        # Mock image generation response with multiple images
        mock_image1 = Mock()
        mock_image1.url = None
        mock_image1.b64_json = fake_b64_data1
        
        mock_image2 = Mock()
        mock_image2.url = None
        mock_image2.b64_json = fake_b64_data2
        
        mock_image_gen = Mock()
        mock_image_gen.images = [mock_image1, mock_image2]
        
        mock_response_output = Mock()
        mock_response_output.image_generation = mock_image_gen
        
        # Test the method
        description, files = asyncio.run(self.openai_service._handle_image_generation_output(mock_response_output))
        
        # Verify the result
        self.assertIsNotNone(description)
        self.assertIn("🎨 **Generated 2 images:**", description)
        self.assertEqual(len(files), 2)
        self.assertIsInstance(files[0], discord.File)
        self.assertIsInstance(files[1], discord.File)

    @patch('src.services.openai_service.AsyncOpenAI')
    def test_response_with_only_image_generation(self, mock_openai_class):
        """Test response processing when only image generation output is returned."""
        # Mock OpenAI client
        mock_client = AsyncMock()
        mock_openai_class.return_value = mock_client

        # Mock response with only image generation output
        fake_image_data = b"fake_image_data"
        fake_b64_data = base64.b64encode(fake_image_data).decode()

        mock_image = Mock()
        mock_image.url = None
        mock_image.b64_json = fake_b64_data

        mock_image_gen = Mock()
        mock_image_gen.images = [mock_image]

        mock_response_output = Mock()
        mock_response_output.type = "image_generation"
        mock_response_output.image_generation = mock_image_gen

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
        self.assertIn("🎨 **Generated 1 image:**", result["text"])
        self.assertEqual(len(result["files"]), 1)
        self.assertIsInstance(result["files"][0], discord.File)


if __name__ == "__main__":
    unittest.main()
