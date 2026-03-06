"""
Git utility functions for repository operations.

Extracted from service classes to follow the principle of separating
stateless utility functions from stateful service classes.
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
        logger.error(f"Error checking git repository status: {e}")
        return False


def get_current_commit_hash() -> Optional[str]:
    """Get the current commit hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            logger.error(f"Failed to get current commit hash: {result.stderr}")
            return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.error(f"Error getting current commit hash: {e}")
        return None


def get_current_branch() -> Optional[str]:
    """Get the current branch name."""
    try:
        result = subprocess.run(["git",
                                 "branch",
                                 "--show-current"],
                                capture_output=True,
                                text=True,
                                timeout=10)
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            logger.error(f"Failed to get current branch: {result.stderr}")
            return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.error(f"Error getting current branch: {e}")
        return None


def fetch_updates() -> bool:
    """Fetch updates from origin."""
    try:
        result = subprocess.run(
            ["git", "fetch", "origin"], capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            logger.debug("Successfully fetched updates from origin")
            return True
        else:
            logger.error(f"Failed to fetch updates: {result.stderr}")
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.error(f"Error fetching updates: {e}")
        return False


def check_for_new_commits() -> bool:
    """Check if there are new commits on origin."""
    try:
        branch = get_current_branch()
        if not branch:
            return False

        # Count commits between HEAD and origin/branch
        result = subprocess.run(["git",
                                 "rev-list",
                                 f"HEAD..origin/{branch}",
                                 "--count"],
                                capture_output=True,
                                text=True,
                                timeout=10)

        if result.returncode == 0:
            commit_count = int(result.stdout.strip())
            if commit_count > 0:
                logger.info(
                    f"Found {commit_count} new commits on origin/{branch}")
                return True
            else:
                logger.debug(f"No new commits on origin/{branch}")
                return False
        else:
            logger.error(f"Failed to check for new commits: {result.stderr}")
            return False

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError) as e:
        logger.error(f"Error checking for new commits: {e}")
        return False


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
                logger.info(f"Git pull: {output}")
            return True
        else:
            logger.error(
                f"Git pull failed with return code {result.returncode}"
            )
            if result.stderr.strip():
                logger.error(f"Git pull error: {result.stderr.strip()}")
            return False

    except subprocess.TimeoutExpired:
        logger.error("Git pull timed out")
        return False
    except (FileNotFoundError, OSError) as e:
        logger.error(f"Error during git pull: {e}")
        return False
