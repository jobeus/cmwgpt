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
import json
import os
import stat
import time
import glob

logger = logging.getLogger(__name__)


class StateService:
    """Thread-safe state management for the Discord bot."""

    def __init__(self):
        """Initialize the state service with thread-safe storage."""
        self._conversations: Dict[int, List[Dict[str, Any]]] = {}
        self._models: Dict[int, str] = {}
        self._channel_system_prompts: Dict[int, str] = {}
        # Track channels where bot has been used
        self._active_channels: set[int] = set()
        self._last_git_sha: Optional[str] = None  # Track last known git SHA
        # Track OpenAI response IDs per channel for conversation continuity
        self._response_ids: Dict[int, str] = {}

        # Thread locks for each data structure
        self._conversations_lock = threading.RLock()
        self._models_lock = threading.RLock()
        self._prompts_lock = threading.RLock()
        self._active_channels_lock = threading.RLock()
        self._git_sha_lock = threading.RLock()
        self._response_ids_lock = threading.RLock()

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
            logger.debug(
                f"""Set conversation for channel {channel_id} with {
                len(conversation)} messages"""
            )

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

    # Response ID management for conversation continuity
    def get_response_id(self, channel_id: int) -> Optional[str]:
        """
        Get the last OpenAI response ID for a channel.

        Args:
            channel_id: Discord channel ID

        Returns:
            Last response ID for the channel or None if not found
        """
        with self._response_ids_lock:
            return self._response_ids.get(channel_id)

    def set_response_id(self, channel_id: int, response_id: str) -> None:
        """
        Set the OpenAI response ID for a channel.

        Args:
            channel_id: Discord channel ID
            response_id: OpenAI response ID to store
        """
        with self._response_ids_lock:
            self._response_ids[channel_id] = response_id
            logger.debug(f"Set response ID for channel {channel_id}: {response_id}")

    def clear_response_id(self, channel_id: int) -> None:
        """
        Clear the OpenAI response ID for a channel.

        Args:
            channel_id: Discord channel ID
        """
        with self._response_ids_lock:
            if channel_id in self._response_ids:
                del self._response_ids[channel_id]
                logger.debug(f"Cleared response ID for channel {channel_id}")

    def get_all_response_ids(self) -> Dict[int, str]:
        """
        Get all response IDs (for debugging/admin purposes).

        Returns:
            Dictionary mapping channel IDs to response IDs
        """
        with self._response_ids_lock:
            return self._response_ids.copy()

    # Model management
    def get_model(self, channel_id: int) -> Optional[str]:
        """Get the model setting for a channel."""
        with self._models_lock:
            return self._models.get(channel_id)

    def set_model(self, channel_id: int, model: str) -> None:
        """Set the model for a channel."""
        with self._models_lock:
            self._models[channel_id] = model
            logger.debug(f"Set model for channel {channel_id} to {model}")

    def get_all_models(self) -> Dict[int, str]:
        """Get all model settings."""
        with self._models_lock:
            return self._models.copy()

    # System prompt management
    def get_system_prompt(self, channel_id: int) -> Optional[str]:
        """Get the system prompt for a channel."""
        with self._prompts_lock:
            return self._channel_system_prompts.get(channel_id)

    def set_system_prompt(self, channel_id: int, prompt: str) -> None:
        """Set the system prompt for a channel."""
        with self._prompts_lock:
            self._channel_system_prompts[channel_id] = prompt
            logger.debug(f"Set system prompt for channel {channel_id}")

    def clear_system_prompt(self, channel_id: int) -> None:
        """Clear the system prompt for a channel."""
        with self._prompts_lock:
            if channel_id in self._channel_system_prompts:
                del self._channel_system_prompts[channel_id]
                logger.debug(f"Cleared system prompt for channel {channel_id}")

    def get_all_system_prompts(self) -> Dict[int, str]:
        """Get all system prompts."""
        with self._prompts_lock:
            return self._channel_system_prompts.copy()

    # Utility methods
    def clear_all_data(self) -> None:
        """Clear all stored data (for testing purposes)."""
        with self._conversations_lock, self._models_lock, self._prompts_lock, self._active_channels_lock:
            self._conversations.clear()
            self._models.clear()
            self._channel_system_prompts.clear()
            self._active_channels.clear()
            logger.info("Cleared all state data")

    def get_stats(self) -> Dict[str, int]:
        """
        Get statistics about stored data.

        Returns:
            Dictionary with counts of stored data
        """
        with self._conversations_lock, self._models_lock, self._prompts_lock, self._active_channels_lock:
            return {
                "conversations": len(self._conversations),
                "models": len(self._models),
                "system_prompts": len(self._channel_system_prompts),
                "active_channels": len(self._active_channels),
            }

    # Active channel management
    def mark_channel_active(self, channel_id: int) -> None:
        """
        Mark a channel as active (bot has been used there).

        Args:
            channel_id: Discord channel ID
        """
        with self._active_channels_lock:
            self._active_channels.add(channel_id)
            logger.debug(f"Marked channel {channel_id} as active")

    def get_active_channels(self) -> set[int]:
        """
        Get all channels where the bot has been used.

        Returns:
            Set of channel IDs
        """
        with self._active_channels_lock:
            return self._active_channels.copy()

    def clear_active_channels(self) -> None:
        """Clear the active channels list."""
        with self._active_channels_lock:
            self._active_channels.clear()
            logger.info("Cleared active channels list")

    # Git SHA management
    def get_last_git_sha(self) -> Optional[str]:
        """
        Get the last known git SHA from the previous session.

        Returns:
            Last known git SHA or None if not available
        """
        with self._git_sha_lock:
            return self._last_git_sha

    def set_last_git_sha(self, sha: str) -> None:
        """
        Set the last known git SHA.

        Args:
            sha: Git commit SHA
        """
        with self._git_sha_lock:
            self._last_git_sha = sha
            logger.debug(f"Updated last git SHA to: {sha}")

    def save_state_to_temp_file(self, restart_info: Optional[dict] = None) -> Optional[str]:
        """
        Save current state to a secure temporary file.

        Args:
            restart_info: Optional restart information to include in the state file

        Returns:
            Path to the temporary file, or None if save failed
        """
        try:
            # Create unique filename with timestamp and PID
            timestamp = int(time.time())
            pid = os.getpid()
            temp_filename = f"/tmp/cmwgpt_state_backup_{timestamp}_{pid}.json"

            # Gather all state data under locks
            with self._conversations_lock, self._models_lock, self._prompts_lock, self._active_channels_lock, self._git_sha_lock, self._response_ids_lock:
                state_data = {
                    "conversations": self._conversations.copy(),
                    "models": self._models.copy(),
                    "system_prompts": self._channel_system_prompts.copy(),
                    "active_channels": list(self._active_channels),
                    "response_ids": self._response_ids.copy(),
                    "last_git_sha": self._last_git_sha,
                    "timestamp": timestamp,
                    "pid": pid,
                }

                # Include restart info if provided
                if restart_info:
                    state_data["restart_info"] = restart_info

            # Write to temporary file with secure permissions
            with open(temp_filename, "w", encoding="utf-8") as f:
                json.dump(state_data, f, indent=2)

            # Set restrictive permissions (600 - read/write for owner only)
            os.chmod(temp_filename, stat.S_IRUSR | stat.S_IWUSR)

            logger.debug(f"State saved to temporary file: {temp_filename}")
            return temp_filename

        except (OSError, PermissionError, json.JSONEncodeError) as e:
            logger.error(f"Failed to save state to temporary file: {e}")
            return None

    def load_state_from_temp_files(self) -> bool:
        """
        Load state from any existing temporary files and clean them up.

        Returns:
            True if state was loaded, False otherwise
        """
        try:
            # Find all matching temporary files, excluding restart info files
            pattern = "/tmp/cmwgpt_state_backup_*.json"
            all_temp_files = glob.glob(pattern)

            if not all_temp_files:
                logger.info("No temporary state files found")
                return False

            # Sort by timestamp (newest first)
            all_temp_files.sort(reverse=True)

            for temp_file in all_temp_files:
                try:
                    logger.info(f"Attempting to load state from: {temp_file}")

                    with open(temp_file, "r", encoding="utf-8") as f:
                        state_data = json.load(f)

                    # Validate the data structure
                    if not all(key in state_data for key in ["conversations", "models", "system_prompts"]):
                        logger.warning(f"Invalid state file format: {temp_file}")
                        continue

                    # Load the state under locks
                    with self._conversations_lock, self._models_lock, self._prompts_lock, self._active_channels_lock, self._git_sha_lock, self._response_ids_lock:
                        # Convert string keys back to integers for channel IDs
                        self._conversations = {int(k): v for k, v in state_data["conversations"].items()}
                        self._models = {int(k): v for k, v in state_data["models"].items()}
                        self._channel_system_prompts = {int(k): v for k, v in state_data["system_prompts"].items()}
                        # Load active channels (may not exist in older state
                        # files)
                        if "active_channels" in state_data:
                            self._active_channels = set(state_data["active_channels"])
                        else:
                            # If not present, derive from existing
                            # conversations and models
                            self._active_channels = set(self._conversations.keys()) | set(self._models.keys())

                        # Load response IDs (may not exist in older state files)
                        if "response_ids" in state_data:
                            self._response_ids = {int(k): v for k, v in state_data["response_ids"].items()}
                        else:
                            self._response_ids = {}

                        # Load last git SHA (may not exist in older state
                        # files)
                        self._last_git_sha = state_data.get("last_git_sha")

                    logger.debug(f"Successfully loaded state from: {temp_file}")
                    sha_info = ""
                    if self._last_git_sha:
                        sha_info = f", last git SHA: {self._last_git_sha}"
                    logger.info(
                        f"""Restored {
                        len(self._conversations)} conversations, {
                        len(self._models)} models, {
                        len(self._channel_system_prompts)} system prompts, {
                        len(self._active_channels)} active channels, {
                        len(self._response_ids)} response IDs{sha_info}"""
                    )

                    # Successfully loaded, break out of loop
                    break

                except (OSError, json.JSONDecodeError, KeyError, ValueError) as e:
                    logger.error(f"Failed to load state from {temp_file}: {e}")
                    continue

            # Clean up all temporary files (both main state files and restart
            # info files)
            for temp_file in all_temp_files:
                try:
                    os.remove(temp_file)
                    logger.debug(f"Cleaned up temporary file: {temp_file}")
                except OSError as e:
                    logger.error(f"Failed to clean up temporary file {temp_file}: {e}")

            return True

        except (OSError, ValueError) as e:
            logger.error(f"Error during state loading: {e}")
            return False

    def cleanup_temp_files(self) -> None:
        """
        Clean up any leftover temporary state files.
        """
        try:
            # Clean up all state backup files (including any old restart info
            # files)
            pattern = "/tmp/cmwgpt_state_backup_*.json"
            temp_files = glob.glob(pattern)

            for temp_file in temp_files:
                try:
                    os.remove(temp_file)
                    logger.info(f"Cleaned up leftover temporary file: {temp_file}")
                except OSError as e:
                    logger.error(f"Failed to clean up temporary file {temp_file}: {e}")

        except (OSError, ValueError) as e:
            logger.error(f"Error during temp file cleanup: {e}")


# Global state service instance
state_service = StateService()
