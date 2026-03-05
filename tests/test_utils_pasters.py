"""
Unit tests for utils/pasters.py module.
Tests paste.rs integration functionality.
"""

from src.utils.pasters import upload_to_pasters
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import sys
import os
import httpx

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Add src directory for new architecture
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(
            os.path.dirname(
                os.path.abspath(__file__))),
        "src"))


class TestPasters(unittest.IsolatedAsyncioTestCase):
    """Test pasters.py functionality."""

    def _setup_mock_client(self, mock_client_class, status_code=201, text="https://paste.rs/abc123", ExceptionToRaise=None):
        mock_instance = AsyncMock()
        mock_post = AsyncMock()
        
        if ExceptionToRaise:
            mock_post.side_effect = ExceptionToRaise
        else:
            mock_response = MagicMock()
            mock_response.status_code = status_code
            mock_response.text = text
            mock_post.return_value = mock_response
            
        mock_instance.post = mock_post
        mock_client_class.return_value.__aenter__.return_value = mock_instance
        return mock_post

    @patch("src.services.paste_service.httpx.AsyncClient")
    async def test_successful_upload(self, mock_client_class):
        """Test successful upload to paste.rs."""
        mock_post = self._setup_mock_client(mock_client_class, 201, "https://paste.rs/abc123")

        test_text = "This is a test markdown content"
        result = await upload_to_pasters(test_text)

        self.assertEqual(result, "https://paste.rs/abc123.md")
        mock_post.assert_called_once()

    @patch("src.services.paste_service.httpx.AsyncClient")
    async def test_failed_upload_400(self, mock_client_class):
        """Test failed upload with 400 status code."""
        self._setup_mock_client(mock_client_class, 400, "Bad Request")

        test_text = "This is a test markdown content"
        with self.assertRaises(Exception) as context:
            await upload_to_pasters(test_text)

        self.assertIn("paste.rs error: 400", str(context.exception))
        self.assertIn("Bad Request", str(context.exception))

    @patch("src.services.paste_service.httpx.AsyncClient")
    async def test_failed_upload_500(self, mock_client_class):
        """Test failed upload with 500 status code."""
        self._setup_mock_client(mock_client_class, 500, "Internal Server Error")

        test_text = "This is a test markdown content"
        with self.assertRaises(Exception) as context:
            await upload_to_pasters(test_text)

        self.assertIn("paste.rs error: 500", str(context.exception))
        self.assertIn("Internal Server Error", str(context.exception))

    @patch("src.services.paste_service.httpx.AsyncClient")
    async def test_upload_with_special_characters(self, mock_client_class):
        """Test upload with special characters and unicode."""
        mock_post = self._setup_mock_client(mock_client_class, 201, "https://paste.rs/xyz789")

        test_text = "Special chars: éñ中文🚀\n```python\nprint('hello')\n```"
        result = await upload_to_pasters(test_text)

        self.assertEqual(result, "https://paste.rs/xyz789.md")

    @patch("src.services.paste_service.httpx.AsyncClient")
    async def test_upload_empty_string(self, mock_client_class):
        """Test upload with empty string."""
        self._setup_mock_client(mock_client_class, 201, "https://paste.rs/empty123")

        test_text = ""
        result = await upload_to_pasters(test_text)

        self.assertEqual(result, "https://paste.rs/empty123.md")

    @patch("src.services.paste_service.httpx.AsyncClient")
    async def test_upload_large_text(self, mock_client_class):
        """Test upload with large text content."""
        self._setup_mock_client(mock_client_class, 201, "https://paste.rs/large456")

        test_text = "A" * 5000  # 5000 characters
        result = await upload_to_pasters(test_text)

        self.assertEqual(result, "https://paste.rs/large456.md")

    @patch("src.services.paste_service.httpx.AsyncClient")
    async def test_response_text_stripping(self, mock_client_class):
        """Test that response text is properly stripped of whitespace."""
        self._setup_mock_client(mock_client_class, 201, "  https://paste.rs/whitespace123  \n")

        test_text = "Test content"
        result = await upload_to_pasters(test_text)

        self.assertEqual(result, "https://paste.rs/whitespace123.md")

    @patch("src.services.paste_service.httpx.AsyncClient")
    async def test_network_error(self, mock_client_class):
        """Test handling of network errors."""
        self._setup_mock_client(mock_client_class, ExceptionToRaise=httpx.RequestError("Network error", request=MagicMock()))

        test_text = "Test content"
        with self.assertRaises(Exception) as context:
            await upload_to_pasters(test_text)

        self.assertIn("Failed to upload to paste service", str(context.exception))

if __name__ == "__main__":
    unittest.main()
