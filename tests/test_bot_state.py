"""
Unit tests for bot_state.py module.
Tests in-memory storage functionality.
"""

import unittest
import sys
import os

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestBotState(unittest.TestCase):
    """Test bot state management."""

    def setUp(self):
        """Set up test environment."""
        # Import and reset bot_state
        import bot_state

        bot_state.conversations.clear()
        bot_state.models.clear()
        bot_state.channel_system_prompts.clear()
        self.bot_state = bot_state

    def test_initial_state(self):
        """Test that initial state is empty."""
        self.assertEqual(len(self.bot_state.conversations), 0)
        self.assertEqual(len(self.bot_state.models), 0)
        self.assertEqual(len(self.bot_state.channel_system_prompts), 0)

    def test_conversations_storage(self):
        """Test conversation storage functionality."""
        channel_id = 12345
        conversation = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        # Store conversation
        self.bot_state.conversations[channel_id] = conversation

        # Verify storage
        self.assertEqual(len(self.bot_state.conversations), 1)
        self.assertIn(channel_id, self.bot_state.conversations)
        self.assertEqual(
            self.bot_state.conversations[channel_id],
            conversation)

    def test_models_storage(self):
        """Test model storage functionality."""
        channel_id = 12345
        model = "gpt-4.1-mini"

        # Store model
        self.bot_state.models[channel_id] = model

        # Verify storage
        self.assertEqual(len(self.bot_state.models), 1)
        self.assertIn(channel_id, self.bot_state.models)
        self.assertEqual(self.bot_state.models[channel_id], model)

    def test_channel_system_prompts_storage(self):
        """Test channel system prompts storage functionality."""
        channel_id = 12345
        system_prompt = "You are a coding assistant."

        # Store system prompt
        self.bot_state.channel_system_prompts[channel_id] = system_prompt

        # Verify storage
        self.assertEqual(len(self.bot_state.channel_system_prompts), 1)
        self.assertIn(channel_id, self.bot_state.channel_system_prompts)
        self.assertEqual(
            self.bot_state.channel_system_prompts[channel_id],
            system_prompt)

    def test_multiple_channels(self):
        """Test storage for multiple channels."""
        channel_ids = [12345, 67890, 11111]

        for i, channel_id in enumerate(channel_ids):
            self.bot_state.conversations[channel_id] = [
                {"role": "system", "content": f"Prompt {i}"}]
            self.bot_state.models[channel_id] = f"model-{i}"
            self.bot_state.channel_system_prompts[channel_id] = f"System prompt {i}"

        # Verify all channels are stored
        self.assertEqual(len(self.bot_state.conversations), 3)
        self.assertEqual(len(self.bot_state.models), 3)
        self.assertEqual(len(self.bot_state.channel_system_prompts), 3)

        # Verify individual channel data
        for i, channel_id in enumerate(channel_ids):
            self.assertIn(channel_id, self.bot_state.conversations)
            self.assertIn(channel_id, self.bot_state.models)
            self.assertIn(channel_id, self.bot_state.channel_system_prompts)
            self.assertEqual(self.bot_state.models[channel_id], f"model-{i}")

    def test_conversation_modification(self):
        """Test modifying existing conversations."""
        channel_id = 12345
        initial_conversation = [{"role": "system", "content": "Initial"}]

        # Store initial conversation
        self.bot_state.conversations[channel_id] = initial_conversation

        # Modify conversation
        self.bot_state.conversations[channel_id].append(
            {"role": "user", "content": "Hello"})
        self.bot_state.conversations[channel_id].append(
            {"role": "assistant", "content": "Hi!"})

        # Verify modification
        self.assertEqual(len(self.bot_state.conversations[channel_id]), 3)
        self.assertEqual(
            self.bot_state.conversations[channel_id][1]["content"],
            "Hello")
        self.assertEqual(
            self.bot_state.conversations[channel_id][2]["content"], "Hi!")

    def test_data_types(self):
        """Test that the data structures have correct types."""
        self.assertIsInstance(self.bot_state.conversations, dict)
        self.assertIsInstance(self.bot_state.models, dict)
        self.assertIsInstance(self.bot_state.channel_system_prompts, dict)

    def test_channel_isolation(self):
        """Test that different channels don't interfere with each other."""
        channel1 = 12345
        channel2 = 67890

        # Set different data for each channel
        self.bot_state.conversations[channel1] = [
            {"role": "system", "content": "Channel 1"}]
        self.bot_state.conversations[channel2] = [
            {"role": "system", "content": "Channel 2"}]

        self.bot_state.models[channel1] = "model-1"
        self.bot_state.models[channel2] = "model-2"

        # Verify isolation
        self.assertNotEqual(
            self.bot_state.conversations[channel1],
            self.bot_state.conversations[channel2])
        self.assertNotEqual(
            self.bot_state.models[channel1],
            self.bot_state.models[channel2])


if __name__ == "__main__":
    unittest.main()
