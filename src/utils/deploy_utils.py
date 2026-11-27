"""Asking the deploy agent for a fresh release of the bot."""

import logging
import os
import shlex
import subprocess

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 30


def request_redeploy() -> bool:
    """Ask deployd to build and switch to a fresh release of the latest code. True if it agreed.

    On grizzly the unit sets ``APP_NAME`` and ``deploy request <name> --force`` hands the job
    to deployd, which builds the release (newest packages included) while this process keeps
    running and then restarts the unit. Anywhere else there is nobody to ask: False.
    ``REDEPLOY_COMMAND`` replaces the command.
    """
    app_name = os.getenv("APP_NAME", "").strip()
    command = os.getenv("REDEPLOY_COMMAND", "").strip()
    if not command:
        if not app_name:
            return False
        command = f"deploy request {shlex.quote(app_name)} --force"
    try:
        result = subprocess.run(
            shlex.split(command), capture_output=True, text=True, timeout=REQUEST_TIMEOUT_SECONDS)
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        logger.warning("Couldn't ask the deploy agent for a release: %s", e)
        return False
    if result.returncode != 0:
        logger.warning(
            "The deploy agent refused (%s): %s", result.returncode,
            (result.stderr or result.stdout).strip()[-500:])
        return False
    logger.info("Deploy agent: %s", result.stdout.strip() or "accepted")
    return True
