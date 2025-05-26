"""
Unit tests for utils/pasters.py module.
Tests paste.rs integration functionality.
"""

from utils.pasters import upload_to_pasters
import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestPasters(unittest.TestCase):
    """Test pasters.py functionality."""

    @patch('utils.pasters.requests.post')
    def test_successful_upload(self, mock_post):
        """Test successful upload to paste.rs."""
        # Mock successful response
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.text = 'https://paste.rs/abc123'
        mock_post.return_value = mock_response

        # Test upload
        test_text = "This is a test markdown content"
        result = upload_to_pasters(test_text)

        # Verify result
        self.assertEqual(result, 'https://paste.rs/abc123.md')

        # Verify request was made correctly
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        self.assertEqual(call_args[0][0], "https://paste.rs")

        # Verify the data parameter is a BytesIO object with correct content
        data_param = call_args[1]['data']
        self.assertEqual(data_param.read(), test_text.encode('utf-8'))

    @patch('utils.pasters.requests.post')
    def test_failed_upload_400(self, mock_post):
        """Test failed upload with 400 status code."""
        # Mock failed response
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = 'Bad Request'
        mock_post.return_value = mock_response

        # Test upload should raise exception
        test_text = "This is a test markdown content"
        with self.assertRaises(Exception) as context:
            upload_to_pasters(test_text)

        self.assertIn('paste.rs error: 400', str(context.exception))
        self.assertIn('Bad Request', str(context.exception))

    @patch('utils.pasters.requests.post')
    def test_failed_upload_500(self, mock_post):
        """Test failed upload with 500 status code."""
        # Mock server error response
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = 'Internal Server Error'
        mock_post.return_value = mock_response

        # Test upload should raise exception
        test_text = "This is a test markdown content"
        with self.assertRaises(Exception) as context:
            upload_to_pasters(test_text)

        self.assertIn('paste.rs error: 500', str(context.exception))
        self.assertIn('Internal Server Error', str(context.exception))

    @patch('utils.pasters.requests.post')
    def test_upload_with_special_characters(self, mock_post):
        """Test upload with special characters and unicode."""
        # Mock successful response
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.text = 'https://paste.rs/xyz789'
        mock_post.return_value = mock_response

        # Test with special characters
        test_text = "Special chars: éñ中文🚀\n```python\nprint('hello')\n```"
        result = upload_to_pasters(test_text)

        # Verify result
        self.assertEqual(result, 'https://paste.rs/xyz789.md')

        # Verify encoding
        call_args = mock_post.call_args
        data_param = call_args[1]['data']
        self.assertEqual(data_param.read(), test_text.encode('utf-8'))

    @patch('utils.pasters.requests.post')
    def test_upload_empty_string(self, mock_post):
        """Test upload with empty string."""
        # Mock successful response
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.text = 'https://paste.rs/empty123'
        mock_post.return_value = mock_response

        # Test with empty string
        test_text = ""
        result = upload_to_pasters(test_text)

        # Verify result
        self.assertEqual(result, 'https://paste.rs/empty123.md')

    @patch('utils.pasters.requests.post')
    def test_upload_large_text(self, mock_post):
        """Test upload with large text content."""
        # Mock successful response
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.text = 'https://paste.rs/large456'
        mock_post.return_value = mock_response

        # Test with large text (simulate Discord's 2000+ character limit)
        test_text = "A" * 5000  # 5000 characters
        result = upload_to_pasters(test_text)

        # Verify result
        self.assertEqual(result, 'https://paste.rs/large456.md')

    @patch('utils.pasters.requests.post')
    def test_response_text_stripping(self, mock_post):
        """Test that response text is properly stripped of whitespace."""
        # Mock response with whitespace
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.text = '  https://paste.rs/whitespace123  \n'
        mock_post.return_value = mock_response

        # Test upload
        test_text = "Test content"
        result = upload_to_pasters(test_text)

        # Verify whitespace is stripped
        self.assertEqual(result, 'https://paste.rs/whitespace123.md')

    @patch('utils.pasters.requests.post')
    def test_network_error(self, mock_post):
        """Test handling of network errors."""
        # Mock network error
        mock_post.side_effect = Exception("Network error")

        # Test upload should raise the network exception
        test_text = "Test content"
        with self.assertRaises(Exception) as context:
            upload_to_pasters(test_text)

        self.assertEqual(str(context.exception), "Network error")


if __name__ == '__main__':
    unittest.main()
