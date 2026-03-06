"""
Restart handler for the Discord bot.

This module provides the restart functionality that:
- Saves current state before restarting
- Performs git pull to update code
- Gracefully shuts down the bot
- Exits with a specific code to signal restart
"""

import asyncio
import logging
import sys
from typing import Callable

from src.utils.git_utils import perform_git_pull as default_perform_git_pull

logger = logging.getLogger(__name__)


class RestartHandler:
    """Handles bot restart operations with state persistence."""

    def __init__(
        self,
        *,
        state_service,
        git_pull: Callable[[], bool] = default_perform_git_pull,
    ):
        """Initialize the restart handler."""
        self._state_service = state_service
        self._git_pull = git_pull
        self._restart_in_progress = False
        self._skip_cleanup = False  # Flag to prevent cleanup during restart

    async def perform_restart(self, manual: bool = False) -> None:
        """
        Perform a complete restart of the bot.

        Args:
            manual: Whether this is a manual restart

        This includes:
        1. Saving current state
        2. Performing git pull
        3. Gracefully shutting down
        4. Exiting with restart code
        """
        if self._restart_in_progress:
            logger.warning(
                "Restart already in progress, ignoring duplicate request")
            return

        self._restart_in_progress = True
        self._skip_cleanup = True  # Prevent cleanup during restart

        print("🔄 Restarting bot...")

        try:
            # Step 1: Save current state with restart info (don't update git SHA yet)
            # The announcement service will handle git SHA comparison and
            # update
            print("💾 Saving bot state...")
            restart_info = {"manual_restart": manual}
            temp_file = self._state_service.save_state_to_temp_file(restart_info)
            if temp_file:
                print("✅ State saved")
            else:
                print("⚠️  Failed to save state, continuing anyway")

            # Step 2: Perform git pull
            print("📥 Updating code...")
            if self._git_pull():
                print("✅ Code updated")
            else:
                print("⚠️  Git pull failed, continuing anyway")

            # Step 3: Give a moment for any final operations
            await asyncio.sleep(0.5)

            # Step 4: Exit with restart code
            print("🚀 Restarting...")
            # Use exit code 42 to signal that this is an intentional restart
            # The process manager (systemd, pm2, etc.) should restart the bot
            sys.exit(42)

        except (OSError, RuntimeError, asyncio.TimeoutError) as e:
            logger.error(f"Error during restart process: {e}")
            self._restart_in_progress = False
            raise

    def is_restart_in_progress(self) -> bool:
        """Check if a restart is currently in progress."""
        return self._restart_in_progress

    def should_skip_cleanup(self) -> bool:
        """Check if cleanup should be skipped (during restart)."""
        return self._skip_cleanup

    async def perform_graceful_shutdown(self) -> None:
        """
        Perform a graceful shutdown without restarting.

        This saves the current state to disk just like a restart,
        but doesn't perform git pull or exit with restart code.
        Used when the bot is killed with Ctrl-C or kill signal.
        """
        if self._restart_in_progress:
            logger.warning(
                "Restart already in progress, graceful shutdown will proceed anyway")

        # Set skip cleanup flag to prevent temp file cleanup and duplicate
        # service shutdown
        self._skip_cleanup = True

        print("💾 Performing graceful shutdown...")

        try:
            # Step 1: Save current state with shutdown info (don't update git SHA)
            # The announcement service will handle git SHA comparison on next
            # startup
            print("💾 Saving bot state...")
            shutdown_info = {
                "manual_restart": False,
                "graceful_shutdown": True}
            temp_file = self._state_service.save_state_to_temp_file(shutdown_info)
            if temp_file:
                print("✅ State saved")
            else:
                print("⚠️  Failed to save state")

            # Step 2: Give a moment for any final operations
            await asyncio.sleep(0.5)

            print("✅ Graceful shutdown preparation complete")

        except (OSError, RuntimeError, asyncio.TimeoutError) as e:
            logger.error(f"Error during graceful shutdown: {e}")
            print(f"⚠️  Error during graceful shutdown: {e}")
            raise
