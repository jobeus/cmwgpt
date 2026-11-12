"""
Git utility functions for repository operations.
"""

import logging
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


def is_git_repository() -> bool:
    """Check if the current directory is a git repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"], capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.error("Error checking git repository status: %s", e)
        return False


def get_current_commit_hash() -> Optional[str]:
    """Get the current commit hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            logger.error("Failed to get current commit hash: %s", result.stderr)
            return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.error("Error getting current commit hash: %s", e)
        return None


def perform_git_pull() -> bool:
    """Perform git pull to update the code."""
    try:
        # First, check if we're in a git repository
        if not is_git_repository():
            logger.warning("Not in a git repository, skipping git pull")
            return False

        # Check if there are any uncommitted changes
        result = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, timeout=10)

        if result.returncode == 0 and result.stdout.strip():
            logger.warning("Uncommitted changes detected, git pull may fail")

        # Perform the git pull
        result = subprocess.run(
            ["git", "pull", "origin"], capture_output=True, text=True, timeout=60)

        if result.returncode == 0:
            output = result.stdout.strip()
            if "Already up to date" in output:
                logger.debug("Git pull: Already up to date")
            elif output:
                logger.info("Git pull: %s", output)
            return True
        else:
            logger.error(
                "Git pull failed with return code %s", result.returncode
            )
            if result.stderr.strip():
                logger.error("Git pull error: %s", result.stderr.strip())
            return False

    except subprocess.TimeoutExpired:
        logger.error("Git pull timed out")
        return False
    except (FileNotFoundError, OSError) as e:
        logger.error("Error during git pull: %s", e)
        return False
