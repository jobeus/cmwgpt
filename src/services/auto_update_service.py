"""
Restart trigger for the Discord bot.

Puts a restart on the bot's queue, for ``/restart`` and for the package update watcher.
(New commits arrive from outside: CI tells grizzly's deploy agent, deployd, which builds a
release and restarts the bot. The bot no longer listens for deploy requests itself.)
"""

import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


class AutoUpdateService:
    """Queues restarts of the bot."""

    def __init__(self, *, queue_service):
        self._queue_service = queue_service
        self._restart_callback: Optional[Callable[..., Awaitable[None]]] = None

    def set_restart_callback(self, callback: Callable[..., Awaitable[None]]) -> None:
        """Set the callback function to call when a restart is needed."""
        self._restart_callback = callback

    async def trigger_restart(self, manual: bool = False) -> bool:
        """Trigger a restart of the bot."""
        if not self._restart_callback:
            logger.error("No restart callback set")
            return False

        restart_type = "manual" if manual else "automatic"
        logger.info("Triggering %s restart", restart_type)

        async def restart_wrapper():
            await self._restart_callback(manual=manual)

        return await self._queue_service.queue_restart(restart_wrapper)
