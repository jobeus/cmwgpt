"""
Restart handler for the Discord bot.

A restart saves state, pulls the latest code, then runs the normal shutdown
sequence (so replies in progress finish and clients close). Once the bot's
event loop has exited, ``main`` exits the process with :data:`RESTART_EXIT_CODE`
and the process manager (systemd, pm2, start.sh) starts it again.
"""

import asyncio
import logging
from typing import Any, Callable, Coroutine, Optional

from src.utils.git_utils import perform_git_pull as default_perform_git_pull

logger = logging.getLogger(__name__)

RESTART_EXIT_CODE = 42


class RestartHandler:
    """Handles bot restart operations with state persistence."""

    def __init__(
        self,
        *,
        state_service,
        git_pull: Callable[[], bool] = default_perform_git_pull,
    ):
        self._state_service = state_service
        self._git_pull = git_pull
        self._restart_in_progress = False
        self._shutdown: Optional[Callable[[], Coroutine[Any, Any, None]]] = None
        self._shutdown_task: Optional[asyncio.Task] = None
        # Set when the process should exit with a specific code after the loop ends.
        self.exit_code: Optional[int] = None

    def set_shutdown_callback(self, shutdown: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """The coroutine that shuts the bot down (services, then the Discord connection)."""
        self._shutdown = shutdown

    async def perform_restart(self, manual: bool = False) -> None:
        """Save state, pull code, and start the shutdown that ends in a restart."""
        if self._restart_in_progress:
            logger.warning("Restart already in progress, ignoring duplicate request")
            return
        self._restart_in_progress = True
        print("🔄 Restarting bot...")

        if self._state_service.save_state_to_temp_file({"manual_restart": manual}):
            print("✅ State saved")
        else:
            print("⚠️  Failed to save state, continuing anyway")

        # git pull is synchronous; keep it off the loop so heartbeats continue.
        print("📥 Updating code...")
        try:
            pulled = await asyncio.to_thread(self._git_pull)
        except Exception as e:
            logger.error(f"git pull raised: {e}")
            pulled = False
        print("✅ Code updated" if pulled else "⚠️  Git pull failed, continuing anyway")

        self.exit_code = RESTART_EXIT_CODE
        if self._shutdown is None:
            logger.error("No shutdown callback configured; cannot restart cleanly")
            return
        # This usually runs inside a queue worker, which shutdown waits on, so
        # run the shutdown as its own task instead of awaiting it here.
        print("🚀 Restarting...")
        self._shutdown_task = asyncio.get_running_loop().create_task(self._shutdown())

    def is_restart_in_progress(self) -> bool:
        return self._restart_in_progress

    async def perform_graceful_shutdown(self) -> None:
        """Save state before a non-restart shutdown (Ctrl-C, SIGTERM)."""
        if self._state_service.save_state_to_temp_file({"manual_restart": False, "graceful_shutdown": True}):
            print("✅ State saved")
        else:
            print("⚠️  Failed to save state")
