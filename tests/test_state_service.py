"""
Unit tests for the StateService class.
Tests thread-safe state management functionality.
"""

from src.services.state_service import StateService
import unittest
import threading
import sys
import os

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestStateService(unittest.TestCase):
    """Test thread-safe state management."""

    def setUp(self):
        """Set up test environment."""
        self.state_service = StateService()

    def tearDown(self):
        """Clean up test environment."""
        self.state_service.clear_all_data()

    def test_conversation_management(self):
        """Test conversation storage and retrieval."""
        channel_id = 12345
        conversation = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        # Test setting and getting conversation
        self.state_service.set_conversation(channel_id, conversation)
        retrieved = self.state_service.get_conversation(channel_id)

        self.assertEqual(retrieved, conversation)
        self.assertIsNot(retrieved, conversation)  # Should be a copy

        # Test getting non-existent conversation
        self.assertIsNone(self.state_service.get_conversation(99999))

        # Test adding message to conversation
        new_message = {"role": "user", "content": "How are you?"}
        self.state_service.add_message_to_conversation(channel_id, new_message)

        updated = self.state_service.get_conversation(channel_id)
        self.assertEqual(len(updated), 4)
        self.assertEqual(updated[-1], new_message)

        # Test clearing conversation
        self.state_service.clear_conversation(channel_id)
        self.assertIsNone(self.state_service.get_conversation(channel_id))

    def test_model_management(self):
        """Test model storage and retrieval."""
        channel_id = 12345
        model = "gpt-5-mini"

        # Test setting and getting model
        self.state_service.set_model(channel_id, model)
        retrieved = self.state_service.get_model(channel_id)

        self.assertEqual(retrieved, model)

        # Test getting non-existent model
        self.assertIsNone(self.state_service.get_model(99999))

        # Test getting all models
        all_models = self.state_service.get_all_models()
        self.assertEqual(all_models[channel_id], model)

    def test_system_prompt_management(self):
        """Test system prompt storage and retrieval."""
        channel_id = 12345
        prompt = "You are a coding assistant."

        # Test setting and getting system prompt
        self.state_service.set_system_prompt(channel_id, prompt)
        retrieved = self.state_service.get_system_prompt(channel_id)

        self.assertEqual(retrieved, prompt)

        # Test getting non-existent prompt
        self.assertIsNone(self.state_service.get_system_prompt(99999))

        # Test clearing system prompt
        self.state_service.clear_system_prompt(channel_id)
        self.assertIsNone(self.state_service.get_system_prompt(channel_id))

        # Test getting all system prompts
        self.state_service.set_system_prompt(channel_id, prompt)
        all_prompts = self.state_service.get_all_system_prompts()
        self.assertEqual(all_prompts[channel_id], prompt)

    def test_thread_safety(self):
        """Test thread safety of state operations."""
        channel_id = 12345
        num_threads = 10
        operations_per_thread = 50

        results = []
        errors = []

        def worker_thread(thread_id):
            """Worker function for thread safety test."""
            try:
                for i in range(operations_per_thread):
                    # Set conversation
                    conversation = [{"role": "user", "content": f"Thread {thread_id}, message {i}"}]
                    self.state_service.set_conversation(channel_id + thread_id, conversation)

                    # Set model
                    model = f"model-{thread_id}-{i}"
                    self.state_service.set_model(channel_id + thread_id, model)

                    # Set system prompt
                    prompt = f"Prompt for thread {thread_id}, iteration {i}"
                    self.state_service.set_system_prompt(channel_id + thread_id, prompt)

                    # Read back values
                    retrieved_conv = self.state_service.get_conversation(channel_id + thread_id)
                    retrieved_model = self.state_service.get_model(channel_id + thread_id)
                    retrieved_prompt = self.state_service.get_system_prompt(channel_id + thread_id)

                    # Verify values
                    if retrieved_conv != conversation:
                        errors.append(f"Conversation mismatch in thread {thread_id}")
                    if retrieved_model != model:
                        errors.append(f"Model mismatch in thread {thread_id}")
                    if retrieved_prompt != prompt:
                        errors.append(f"Prompt mismatch in thread {thread_id}")

                results.append(f"Thread {thread_id} completed successfully")

            except Exception as e:
                errors.append(f"Thread {thread_id} failed: {e}")

        # Start threads
        threads = []
        for i in range(num_threads):
            thread = threading.Thread(target=worker_thread, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Check results
        self.assertEqual(len(results), num_threads, f"Not all threads completed. Errors: {errors}")
        self.assertEqual(len(errors), 0, f"Thread safety errors: {errors}")

    def test_stats_and_utilities(self):
        """Test utility methods and statistics."""
        # Test initial stats
        stats = self.state_service.get_stats()
        self.assertEqual(stats["conversations"], 0)
        self.assertEqual(stats["models"], 0)
        self.assertEqual(stats["system_prompts"], 0)

        # Add some data
        self.state_service.set_conversation(1, [{"role": "user", "content": "test"}])
        self.state_service.set_model(1, "test-model")
        self.state_service.set_system_prompt(1, "test-prompt")

        # Check updated stats
        stats = self.state_service.get_stats()
        self.assertEqual(stats["conversations"], 1)
        self.assertEqual(stats["models"], 1)
        self.assertEqual(stats["system_prompts"], 1)

        # Test clear all data
        self.state_service.clear_all_data()
        stats = self.state_service.get_stats()
        self.assertEqual(stats["conversations"], 0)
        self.assertEqual(stats["models"], 0)
        self.assertEqual(stats["system_prompts"], 0)

    def test_get_all_methods(self):
        """Test methods that return all stored data."""
        # Add test data
        channels = [1, 2, 3]
        for i, channel_id in enumerate(channels):
            self.state_service.set_conversation(channel_id, [{"role": "user", "content": f"conv {i}"}])
            self.state_service.set_model(channel_id, f"model-{i}")
            self.state_service.set_system_prompt(channel_id, f"prompt-{i}")

        # Test get_all methods
        all_conversations = self.state_service.get_all_conversations()
        all_models = self.state_service.get_all_models()
        all_prompts = self.state_service.get_all_system_prompts()

        self.assertEqual(len(all_conversations), 3)
        self.assertEqual(len(all_models), 3)
        self.assertEqual(len(all_prompts), 3)

        # Verify data integrity
        for i, channel_id in enumerate(channels):
            self.assertEqual(all_conversations[channel_id][0]["content"], f"conv {i}")
            self.assertEqual(all_models[channel_id], f"model-{i}")
            self.assertEqual(all_prompts[channel_id], f"prompt-{i}")

    def test_add_message_to_new_conversation(self):
        """Test adding message to a channel with no existing conversation."""
        channel_id = 12345
        message = {"role": "user", "content": "First message"}

        # Add message to non-existent conversation
        self.state_service.add_message_to_conversation(channel_id, message)

        # Should create new conversation with the message
        conversation = self.state_service.get_conversation(channel_id)
        self.assertEqual(len(conversation), 1)
        self.assertEqual(conversation[0], message)

    def test_response_id_management(self):
        """Test response ID storage and retrieval for conversation continuity."""
        channel_id = 12345
        response_id = "resp_abc123"

        # Test getting non-existent response ID
        self.assertIsNone(self.state_service.get_response_id(channel_id))

        # Test setting and getting response ID
        self.state_service.set_response_id(channel_id, response_id)
        retrieved = self.state_service.get_response_id(channel_id)
        self.assertEqual(retrieved, response_id)

        # Test updating response ID
        new_response_id = "resp_def456"
        self.state_service.set_response_id(channel_id, new_response_id)
        retrieved = self.state_service.get_response_id(channel_id)
        self.assertEqual(retrieved, new_response_id)

        # Test clearing response ID
        self.state_service.clear_response_id(channel_id)
        self.assertIsNone(self.state_service.get_response_id(channel_id))

        # Test clearing non-existent response ID (should not raise error)
        self.state_service.clear_response_id(99999)

    def test_response_id_isolation(self):
        """Test that response IDs are isolated per channel."""
        channel1 = 12345
        channel2 = 67890
        response_id1 = "resp_abc123"
        response_id2 = "resp_def456"

        # Set different response IDs for different channels
        self.state_service.set_response_id(channel1, response_id1)
        self.state_service.set_response_id(channel2, response_id2)

        # Verify isolation
        self.assertEqual(self.state_service.get_response_id(channel1), response_id1)
        self.assertEqual(self.state_service.get_response_id(channel2), response_id2)

        # Clear one channel's response ID
        self.state_service.clear_response_id(channel1)
        self.assertIsNone(self.state_service.get_response_id(channel1))
        self.assertEqual(self.state_service.get_response_id(channel2), response_id2)

    def test_get_all_response_ids(self):
        """Test getting all response IDs."""
        channels = [12345, 67890, 11111]
        response_ids = ["resp_abc123", "resp_def456", "resp_ghi789"]

        # Set response IDs for multiple channels
        for channel, response_id in zip(channels, response_ids):
            self.state_service.set_response_id(channel, response_id)

        # Get all response IDs
        all_response_ids = self.state_service.get_all_response_ids()

        # Verify all response IDs are present
        self.assertEqual(len(all_response_ids), 3)
        for channel, response_id in zip(channels, response_ids):
            self.assertEqual(all_response_ids[channel], response_id)

    def test_response_id_persistence(self):
        """Test that response IDs are included in state persistence."""
        import tempfile
        import json
        import os

        channel_id = 12345
        response_id = "resp_abc123"
        conversation = [{"role": "user", "content": "Hello"}]

        # Set up state
        self.state_service.set_response_id(channel_id, response_id)
        self.state_service.set_conversation(channel_id, conversation)

        # Save state to temp file
        temp_file = self.state_service.save_state_to_temp_file()
        self.assertIsNotNone(temp_file)

        try:
            # Verify the temp file contains response IDs
            with open(temp_file, 'r', encoding='utf-8') as f:
                state_data = json.load(f)

            self.assertIn('response_ids', state_data)
            self.assertEqual(state_data['response_ids'][str(channel_id)], response_id)

            # Create new state service and load from temp file
            new_state_service = StateService()
            success = new_state_service.load_state_from_temp_files()
            self.assertTrue(success)

            # Verify response ID was restored
            restored_response_id = new_state_service.get_response_id(channel_id)
            self.assertEqual(restored_response_id, response_id)

            # Verify other data was also restored
            restored_conversation = new_state_service.get_conversation(channel_id)
            self.assertEqual(restored_conversation, conversation)

        finally:
            # Clean up temp file
            if os.path.exists(temp_file):
                os.remove(temp_file)


if __name__ == "__main__":
    unittest.main()
