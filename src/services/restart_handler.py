"""
Restart handler for the Discord bot.

A restart saves state and asks the deploy agent (deployd on grizzly) for a fresh release of
the latest code. deployd builds it, newest packages included, while the bot keeps running,
then stops the bot with SIGTERM (the normal shutdown, so replies in progress finish and
clients close) and starts the new release.

Where there is no deploy agent (a local run), the bot shuts itself down instead and exits
with :data:`RESTART_EXIT_CODE`; whatever runs it can start it again.
"""

import asyncio
import logging
from typing import Any, Callable, Coroutine, Optional

from src.utils.deploy_utils import request_redeploy as default_request_redeploy

logger = logging.getLogger(__name__)

RESTART_EXIT_CODE = 42
# A requested release normally replaces this process within a few minutes. If it still
# hasn't after this long (its build failed, say), restarts are allowed again.
REDEPLOY_WAIT_SECONDS = 20 * 60


class RestartHandler:
    """Handles bot restart operations with state persistence."""

    def __init__(
        self,
        *,
        state_service,
        redeploy: Callable[[], bool] = default_request_redeploy,
    ):
        self._state_service = state_service
        self._redeploy = redeploy
        self._restart_in_progress = False
        self._manual_restart = False
        self._shutdown: Optional[Callable[[], Coroutine[Any, Any, None]]] = None
        self._shutdown_task: Optional[asyncio.Task] = None
        self._give_up_task: Optional[asyncio.Task] = None
        # Set when the process should exit with a specific code after the loop ends.
        self.exit_code: Optional[int] = None

    def set_shutdown_callback(self, shutdown: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """The coroutine that shuts the bot down (services, then the Discord connection)."""
        self._shutdown = shutdown

    async def perform_restart(self, manual: bool = False) -> None:
        """Save state and ask for a fresh release; without a deploy agent, shut down to restart."""
        if self._restart_in_progress:
            logger.warning("Restart already in progress, ignoring duplicate request")
            return
        self._restart_in_progress = True
        self._manual_restart = manual
        logger.info("Restarting the bot (%s)", "manual /restart" if manual else "automatic")

        if self._state_service.save_restart_state({"manual_restart": manual}):
            logger.info("Saved state to Redis for the restart")
        else:
            logger.warning("Couldn't save state to Redis for the restart; continuing anyway")

        # The request is a subprocess; keep it off the loop so heartbeats continue.
        try:
            requested = await asyncio.to_thread(self._redeploy)
        except Exception as e:
            logger.error("Asking for a release raised: %s", e, exc_info=True)
            requested = False
        if requested:
            logger.info("A fresh release is being built; the deploy agent restarts the bot onto it")
            self._give_up_task = asyncio.get_running_loop().create_task(self._allow_restarts_again_later())
            return

        logger.warning("No deploy agent to ask; restarting on the current code")
        self.exit_code = RESTART_EXIT_CODE
        if self._shutdown is None:
            logger.error("No shutdown callback configured; cannot restart cleanly")
            return
        # This usually runs inside a queue worker, which shutdown waits on, so
        # run the shutdown as its own task instead of awaiting it here.
        self._shutdown_task = asyncio.get_running_loop().create_task(self._shutdown())

    async def _allow_restarts_again_later(self) -> None:
        await asyncio.sleep(REDEPLOY_WAIT_SECONDS)
        logger.warning("The release asked for never replaced this process; allowing restarts again")
        self._restart_in_progress = False
        self._manual_restart = False

    async def perform_graceful_shutdown(self) -> None:
        """Save state before a shutdown from outside (Ctrl-C, or SIGTERM from the deploy agent)."""
        # The deploy agent stopping us for a release /restart asked for is still a manual restart.
        manual = self._restart_in_progress and self._manual_restart
        if self._state_service.save_restart_state({"manual_restart": manual, "graceful_shutdown": True}):
            logger.info("Saved state before shutting down")
        else:
            logger.warning("Couldn't save state before shutting down")
