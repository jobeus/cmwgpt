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
        # Consolidated channel data structure - all per-channel data in one
        # dict
        self._channel_data: Dict[int, Dict[str, Any]] = {}
        # Global data not tied to specific channels
        self._active_channels: set[int] = set()
        self._last_git_sha: Optional[str] = None
        self._death_settings: Optional[Dict[str, Any]] = None

        # Single lock for all channel data operations (simpler and more
        # efficient)
        self._channel_data_lock = threading.RLock()
        self._global_data_lock = threading.RLock()
        
        self._settings_file = os.environ.get("CMWGPT_SETTINGS_FILE", ".cache/cmwgpt_settings.json")
        self._load_settings()

        logger.info(
            "StateService initialized with optimized thread-safe storage")

    # Optimized channel data management - all operations in one place
    def _ensure_channel_data(self, channel_id: int) -> None:
        """Ensure channel data structure exists."""
        if channel_id not in self._channel_data:
            self._channel_data[channel_id] = {
                'conversation': [],
                'model': None,
                'draw_model': None,
                'edit_model': None,
                'system_prompt': None,
                'response_id': None
            }

    def get_conversation(
            self, channel_id: int) -> Optional[List[Dict[str, Any]]]:
        """Get conversation history for a channel."""
        with self._channel_data_lock:
            if channel_id not in self._channel_data:
                return None
            conversation = self._channel_data[channel_id]['conversation']
            return conversation if conversation else None

    def set_conversation(self, channel_id: int,
                         conversation: List[Dict[str, Any]]) -> None:
        """Set conversation history for a channel."""
        with self._channel_data_lock:
            self._ensure_channel_data(channel_id)
            self._channel_data[channel_id]['conversation'] = conversation.copy()
            logger.debug(
                f"Set conversation for channel {channel_id} with {len(conversation)} messages")

    def add_message_to_conversation(
            self, channel_id: int, message: Dict[str, Any]) -> None:
        """Add a message to the conversation history for a channel."""
        with self._channel_data_lock:
            self._ensure_channel_data(channel_id)
            self._channel_data[channel_id]['conversation'].append(message)
            logger.debug(
                f"Added message to conversation for channel {channel_id}")

    def clear_conversation(self, channel_id: int) -> None:
        """Clear conversation history for a channel."""
        with self._channel_data_lock:
            if channel_id in self._channel_data:
                # Check if this is the only data for this channel
                channel_data = self._channel_data[channel_id]
                if not any([channel_data['model'],
                            channel_data['draw_model'],
                            channel_data['edit_model'],
                            channel_data['system_prompt'],
                            channel_data['response_id']]):
                    # Remove the entire channel entry if no other data exists
                    del self._channel_data[channel_id]
                else:
                    # Just clear the conversation
                    self._channel_data[channel_id]['conversation'] = []
                    self._channel_data[channel_id]['response_id'] = None
                logger.debug(f"Cleared conversation for channel {channel_id}")

    def get_all_conversations(self) -> Dict[int, List[Dict[str, Any]]]:
        """Get all conversation histories (for debugging/admin purposes)."""
        with self._channel_data_lock:
            return {k: v['conversation'].copy() for k,
                    v in self._channel_data.items() if v['conversation']}

    # Response ID management for conversation continuity
    def get_response_id(self, channel_id: int) -> Optional[str]:
        """Get the last OpenAI response ID for a channel."""
        with self._channel_data_lock:
            return self._channel_data.get(channel_id, {}).get('response_id')

    def set_response_id(self, channel_id: int, response_id: str) -> None:
        """Set the OpenAI response ID for a channel."""
        with self._channel_data_lock:
            self._ensure_channel_data(channel_id)
            self._channel_data[channel_id]['response_id'] = response_id
            logger.debug(
                f"Set response ID for channel {channel_id}: {response_id}")

    def clear_response_id(self, channel_id: int) -> None:
        """Clear the OpenAI response ID for a channel."""
        with self._channel_data_lock:
            if channel_id in self._channel_data:
                self._channel_data[channel_id]['response_id'] = None
                logger.debug(f"Cleared response ID for channel {channel_id}")

    def get_all_response_ids(self) -> Dict[int, str]:
        """Get all response IDs (for debugging/admin purposes)."""
        with self._channel_data_lock:
            return {k: v['response_id']
                    for k, v in self._channel_data.items() if v['response_id']}

    # Model management
    def get_model(self, channel_id: int) -> Optional[str]:
        """Get the model setting for a channel."""
        with self._channel_data_lock:
            return self._channel_data.get(channel_id, {}).get('model')

    def set_model(self, channel_id: int, model: str) -> None:
        """Set the model for a channel."""
        with self._channel_data_lock:
            self._ensure_channel_data(channel_id)
            self._channel_data[channel_id]['model'] = model
            logger.debug(f"Set model for channel {channel_id} to {model}")
        self._save_settings()

    def get_all_models(self) -> Dict[int, str]:
        """Get all model settings."""
        with self._channel_data_lock:
            return {k: v['model']
                    for k, v in self._channel_data.items() if v['model']}

    # Draw model management
    def get_draw_model(self, channel_id: int) -> Optional[str]:
        """Get the draw model setting for a channel."""
        with self._channel_data_lock:
            return self._channel_data.get(channel_id, {}).get('draw_model')

    def set_draw_model(self, channel_id: int, model: str) -> None:
        """Set the draw model for a channel."""
        with self._channel_data_lock:
            self._ensure_channel_data(channel_id)
            self._channel_data[channel_id]['draw_model'] = model
            logger.debug(f"Set draw model for channel {channel_id} to {model}")
        self._save_settings()

    def get_all_draw_models(self) -> Dict[int, str]:
        """Get all draw model settings."""
        with self._channel_data_lock:
            return {k: v['draw_model']
                    for k, v in self._channel_data.items() if v['draw_model']}

    # Edit model management
    def get_edit_model(self, channel_id: int) -> Optional[str]:
        """Get the edit model setting for a channel."""
        with self._channel_data_lock:
            return self._channel_data.get(channel_id, {}).get('edit_model')

    def set_edit_model(self, channel_id: int, model: str) -> None:
        """Set the edit model for a channel."""
        with self._channel_data_lock:
            self._ensure_channel_data(channel_id)
            self._channel_data[channel_id]['edit_model'] = model
            logger.debug(f"Set edit model for channel {channel_id} to {model}")
        self._save_settings()

    def get_all_edit_models(self) -> Dict[int, str]:
        """Get all edit model settings."""
        with self._channel_data_lock:
            return {k: v['edit_model']
                    for k, v in self._channel_data.items() if v['edit_model']}

    # System prompt management
    def get_system_prompt(self, channel_id: int) -> Optional[str]:
        """Get the system prompt for a channel."""
        with self._channel_data_lock:
            return self._channel_data.get(channel_id, {}).get('system_prompt')

    def set_system_prompt(self, channel_id: int, prompt: str) -> None:
        """Set the system prompt for a channel."""
        with self._channel_data_lock:
            self._ensure_channel_data(channel_id)
            self._channel_data[channel_id]['system_prompt'] = prompt
            logger.debug(f"Set system prompt for channel {channel_id}")
        self._save_settings()

    def clear_system_prompt(self, channel_id: int) -> None:
        """Clear the system prompt for a channel."""
        with self._channel_data_lock:
            if channel_id in self._channel_data:
                self._channel_data[channel_id]['system_prompt'] = None
                logger.debug(f"Cleared system prompt for channel {channel_id}")
        self._save_settings()

    def get_all_system_prompts(self) -> Dict[int, str]:
        """Get all system prompts."""
        with self._channel_data_lock:
            return {k: v['system_prompt'] for k,
                    v in self._channel_data.items() if v['system_prompt']}


    # Death settings management (global)
    def get_death_settings(self) -> Optional[Dict[str, Any]]:
        """Get the global death settings."""
        with self._global_data_lock:
            return self._death_settings

    def set_death_settings(self, settings: Dict[str, Any]) -> None:
        """Set the global death settings."""
        with self._global_data_lock:
            self._death_settings = settings.copy()
            logger.debug("Set global death settings")
        self._save_settings()

    def clear_death_settings(self) -> None:
        """Clear the global death settings."""
        with self._global_data_lock:
            self._death_settings = None
            logger.debug("Cleared global death settings")
        self._save_settings()

    # Optimized batch operations
    def get_channel_context(self, channel_id: int) -> Dict[str, Any]:
        """
        Get all context for a channel in one operation.

        Returns:
            Dictionary with conversation, model, system_prompt, and response_id
        """
        with self._channel_data_lock:
            if channel_id not in self._channel_data:
                return {
                    'conversation': None,
                    'model': None,
                    'draw_model': None,
                    'edit_model': None,
                    'response_id': None
                }
            return self._channel_data[channel_id].copy()

    def update_channel_context(self, channel_id: int, **updates) -> None:
        """
        Update multiple channel context fields in one operation.

        Args:
            channel_id: Discord channel ID
            **updates: Fields to update (conversation, model, system_prompt, response_id)
        """
        with self._channel_data_lock:
            self._ensure_channel_data(channel_id)

            for field, value in updates.items():
                if field in [
                    'conversation',
                    'model',
                    'draw_model',
                    'edit_model',
                    'system_prompt',
                    'response_id',
                ]:
                    if field in ['conversation'] and isinstance(value, (list, dict)):
                        self._channel_data[channel_id][field] = value.copy() if value is not None else None
                    else:
                        self._channel_data[channel_id][field] = value
                    logger.debug(f"Updated {field} for channel {channel_id}")
        self._save_settings()

    def add_message_and_update_response_id(
            self, channel_id: int, message: Dict[str, Any], response_id: str) -> None:
        """
        Add a message to conversation and update response ID in one atomic operation.

        Args:
            channel_id: Discord channel ID
            message: Message to add to conversation
            response_id: New response ID to store
        """
        with self._channel_data_lock:
            self._ensure_channel_data(channel_id)
            self._channel_data[channel_id]['conversation'].append(message)
            self._channel_data[channel_id]['response_id'] = response_id
            logger.debug(
                f"Added message and updated response ID for channel {channel_id}: {response_id}")

    # Utility methods
    def clear_all_data(self) -> None:
        """Clear all stored data (for testing purposes)."""
        with self._channel_data_lock, self._global_data_lock:
            self._channel_data.clear()
            self._active_channels.clear()
            logger.info("Cleared all state data")

    def get_stats(self) -> Dict[str, int]:
        """Get statistics about stored data."""
        with self._channel_data_lock, self._global_data_lock:
            conversations = sum(
                1 for v in self._channel_data.values() if v['conversation'])
            models = sum(1 for v in self._channel_data.values() if v['model'])
            draw_models = sum(
                1 for v in self._channel_data.values() if v['draw_model'])
            edit_models = sum(
                1 for v in self._channel_data.values() if v['edit_model'])
            system_prompts = sum(
                1 for v in self._channel_data.values() if v['system_prompt'])
            response_ids = sum(
                1 for v in self._channel_data.values() if v['response_id'])

            return {
                "conversations": conversations,
                "models": models,
                "draw_models": draw_models,
                "edit_models": edit_models,
                "system_prompts": system_prompts,
                "response_ids": response_ids,
                "active_channels": len(self._active_channels),
            }

    # Active channel management
    def mark_channel_active(self, channel_id: int) -> None:
        """
        Mark a channel as active (bot has been used there).

        Args:
            channel_id: Discord channel ID
        """
        with self._global_data_lock:
            self._active_channels.add(channel_id)
            logger.debug(f"Marked channel {channel_id} as active")

    def get_active_channels(self) -> set[int]:
        """
        Get all channels where the bot has been used.

        Returns:
            Set of channel IDs
        """
        with self._global_data_lock:
            return self._active_channels.copy()

    def clear_active_channels(self) -> None:
        """Clear the active channels list."""
        with self._global_data_lock:
            self._active_channels.clear()
            logger.info("Cleared active channels list")

    # Git SHA management
    def get_last_git_sha(self) -> Optional[str]:
        """
        Get the last known git SHA from the previous session.

        Returns:
            Last known git SHA or None if not available
        """
        with self._global_data_lock:
            return self._last_git_sha

    def set_last_git_sha(self, sha: str) -> None:
        """
        Set the last known git SHA.

        Args:
            sha: Git commit SHA
        """
        with self._global_data_lock:
            self._last_git_sha = sha
            logger.debug(f"Updated last git SHA to: {sha}")

    def save_state_to_temp_file(
            self,
            restart_info: Optional[dict] = None) -> Optional[str]:
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

            # Gather all state data under consolidated locks
            with self._channel_data_lock, self._global_data_lock:
                # Extract data from consolidated structure
                conversations = {
                    k: v['conversation'] for k,
                    v in self._channel_data.items() if v['conversation']}
                response_ids = {
                    k: v['response_id'] for k,
                    v in self._channel_data.items() if v['response_id']}

                state_data = {
                    "conversations": conversations,
                    "response_ids": response_ids,
                    "active_channels": list(self._active_channels),
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

        except (OSError, PermissionError, TypeError, ValueError) as e:
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
                    if "conversations" not in state_data:
                        logger.warning(
                            f"Invalid state file format: {temp_file}")
                        continue

                    # Load the state under consolidated locks
                    with self._channel_data_lock, self._global_data_lock:
                        # Rebuild consolidated channel data structure for transient state
                        conversations = state_data.get("conversations", {})
                        response_ids = state_data.get("response_ids", {})

                        # Get all channel IDs from transient data sources
                        all_channel_ids = set()
                        all_channel_ids.update(int(k)
                                               for k in conversations.keys())
                        all_channel_ids.update(int(k)
                                               for k in response_ids.keys())

                        for channel_id in all_channel_ids:
                            self._ensure_channel_data(channel_id)
                            if str(channel_id) in conversations:
                                self._channel_data[channel_id]['conversation'] = conversations[str(channel_id)]
                            if str(channel_id) in response_ids:
                                self._channel_data[channel_id]['response_id'] = response_ids[str(channel_id)]

                        # Load active channels
                        if "active_channels" in state_data:
                            self._active_channels = set(
                                state_data["active_channels"])
                        else:
                            # Derive from existing data
                            self._active_channels = all_channel_ids

                        # Load last git SHA (may not exist in older state
                        # files)
                        self._last_git_sha = state_data.get("last_git_sha")

                    logger.debug(
                        f"Successfully loaded state from: {temp_file}")
                    sha_info = ""
                    if self._last_git_sha:
                        sha_info = f", last git SHA: {self._last_git_sha}"
                    # Count restored data
                    conversations_count = sum(
                        1 for v in self._channel_data.values() if v['conversation'])
                    response_ids_count = sum(
                        1 for v in self._channel_data.values() if v['response_id'])

                    logger.info(
                        f"Restored {conversations_count} conversations, {len(self._active_channels)} active channels, "
                        f"{response_ids_count} response IDs{sha_info}"
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
                    logger.error(
                        f"Failed to clean up temporary file {temp_file}: {e}")

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
                    logger.info(
                        f"Cleaned up leftover temporary file: {temp_file}")
                except OSError as e:
                    logger.error(
                        f"Failed to clean up temporary file {temp_file}: {e}")

        except (OSError, ValueError) as e:
            logger.error(f"Error during temp file cleanup: {e}")

    def _save_settings(self) -> None:
        """Save persistent settings to disk."""
        try:
            os.makedirs(os.path.dirname(self._settings_file) or ".", exist_ok=True)
            with self._channel_data_lock, self._global_data_lock:
                settings_data = {
                    "models": {str(k): v['model'] for k, v in self._channel_data.items() if v.get('model')},
                    "draw_models": {str(k): v['draw_model'] for k, v in self._channel_data.items() if v.get('draw_model')},
                    "edit_models": {str(k): v['edit_model'] for k, v in self._channel_data.items() if v.get('edit_model')},
                    "system_prompts": {str(k): v['system_prompt'] for k, v in self._channel_data.items() if v.get('system_prompt')},

                    "death_settings": self._death_settings,
                }
            
            # Write atomically
            temp_file = f"{self._settings_file}.tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(settings_data, f, indent=2)
            os.replace(temp_file, self._settings_file)
            
        except (OSError, ValueError) as e:
            logger.error(f"Failed to save settings: {e}")

    def _load_settings(self) -> None:
        """Load persistent settings from disk."""
        if not hasattr(self, '_settings_file') or not os.path.exists(self._settings_file):
            return
            
        try:
            with open(self._settings_file, "r", encoding="utf-8") as f:
                settings_data = json.load(f)
                
            with self._channel_data_lock, self._global_data_lock:
                # Load models
                for k, v in settings_data.get("models", {}).items():
                    self._ensure_channel_data(int(k))
                    self._channel_data[int(k)]['model'] = v
                    
                # Load draw_models
                for k, v in settings_data.get("draw_models", {}).items():
                    self._ensure_channel_data(int(k))
                    self._channel_data[int(k)]['draw_model'] = v
                    
                # Load edit_models
                for k, v in settings_data.get("edit_models", {}).items():
                    self._ensure_channel_data(int(k))
                    self._channel_data[int(k)]['edit_model'] = v
                    
                # Load system_prompts
                for k, v in settings_data.get("system_prompts", {}).items():
                    self._ensure_channel_data(int(k))
                    self._channel_data[int(k)]['system_prompt'] = v
                    

                # Load death_settings
                self._death_settings = settings_data.get("death_settings")
                
            logger.info("Loaded persistent settings from disk")
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to load settings: {e}")


# Global state service instance
state_service = StateService()
