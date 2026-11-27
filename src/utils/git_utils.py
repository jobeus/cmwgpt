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
