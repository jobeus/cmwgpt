"""
Thread-safe state for the Discord bot, kept in Redis (``StateStore``).

- Per-channel settings (chat model, image model, system prompt) are the hash
  ``cmwgpt:settings:channels`` (one JSON object per channel), and the death settings
  are ``cmwgpt:settings:death``.
- Restart state (active channels, last git SHA, why we restarted) is
  ``cmwgpt:restart_state``, written just before exit and taken on the next start.

The settings JSON file and ``/tmp`` restart files from before Redis are imported once.
"""

import glob
import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from src.db.state_store import StateStore

logger = logging.getLogger(__name__)

CHANNEL_SETTINGS_KEY = "settings:channels"
DEATH_SETTINGS_KEY = "settings:death"
RESTART_STATE_KEY = "restart_state"
# A restart takes seconds; state older than this is from some other run and is ignored.
RESTART_STATE_TTL_SECONDS = 24 * 3600

LEGACY_SETTINGS_FILE = ".cache/cmwgpt_settings.json"
LEGACY_RESTART_FILES = "/tmp/cmwgpt_state_backup_*.json"

# Per-channel settings and the key each was saved under in the legacy settings file.
_CHANNEL_SETTINGS = {
    "model": "models",
    "image_model": "image_models",
    "system_prompt": "system_prompts",
}


class StateService:
    """Thread-safe state management for the Discord bot."""

    def __init__(self, store: Optional[StateStore] = None, *,
                 legacy_settings_file: str = LEGACY_SETTINGS_FILE,
                 legacy_restart_files: str = LEGACY_RESTART_FILES):
        self._store = store or StateStore(None)
        self._legacy_restart_files = legacy_restart_files
        self._channel_settings: Dict[int, Dict[str, Optional[str]]] = {}
        self._active_channels: set[int] = set()
        self._last_git_sha: Optional[str] = None
        self._death_settings: Optional[Dict[str, Any]] = None
        # What the previous process said about why it exited (see save_restart_state).
        self._restart_info: Dict[str, Any] = {}
        self._lock = threading.RLock()

        self._import_legacy_settings(legacy_settings_file)
        self._load_settings()

    # -- per-channel settings -------------------------------------------------

    def _get_channel_setting(self, channel_id: int, key: str) -> Optional[str]:
        with self._lock:
            return self._channel_settings.get(channel_id, {}).get(key)

    def _set_channel_setting(self, channel_id: int, key: str, value: Optional[str]) -> None:
        with self._lock:
            settings = self._channel_settings.setdefault(channel_id, {})
            settings[key] = value
            saved = {k: v for k, v in settings.items() if v}
        if saved:
            self._store.set_field(CHANNEL_SETTINGS_KEY, channel_id, saved)
        else:
            self._store.delete_field(CHANNEL_SETTINGS_KEY, channel_id)

    def get_model(self, channel_id: int) -> Optional[str]:
        return self._get_channel_setting(channel_id, "model")

    def set_model(self, channel_id: int, model: str) -> None:
        self._set_channel_setting(channel_id, "model", model)

    def get_image_model(self, channel_id: int) -> Optional[str]:
        return self._get_channel_setting(channel_id, "image_model")

    def set_image_model(self, channel_id: int, model: str) -> None:
        self._set_channel_setting(channel_id, "image_model", model)

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
        self._store.set(DEATH_SETTINGS_KEY, self._death_settings)

    def clear_death_settings(self) -> None:
        with self._lock:
            self._death_settings = None
        self._store.set(DEATH_SETTINGS_KEY, None)

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
        """Restart info saved by the previous process (empty if none)."""
        return dict(self._restart_info)

    # -- restart state --------------------------------------------------------

    def save_restart_state(self, restart_info: Optional[dict] = None) -> bool:
        """Save the state the next process picks up after a restart. True if it reached Redis."""
        with self._lock:
            state_data: Dict[str, Any] = {
                "active_channels": sorted(self._active_channels),
                "last_git_sha": self._last_git_sha,
                "timestamp": int(time.time()),
                "pid": os.getpid(),
            }
        if restart_info:
            state_data["restart_info"] = restart_info
        return self._store.set(RESTART_STATE_KEY, state_data, ttl_seconds=RESTART_STATE_TTL_SECONDS)

    def load_restart_state(self) -> bool:
        """Take the state the previous process saved before it exited. True if there was some."""
        state_data = self._store.pop(RESTART_STATE_KEY)
        legacy = self._take_legacy_restart_state()
        state_data = state_data or legacy
        if not isinstance(state_data, dict):
            return False
        try:
            with self._lock:
                self._active_channels = {int(c) for c in state_data.get("active_channels", [])}
                self._last_git_sha = state_data.get("last_git_sha")
                self._restart_info = state_data.get("restart_info") or {}
        except (TypeError, ValueError) as e:
            logger.error("Ignoring unreadable restart state %s: %s", state_data, e)
            return False
        logger.info(
            "Restored %s active channels (last git SHA: %s, restart info: %s)",
            len(self._active_channels), self._last_git_sha, self._restart_info)
        return True

    def _take_legacy_restart_state(self) -> Optional[Dict[str, Any]]:
        """The newest restart file a pre-Redis process left in /tmp; deletes them all."""
        files = sorted(glob.glob(self._legacy_restart_files), reverse=True)
        state_data = None
        for path in files:
            if state_data is None:
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        state_data = json.load(f)
                except (OSError, ValueError) as e:
                    logger.error("Couldn't read the old restart file %s: %s", path, e)
            try:
                os.remove(path)
            except OSError as e:
                logger.error("Couldn't delete the old restart file %s: %s", path, e)
        return state_data

    # -- settings -------------------------------------------------------------

    def _import_legacy_settings(self, path: str) -> None:
        data = self._store.import_legacy_file(path, CHANNEL_SETTINGS_KEY)
        if not isinstance(data, dict):
            return
        channels: Dict[str, Dict[str, str]] = {}
        for key, file_key in _CHANNEL_SETTINGS.items():
            for channel_id, value in (data.get(file_key) or {}).items():
                if value:
                    channels.setdefault(str(channel_id), {})[key] = value
        for channel_id, settings in channels.items():
            self._store.set_field(CHANNEL_SETTINGS_KEY, channel_id, settings)
        if data.get("death_settings") is not None:
            self._store.set(DEATH_SETTINGS_KEY, data["death_settings"])

    def _load_settings(self) -> None:
        channels = self._store.get_all(CHANNEL_SETTINGS_KEY)
        death_settings = self._store.get(DEATH_SETTINGS_KEY)
        with self._lock:
            for channel_id, settings in channels.items():
                try:
                    self._channel_settings[int(channel_id)] = {
                        key: settings.get(key) for key in _CHANNEL_SETTINGS if settings.get(key)}
                except (AttributeError, ValueError):
                    logger.warning("Ignoring unreadable settings for channel %s: %s", channel_id, settings)
            self._death_settings = death_settings if isinstance(death_settings, dict) else None
        if channels or death_settings:
            logger.info("Loaded settings for %s channels from Redis", len(channels))
