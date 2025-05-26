"""
Thread-safe state management service for the Discord bot.

This service provides thread-safe access to bot state including:
- Conversation history per channel
- Model settings per channel
- System prompts per channel

All operations are protected by locks to ensure thread safety.
"""

import threading
from typing import Dict, List, Any, Optional
import logging

logger = logging.getLogger(__name__)


class StateService:
    """Thread-safe state management for the Discord bot."""

    def __init__(self):
        """Initialize the state service with thread-safe storage."""
        self._conversations: Dict[int, List[Dict[str, Any]]] = {}
        self._models: Dict[int, str] = {}
        self._channel_system_prompts: Dict[int, str] = {}

        # Thread locks for each data structure
        self._conversations_lock = threading.RLock()
        self._models_lock = threading.RLock()
        self._prompts_lock = threading.RLock()

        logger.info("StateService initialized with thread-safe storage")

    # Conversation management
    def get_conversation(self, channel_id: int) -> Optional[List[Dict[str, Any]]]:
        """
        Get conversation history for a channel.

        Args:
            channel_id: Discord channel ID

        Returns:
            List of conversation messages or None if not found
        """
        with self._conversations_lock:
            return self._conversations.get(channel_id)

    def set_conversation(self, channel_id: int, conversation: List[Dict[str, Any]]) -> None:
        """
        Set conversation history for a channel.

        Args:
            channel_id: Discord channel ID
            conversation: List of conversation messages
        """
        with self._conversations_lock:
            self._conversations[channel_id] = conversation.copy()
            logger.debug(f"Set conversation for channel {channel_id} with {len(conversation)} messages")

    def add_message_to_conversation(self, channel_id: int, message: Dict[str, Any]) -> None:
        """
        Add a message to the conversation history for a channel.

        Args:
            channel_id: Discord channel ID
            message: Message dictionary to add
        """
        with self._conversations_lock:
            if channel_id not in self._conversations:
                self._conversations[channel_id] = []
            self._conversations[channel_id].append(message)
            logger.debug(f"Added message to conversation for channel {channel_id}")

    def clear_conversation(self, channel_id: int) -> None:
        """
        Clear conversation history for a channel.

        Args:
            channel_id: Discord channel ID
        """
        with self._conversations_lock:
            if channel_id in self._conversations:
                del self._conversations[channel_id]
                logger.debug(f"Cleared conversation for channel {channel_id}")

    def get_all_conversations(self) -> Dict[int, List[Dict[str, Any]]]:
        """
        Get all conversation histories (for debugging/admin purposes).

        Returns:
            Dictionary mapping channel IDs to conversation histories
        """
        with self._conversations_lock:
            return {k: v.copy() for k, v in self._conversations.items()}

    # Model management
    def get_model(self, channel_id: int) -> Optional[str]:
        """
        Get the model setting for a channel.

        Args:
            channel_id: Discord channel ID

        Returns:
            Model name or None if not set
        """
        with self._models_lock:
            return self._models.get(channel_id)

    def set_model(self, channel_id: int, model: str) -> None:
        """
        Set the model for a channel.

        Args:
            channel_id: Discord channel ID
            model: Model name to set
        """
        with self._models_lock:
            self._models[channel_id] = model
            logger.debug(f"Set model for channel {channel_id} to {model}")

    def get_all_models(self) -> Dict[int, str]:
        """
        Get all model settings (for debugging/admin purposes).

        Returns:
            Dictionary mapping channel IDs to model names
        """
        with self._models_lock:
            return self._models.copy()

    # System prompt management
    def get_system_prompt(self, channel_id: int) -> Optional[str]:
        """
        Get the system prompt for a channel.

        Args:
            channel_id: Discord channel ID

        Returns:
            System prompt or None if not set
        """
        with self._prompts_lock:
            return self._channel_system_prompts.get(channel_id)

    def set_system_prompt(self, channel_id: int, prompt: str) -> None:
        """
        Set the system prompt for a channel.

        Args:
            channel_id: Discord channel ID
            prompt: System prompt to set
        """
        with self._prompts_lock:
            self._channel_system_prompts[channel_id] = prompt
            logger.debug(f"Set system prompt for channel {channel_id}")

    def clear_system_prompt(self, channel_id: int) -> None:
        """
        Clear the system prompt for a channel.

        Args:
            channel_id: Discord channel ID
        """
        with self._prompts_lock:
            if channel_id in self._channel_system_prompts:
                del self._channel_system_prompts[channel_id]
                logger.debug(f"Cleared system prompt for channel {channel_id}")

    def get_all_system_prompts(self) -> Dict[int, str]:
        """
        Get all system prompts (for debugging/admin purposes).

        Returns:
            Dictionary mapping channel IDs to system prompts
        """
        with self._prompts_lock:
            return self._channel_system_prompts.copy()

    # Utility methods
    def clear_all_data(self) -> None:
        """Clear all stored data (for testing purposes)."""
        with self._conversations_lock, self._models_lock, self._prompts_lock:
            self._conversations.clear()
            self._models.clear()
            self._channel_system_prompts.clear()
            logger.info("Cleared all state data")

    def get_stats(self) -> Dict[str, int]:
        """
        Get statistics about stored data.

        Returns:
            Dictionary with counts of stored data
        """
        with self._conversations_lock, self._models_lock, self._prompts_lock:
            return {
                "conversations": len(self._conversations),
                "models": len(self._models),
                "system_prompts": len(self._channel_system_prompts),
            }


# Global state service instance
state_service = StateService()
