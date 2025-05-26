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
from typing import Optional

from src.services.state_service import state_service

logger = logging.getLogger(__name__)


class RestartHandler:
    """Handles bot restart operations with state persistence."""

    def __init__(self):
        """Initialize the restart handler."""
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
            logger.warning("Restart already in progress, ignoring duplicate request")
            return

        self._restart_in_progress = True
        self._skip_cleanup = True  # Prevent cleanup during restart

        print("🔄 Restarting bot...")

        try:
            # Step 1: Save current git SHA to state
            print("📝 Recording current git SHA...")
            current_sha = self._get_current_git_sha()
            if current_sha:
                state_service.set_last_git_sha(current_sha)
                print(f"✅ Recorded git SHA: {current_sha}")
            else:
                print("⚠️  Could not determine git SHA")

            # Step 2: Save current state
            print("💾 Saving bot state...")
            temp_file = state_service.save_state_to_temp_file()
            if temp_file:
                print("✅ State saved")

                # Also save restart type information
                restart_info_file = temp_file.replace(".json", "_restart_info.json")
                try:
                    import json

                    with open(restart_info_file, "w") as f:
                        json.dump({"manual_restart": manual}, f)
                    import os
                    import stat

                    os.chmod(restart_info_file, stat.S_IRUSR | stat.S_IWUSR)
                except Exception as e:
                    logger.warning(f"Failed to save restart info: {e}")
            else:
                print("⚠️  Failed to save state, continuing anyway")

            # Step 3: Perform git pull
            print("📥 Updating code...")
            if self._perform_git_pull():
                print("✅ Code updated")
            else:
                print("⚠️  Git pull failed, continuing anyway")

            # Step 4: Give a moment for any final operations
            await asyncio.sleep(0.5)

            # Step 5: Exit with restart code
            print("🚀 Restarting...")
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
            result = subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, text=True, timeout=10)

            if result.returncode != 0:
                logger.warning("Not in a git repository, skipping git pull")
                return False

            # Check if there are any uncommitted changes
            result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=10)

            if result.returncode == 0 and result.stdout.strip():
                logger.warning("Uncommitted changes detected, git pull may fail")

            # Perform the git pull
            result = subprocess.run(["git", "pull", "origin"], capture_output=True, text=True, timeout=60)

            if result.returncode == 0:
                output = result.stdout.strip()
                if "Already up to date" in output:
                    logger.debug("Git pull: Already up to date")
                elif output:
                    logger.info(f"Git pull: {output}")
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

    def should_skip_cleanup(self) -> bool:
        """Check if cleanup should be skipped (during restart)."""
        return self._skip_cleanup

    def _get_current_git_sha(self) -> Optional[str]:
        """
        Get the current git commit SHA.

        Returns:
            Git commit SHA or None if unable to determine
        """
        try:
            result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return result.stdout.strip()
            else:
                logger.error(f"Failed to get git SHA: {result.stderr}")
                return None
        except Exception as e:
            logger.error(f"Error getting git SHA: {e}")
            return None

    async def perform_graceful_shutdown(self) -> None:
        """
        Perform a graceful shutdown without restarting.

        This saves the current state to disk just like a restart,
        but doesn't perform git pull or exit with restart code.
        Used when the bot is killed with Ctrl-C or kill signal.
        """
        if self._restart_in_progress:
            logger.warning("Restart already in progress, graceful shutdown will proceed anyway")

        print("💾 Performing graceful shutdown...")

        try:
            # Step 1: Save current git SHA to state
            print("📝 Recording current git SHA...")
            current_sha = self._get_current_git_sha()
            if current_sha:
                state_service.set_last_git_sha(current_sha)
                print(f"✅ Recorded git SHA: {current_sha}")
            else:
                print("⚠️  Could not determine git SHA")

            # Step 2: Save current state
            print("💾 Saving bot state...")
            temp_file = state_service.save_state_to_temp_file()
            if temp_file:
                print("✅ State saved")

                # Save shutdown type information (not a restart)
                shutdown_info_file = temp_file.replace(".json", "_restart_info.json")
                try:
                    import json

                    with open(shutdown_info_file, "w") as f:
                        json.dump({"manual_restart": False, "graceful_shutdown": True}, f)
                    import os
                    import stat

                    os.chmod(shutdown_info_file, stat.S_IRUSR | stat.S_IWUSR)
                except Exception as e:
                    logger.warning(f"Failed to save shutdown info: {e}")
            else:
                print("⚠️  Failed to save state")

            # Step 3: Give a moment for any final operations
            await asyncio.sleep(0.5)

            print("✅ Graceful shutdown preparation complete")

        except Exception as e:
            logger.error(f"Error during graceful shutdown: {e}")
            print(f"⚠️  Error during graceful shutdown: {e}")
            raise


# Global restart handler instance
restart_handler = RestartHandler()
