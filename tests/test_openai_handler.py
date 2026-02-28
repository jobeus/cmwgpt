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
        # Mock response for responses API
        mock_content = MagicMock()
        mock_content.type = "output_text"
        mock_content.text = "Hello! How can I help you today?"

        mock_message = MagicMock()
        mock_message.type = "message"
        mock_message.content = [mock_content]

        mock_response = MagicMock()
        mock_response.output = [mock_message]
        self.mock_client.responses.create.return_value = mock_response

        # Test parameters
        model = "gpt-5-nano"
        messages = [{"role": "user", "content": "Hello"}]
        system_prompt = "You are a helpful assistant."

        # Call function
        result = await openai_service.get_chat_completion(model, messages, system_prompt)

        # Verify result
        self.assertEqual(result, "Hello! How can I help you today?")

        # Verify API call - should use responses.create with input and
        # instructions
        expected_input = [{"role": "user", "content": "Hello"}]
        self.mock_client.responses.create.assert_called_once_with(
            model=model,
            input=expected_input,
            instructions=system_prompt,
            tools=unittest.mock.ANY,
            tool_choice="auto")

    async def test_get_chat_completion_different_models(self):
        """Test chat completion with different models."""
        models_to_test = ["gpt-5", "gpt-5-mini", "gpt-5-nano"]

        for model in models_to_test:
            with self.subTest(model=model):
                # Mock response for responses API
                mock_content = MagicMock()
                mock_content.type = "output_text"
                mock_content.text = f"Response from {model}"

                mock_message = MagicMock()
                mock_message.type = "message"
                mock_message.content = [mock_content]

                mock_response = MagicMock()
                mock_response.output = [mock_message]
                self.mock_client.responses.create.return_value = mock_response

                # Test parameters
                messages = [{"role": "user", "content": "Test"}]

                # Call function
                result = await openai_service.get_chat_completion(model, messages)

                # Verify result
                self.assertEqual(result, f"Response from {model}")





    async def test_chat_completion_empty_messages(self):
        """Test chat completion with empty messages list."""
        # Mock response for responses API
        mock_content = MagicMock()
        mock_content.type = "output_text"
        mock_content.text = "I'm ready to help!"

        mock_message = MagicMock()
        mock_message.type = "message"
        mock_message.content = [mock_content]

        mock_response = MagicMock()
        mock_response.output = [mock_message]
        self.mock_client.responses.create.return_value = mock_response

        # Test with empty messages
        result = await openai_service.get_chat_completion("gpt-5-nano", [])

        # Verify it still works
        self.assertEqual(result, "I'm ready to help!")

    async def test_chat_completion_complex_messages(self):
        """Test chat completion with complex message structure."""
        # Mock response for responses API
        mock_content = MagicMock()
        mock_content.type = "output_text"
        mock_content.text = "Complex response"

        mock_message = MagicMock()
        mock_message.type = "message"
        mock_message.content = [mock_content]

        mock_response = MagicMock()
        mock_response.output = [mock_message]
        self.mock_client.responses.create.return_value = mock_response

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
            {"role": "assistant", "content": [{"type": "output_text", "text": "I can see your image!"}]},
            {"role": "user", "content": [{"type": "input_text", "text": "What do you think?"}]},
        ]
        system_prompt = "You are a helpful assistant."

        result = await openai_service.get_chat_completion("gpt-5-mini", messages, system_prompt)

        # Verify it handles complex structure
        self.assertEqual(result, "Complex response")

        # Expected input should exclude system messages (system prompt goes to
        # instructions)
        expected_input = [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Hello"},
                    {"type": "input_image", "image_url": "http://example.com/image.jpg"},
                ],
            },
            {"role": "assistant", "content": [{"type": "output_text", "text": "I can see your image!"}]},
            {"role": "user", "content": [{"type": "input_text", "text": "What do you think?"}]},
        ]
        self.mock_client.responses.create.assert_called_once_with(
            model="gpt-5-mini",
            input=expected_input,
            instructions=system_prompt,
            tools=unittest.mock.ANY,
            tool_choice="auto")

    async def test_conversation_continuity_with_previous_response_id(self):
        """Test that previous_response_id is included when available."""
        from src.services.state_service import StateService

        # Mock response for responses API
        mock_content = MagicMock()
        mock_content.type = "output_text"
        mock_content.text = "This is a follow-up response."

        mock_message = MagicMock()
        mock_message.type = "message"
        mock_message.content = [mock_content]

        mock_response = MagicMock()
        mock_response.id = "resp_new123"
        mock_response.output = [mock_message]
        self.mock_client.responses.create.return_value = mock_response

        # Set up state service with previous response ID
        state_service = StateService()
        channel_id = 12345
        previous_response_id = "resp_prev456"
        state_service.set_response_id(channel_id, previous_response_id)

        # Test parameters
        model = "gpt-5-nano"
        messages = [{"role": "user", "content": [
            {"type": "input_text", "text": "Follow up question"}]}]
        system_prompt = "You are a helpful assistant."

        # Call function
        result = await openai_service.get_chat_completion(
            model, messages, system_prompt, channel_id=channel_id, state_service=state_service
        )

        # Verify result
        self.assertEqual(result, "This is a follow-up response.")

        # Verify API was called with previous_response_id
        self.mock_client.responses.create.assert_called_once()
        call_args = self.mock_client.responses.create.call_args
        self.assertIn('previous_response_id', call_args.kwargs)
        self.assertEqual(
            call_args.kwargs['previous_response_id'],
            previous_response_id)

        # Verify new response ID was stored
        stored_response_id = state_service.get_response_id(channel_id)
        self.assertEqual(stored_response_id, "resp_new123")

    async def test_conversation_continuity_without_previous_response_id(self):
        """Test that previous_response_id is not included when not available."""
        from src.services.state_service import StateService

        # Mock response for responses API
        mock_content = MagicMock()
        mock_content.type = "output_text"
        mock_content.text = "This is a first response."

        mock_message = MagicMock()
        mock_message.type = "message"
        mock_message.content = [mock_content]

        mock_response = MagicMock()
        mock_response.id = "resp_first123"
        mock_response.output = [mock_message]
        self.mock_client.responses.create.return_value = mock_response

        # Set up state service without previous response ID
        state_service = StateService()
        channel_id = 12345

        # Test parameters
        model = "gpt-5-nano"
        messages = [{"role": "user", "content": [
            {"type": "input_text", "text": "First question"}]}]
        system_prompt = "You are a helpful assistant."

        # Call function
        result = await openai_service.get_chat_completion(
            model, messages, system_prompt, channel_id=channel_id, state_service=state_service
        )

        # Verify result
        self.assertEqual(result, "This is a first response.")

        # Verify API was called without previous_response_id
        self.mock_client.responses.create.assert_called_once()
        call_args = self.mock_client.responses.create.call_args
        self.assertNotIn('previous_response_id', call_args.kwargs)

        # Verify new response ID was stored
        stored_response_id = state_service.get_response_id(channel_id)
        self.assertEqual(stored_response_id, "resp_first123")

    async def test_response_id_storage_helper(self):
        """Test the _extract_response_text_and_store_id helper method."""
        from src.services.state_service import StateService

        # Set up state service
        state_service = StateService()
        channel_id = 12345

        # Mock response with ID and content
        mock_content = MagicMock()
        mock_content.type = "output_text"
        mock_content.text = "Test response text"

        mock_message = MagicMock()
        mock_message.type = "message"
        mock_message.content = [mock_content]

        mock_response = MagicMock()
        mock_response.id = "resp_test123"
        mock_response.output = [mock_message]

        # Test extracting response text and storing ID
        result = openai_service._extract_response_text_and_store_id(
            mock_response, channel_id, state_service)

        # Verify response text was extracted
        self.assertEqual(result, "Test response text")

        # Verify response ID was stored
        stored_response_id = state_service.get_response_id(channel_id)
        self.assertEqual(stored_response_id, "resp_test123")

        # Test with None response (should not crash)
        result = openai_service._extract_response_text_and_store_id(
            None, channel_id, state_service)
        self.assertIsNone(result)

        # Test with None channel_id (should not crash)
        result = openai_service._extract_response_text_and_store_id(
            mock_response, None, state_service)
        self.assertEqual(result, "Test response text")

        # Test with None state_service (should not crash)
        result = openai_service._extract_response_text_and_store_id(
            mock_response, channel_id, None)
        self.assertEqual(result, "Test response text")


if __name__ == "__main__":
    unittest.main()
