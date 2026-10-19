"""
Thread-safe state for the Discord bot.

- Per-channel settings (chat/draw/edit model, system prompt) and the global
  death settings persist in a JSON settings file.
- Restart state (active channels, last git SHA, why we restarted) survives a
  restart through a temp file written just before exit.
"""

import glob
import json
import logging
import os
import stat
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

STATE_FILE_PATTERN = "/tmp/cmwgpt_state_backup_*.json"

# Per-channel settings and the key each is saved under in the settings file.
_CHANNEL_SETTINGS = {
    "model": "models",
    "draw_model": "draw_models",
    "edit_model": "edit_models",
    "system_prompt": "system_prompts",
}


class StateService:
    """Thread-safe state management for the Discord bot."""

    def __init__(self):
        self._channel_settings: Dict[int, Dict[str, Optional[str]]] = {}
        self._active_channels: set[int] = set()
        self._last_git_sha: Optional[str] = None
        self._death_settings: Optional[Dict[str, Any]] = None
        # What the previous process said about why it exited (see save_state_to_temp_file).
        self._restart_info: Dict[str, Any] = {}
        self._lock = threading.RLock()

        self._settings_file = os.environ.get("CMWGPT_SETTINGS_FILE", ".cache/cmwgpt_settings.json")
        self._load_settings()

    # -- per-channel settings -------------------------------------------------

    def _get_channel_setting(self, channel_id: int, key: str) -> Optional[str]:
        with self._lock:
            return self._channel_settings.get(channel_id, {}).get(key)

    def _set_channel_setting(self, channel_id: int, key: str, value: Optional[str]) -> None:
        with self._lock:
            self._channel_settings.setdefault(channel_id, {})[key] = value
        self._save_settings()

    def get_model(self, channel_id: int) -> Optional[str]:
        return self._get_channel_setting(channel_id, "model")

    def set_model(self, channel_id: int, model: str) -> None:
        self._set_channel_setting(channel_id, "model", model)

    def get_draw_model(self, channel_id: int) -> Optional[str]:
        return self._get_channel_setting(channel_id, "draw_model")

    def set_draw_model(self, channel_id: int, model: str) -> None:
        self._set_channel_setting(channel_id, "draw_model", model)

    def get_edit_model(self, channel_id: int) -> Optional[str]:
        return self._get_channel_setting(channel_id, "edit_model")

    def set_edit_model(self, channel_id: int, model: str) -> None:
        self._set_channel_setting(channel_id, "edit_model", model)

    def get_system_prompt(self, channel_id: int) -> Optional[str]:
        return self._get_channel_setting(channel_id, "system_prompt")

    def set_system_prompt(self, channel_id: int, prompt: str) -> None:
        self._set_channel_setting(channel_id, "system_prompt", prompt)

    def clear_system_prompt(self, channel_id: int) -> None:
        self._set_channel_setting(channel_id, "system_prompt", None)

    # -- global settings ----------------------------------------------------

    def get_death_settings(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._death_settings

    def set_death_settings(self, settings: Dict[str, Any]) -> None:
        with self._lock:
            self._death_settings = settings.copy()
        self._save_settings()

    def clear_death_settings(self) -> None:
        with self._lock:
            self._death_settings = None
        self._save_settings()

    def mark_channel_active(self, channel_id: int) -> None:
        """Remember a channel the bot has been used in (for update announcements)."""
        with self._lock:
            self._active_channels.add(channel_id)

    def get_active_channels(self) -> set[int]:
        with self._lock:
            return self._active_channels.copy()

    def get_last_git_sha(self) -> Optional[str]:
        with self._lock:
            return self._last_git_sha

    def set_last_git_sha(self, sha: str) -> None:
        with self._lock:
            self._last_git_sha = sha

    @property
    def restart_info(self) -> Dict[str, Any]:
        """Restart info loaded from the previous process's state file (empty if none)."""
        return dict(self._restart_info)

    # -- restart state --------------------------------------------------------

    def save_state_to_temp_file(self, restart_info: Optional[dict] = None) -> Optional[str]:
        """Save restart state to an owner-only temp file. Returns its path, or None on failure."""
        try:
            timestamp = int(time.time())
            pid = os.getpid()
            temp_filename = f"/tmp/cmwgpt_state_backup_{timestamp}_{pid}.json"
            with self._lock:
                state_data: Dict[str, Any] = {
                    "active_channels": list(self._active_channels),
                    "last_git_sha": self._last_git_sha,
                    "timestamp": timestamp,
                    "pid": pid,
                }
            if restart_info:
                state_data["restart_info"] = restart_info

            fd = os.open(temp_filename, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state_data, f, indent=2)
            logger.debug(f"State saved to temporary file: {temp_filename}")
            return temp_filename
        except (OSError, TypeError, ValueError) as e:
            logger.error(f"Failed to save state to temporary file: {e}")
            return None

    def load_state_from_temp_files(self) -> bool:
        """Load the newest restart state file, then delete all of them. True if one was found."""
        state_files = sorted(glob.glob(STATE_FILE_PATTERN), reverse=True)
        if not state_files:
            logger.info("No temporary state files found")
            return False

        for state_file in state_files:
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    state_data = json.load(f)
                with self._lock:
                    self._active_channels = {int(c) for c in state_data.get("active_channels", [])}
                    self._last_git_sha = state_data.get("last_git_sha")
                    self._restart_info = state_data.get("restart_info") or {}
                logger.info(
                    f"Restored {len(self._active_channels)} active channels from {state_file} "
                    f"(last git SHA: {self._last_git_sha}, restart info: {self._restart_info})"
                )
                break
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as e:
                logger.error(f"Failed to load state from {state_file}: {e}")

        for state_file in state_files:
            try:
                os.remove(state_file)
            except OSError as e:
                logger.error(f"Failed to clean up temporary file {state_file}: {e}")
        return True

    # -- settings file --------------------------------------------------------

    def _save_settings(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._settings_file) or ".", exist_ok=True)
            with self._lock:
                settings_data: Dict[str, Any] = {
                    file_key: {
                        str(channel_id): values[key]
                        for channel_id, values in self._channel_settings.items() if values.get(key)
                    }
                    for key, file_key in _CHANNEL_SETTINGS.items()
                }
                settings_data["death_settings"] = self._death_settings

            temp_file = f"{self._settings_file}.tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(settings_data, f, indent=2)
            os.replace(temp_file, self._settings_file)
        except (OSError, ValueError) as e:
            logger.error(f"Failed to save settings: {e}")

    def _load_settings(self) -> None:
        if not os.path.exists(self._settings_file):
            return
        try:
            with open(self._settings_file, "r", encoding="utf-8") as f:
                settings_data = json.load(f)
            with self._lock:
                for key, file_key in _CHANNEL_SETTINGS.items():
                    for channel_id, value in (settings_data.get(file_key) or {}).items():
                        self._channel_settings.setdefault(int(channel_id), {})[key] = value
                self._death_settings = settings_data.get("death_settings")
            logger.info("Loaded persistent settings from disk")
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to load settings: {e}")
