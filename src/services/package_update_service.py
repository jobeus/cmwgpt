"""
Package update watcher.

The bot runs the newest release of everything in requirements.txt, dependencies of
dependencies included: start.sh reinstalls with ``--upgrade --upgrade-strategy eager`` on
every restart. This asks pip every few hours whether that reinstall would change anything,
and restarts the bot when it would, so a new upstream release is picked up without waiting
for a commit.
"""

import asyncio
import json
import logging
import subprocess
import sys
from typing import Any, Awaitable, Callable, List, Optional

from src.telemetry import routine_span
from src.utils.discord_error_utils import concise_error

logger = logging.getLogger(__name__)

# What a restart was last asked for, so upgrades that didn't take (a release pip can't
# install, or a deployment whose restart doesn't reinstall) don't restart the bot in a loop.
STATE_KEY = "package_update_trigger"
PIP_TIMEOUT_SECONDS = 300


def pending_upgrades(requirements_path: str = "requirements.txt") -> Optional[List[str]]:
    """``name==version`` for every package a full upgrade would install, or None if pip failed.

    Runs pip's resolver without installing anything (blocking; call it in a thread).
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--dry-run", "--quiet", "--upgrade",
             "--upgrade-strategy", "eager", "-r", requirements_path, "--report", "-"],
            capture_output=True, text=True, timeout=PIP_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning("Couldn't check for package updates: %s", e)
        return None
    if result.returncode != 0:
        logger.warning("pip couldn't check for package updates: %s", result.stderr.strip()[-500:])
        return None
    try:
        installs = json.loads(result.stdout).get("install", [])
        return sorted(f"{item['metadata']['name']}=={item['metadata']['version']}" for item in installs)
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        logger.warning("Couldn't read pip's report of package updates: %s", e)
        return None


class PackageUpdateService:
    """Restarts the bot when newer releases of its packages are out."""

    def __init__(
        self,
        *,
        store: Any,
        restart: Callable[[], Awaitable[bool]],
        interval_hours: float = 6.0,
        is_testing: bool = False,
        check: Callable[[], Optional[List[str]]] = pending_upgrades,
    ):
        self._store = store
        self._restart = restart
        self._interval_seconds = interval_hours * 3600
        self._is_testing = is_testing
        self._check = check
        self._task: Optional[asyncio.Task] = None

    @property
    def enabled(self) -> bool:
        return self._interval_seconds > 0 and not self._is_testing

    def start(self) -> None:
        if not self.enabled:
            logger.info("Package update checks are off")
            return
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())
        logger.info("Checking for package updates every %.1f hours", self._interval_seconds / 3600)

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _loop(self) -> None:
        while True:
            # Sleep first: a start has just installed the newest of everything.
            await asyncio.sleep(self._interval_seconds)
            try:
                with routine_span("package update check"):
                    await self.check_once()
            except Exception as e:
                logger.warning("Package update check failed: %s", concise_error(e))

    async def check_once(self) -> bool:
        """Check now. Returns whether a restart was started."""
        pending = await asyncio.to_thread(self._check)
        if not pending:
            if pending is not None:
                logger.debug("Every package is up to date")
            return False
        if self._store.get(STATE_KEY) == pending:
            logger.warning(
                "Package updates are still pending after the restart for them, so not restarting again: %s",
                ", ".join(pending))
            return False
        if not self._store.set(STATE_KEY, pending):
            # With nowhere to note what this restart is for, an upgrade that doesn't take
            # would restart the bot on every check.
            logger.warning("Package updates are out, but Redis is down, so not restarting yet: %s", ", ".join(pending))
            return False
        logger.info("Package updates are out, restarting to install them: %s", ", ".join(pending))
        return bool(await self._restart())
