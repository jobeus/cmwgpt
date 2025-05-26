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
import subprocess
import sys

from src.services.state_service import state_service

logger = logging.getLogger(__name__)


class RestartHandler:
    """Handles bot restart operations with state persistence."""

    def __init__(self):
        """Initialize the restart handler."""
        self._restart_in_progress = False

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
            logger.warning("Restart already in progress, ignoring duplicate request")
            return

        self._restart_in_progress = True
        logger.info("Starting bot restart process")

        try:
            # Step 1: Save current state
            logger.info("Saving bot state before restart")
            temp_file = state_service.save_state_to_temp_file()
            if temp_file:
                logger.info(f"State saved to: {temp_file}")

                # Also save restart type information
                restart_info_file = temp_file.replace('.json', '_restart_info.json')
                try:
                    import json
                    with open(restart_info_file, 'w') as f:
                        json.dump({"manual_restart": manual}, f)
                    import os
                    import stat
                    os.chmod(restart_info_file, stat.S_IRUSR | stat.S_IWUSR)
                    logger.info(f"Restart info saved to: {restart_info_file}")
                except Exception as e:
                    logger.warning(f"Failed to save restart info: {e}")
            else:
                logger.warning("Failed to save state, continuing with restart")

            # Step 2: Perform git pull
            logger.info("Performing git pull to update code")
            if self._perform_git_pull():
                logger.info("Git pull completed successfully")
            else:
                logger.warning("Git pull failed, continuing with restart anyway")

            # Step 3: Give a moment for any final operations
            await asyncio.sleep(1)

            # Step 4: Exit with restart code
            logger.info("Exiting for restart")
            # Use exit code 42 to signal that this is an intentional restart
            # The process manager (systemd, pm2, etc.) should restart the bot
            sys.exit(42)

        except Exception as e:
            logger.error(f"Error during restart process: {e}")
            self._restart_in_progress = False
            raise

    def _perform_git_pull(self) -> bool:
        """
        Perform git pull to update the code.

        Returns:
            True if git pull succeeded, False otherwise
        """
        try:
            # First, check if we're in a git repository
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                logger.warning("Not in a git repository, skipping git pull")
                return False

            # Check if there are any uncommitted changes
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0 and result.stdout.strip():
                logger.warning("Uncommitted changes detected, git pull may fail")

            # Perform the git pull
            result = subprocess.run(
                ["git", "pull", "origin"],
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode == 0:
                logger.info("Git pull completed successfully")
                if result.stdout.strip():
                    logger.info(f"Git pull output: {result.stdout.strip()}")
                return True
            else:
                logger.error(f"Git pull failed with return code {result.returncode}")
                if result.stderr.strip():
                    logger.error(f"Git pull error: {result.stderr.strip()}")
                return False

        except subprocess.TimeoutExpired:
            logger.error("Git pull timed out")
            return False
        except Exception as e:
            logger.error(f"Error during git pull: {e}")
            return False

    def is_restart_in_progress(self) -> bool:
        """Check if a restart is currently in progress."""
        return self._restart_in_progress


# Global restart handler instance
restart_handler = RestartHandler()
