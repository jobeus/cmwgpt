"""
Auto-update service for the Discord bot.

Provides automatic git-based updates with background monitoring,
automatic restarts, and state persistence.
"""

import asyncio
import logging
import threading
from typing import Optional, Callable, Awaitable

from src.config import KEEP_UP_TO_DATE_WITH_GIT
from src.services.queue_service import queue_service
from src.utils.git_utils import is_git_repository, get_current_commit_hash, fetch_updates, check_for_new_commits

logger = logging.getLogger(__name__)


class AutoUpdateService:
    """Service for automatic git-based updates and restarts."""

    def __init__(self, check_interval: int = 60):
        """Initialize the auto-update service."""
        self.check_interval = check_interval
        self._monitoring_thread: Optional[threading.Thread] = None
        self._stop_monitoring = threading.Event()
        self._is_running = False
        self._restart_callback: Optional[Callable[[], Awaitable[None]]] = None
        self._consecutive_failures = 0
        self._max_consecutive_failures = 5
        self._last_known_commit: Optional[str] = None

        logger.info(
            f"AutoUpdateService initialized with {check_interval}s check interval")

    def set_restart_callback(
        self,
        callback: Callable[[],
                           Awaitable[None]]) -> None:
        """Set the callback function to call when a restart is needed."""
        self._restart_callback = callback

    def start(self) -> None:
        """Start the auto-update monitoring if enabled."""
        if not KEEP_UP_TO_DATE_WITH_GIT:
            logger.info("Auto-update disabled by configuration")
            return

        if not is_git_repository():
            logger.warning("Not in a git repository, auto-update disabled")
            return

        if self._is_running:
            logger.warning("AutoUpdateService is already running")
            return

        self._is_running = True
        self._stop_monitoring.clear()

        # Get initial commit hash
        self._last_known_commit = get_current_commit_hash()
        if self._last_known_commit:
            logger.info(
                f"Starting auto-update monitoring from commit: {self._last_known_commit[:7]}")
        else:
            logger.warning("Could not determine current commit hash")

        # Start monitoring thread
        self._monitoring_thread = threading.Thread(
            target=self._monitor_git_updates, daemon=True)
        self._monitoring_thread.start()

        logger.info("AutoUpdateService started")

    def stop(self) -> None:
        """Stop the auto-update monitoring."""
        if not self._is_running:
            return

        logger.info("Stopping AutoUpdateService...")
        self._stop_monitoring.set()

        if self._monitoring_thread and self._monitoring_thread.is_alive():
            self._monitoring_thread.join(timeout=10.0)
            if self._monitoring_thread.is_alive():
                logger.warning("Monitoring thread did not stop within timeout")

        self._is_running = False
        logger.info("AutoUpdateService stopped")

    async def trigger_restart(self, manual: bool = False) -> bool:
        """Trigger a restart of the bot."""
        if not self._restart_callback:
            logger.error("No restart callback set")
            return False

        restart_type = "manual" if manual else "automatic"
        logger.info(f"Triggering {restart_type} restart")

        # Create a wrapper that passes the manual flag
        async def restart_wrapper():
            await self._restart_callback(manual=manual)

        # Queue the restart through the queue service
        return await queue_service.queue_restart(restart_wrapper)

    def _monitor_git_updates(self) -> None:
        """Main monitoring loop running in background thread."""
        logger.info("Git update monitoring started")

        while not self._stop_monitoring.is_set():
            try:
                # Fetch updates from origin
                if fetch_updates():
                    # Check for new commits
                    if check_for_new_commits():
                        logger.info("New commits detected, triggering restart")
                        # Schedule restart in the main event loop
                        try:
                            loop = asyncio.get_running_loop()
                            asyncio.run_coroutine_threadsafe(
                                self.trigger_restart(manual=False), loop)
                        except RuntimeError:
                            # No running loop, create a new one
                            asyncio.run(self.trigger_restart(manual=False))
                        # Exit monitoring loop after triggering restart
                        break

                    # Reset failure counter on success
                    self._consecutive_failures = 0
                else:
                    self._consecutive_failures += 1
                    logger.warning(
                        f"Git fetch failed ({self._consecutive_failures}/{self._max_consecutive_failures})")

                    if self._consecutive_failures >= self._max_consecutive_failures:
                        logger.error(
                            "Too many consecutive git failures, disabling auto-update")
                        break

            except (OSError, RuntimeError, asyncio.TimeoutError) as e:
                self._consecutive_failures += 1
                logger.error(f"Error in git monitoring loop: {e}")

                if self._consecutive_failures >= self._max_consecutive_failures:
                    logger.error(
                        "Too many consecutive errors, disabling auto-update")
                    break

            # Wait for next check or stop signal
            if self._stop_monitoring.wait(timeout=self.check_interval):
                break

        logger.info("Git update monitoring stopped")

    def get_status(self) -> dict:
        """Get the current status of the auto-update service."""
        return {
            "enabled": KEEP_UP_TO_DATE_WITH_GIT,
            "running": self._is_running,
            "check_interval": self.check_interval,
            "consecutive_failures": self._consecutive_failures,
            "max_failures": self._max_consecutive_failures,
            "last_known_commit": self._last_known_commit,
            "is_git_repo": is_git_repository(),
        }


# Global auto-update service instance
auto_update_service = AutoUpdateService()
