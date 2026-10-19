"""Main entry point for the Discord bot using explicit startup composition."""

import asyncio
import logging
import signal
import sys

from src.startup import create_bot_client

logger = logging.getLogger(__name__)


def setup_signal_handlers(bot_client):
    """Shut down gracefully on SIGINT/SIGTERM.

    Handlers are registered with ``loop.add_signal_handler`` once the bot's
    event loop exists (via the bot's ``setup_hook``). A second signal while
    shutdown is running is ignored.
    """

    shutdown_state = {"in_progress": False}

    async def complete_shutdown():
        try:
            await bot_client.services.restart_handler.perform_graceful_shutdown()
            await bot_client.shutdown()
            print("✅ Shutdown complete, exiting...")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            print(f"⚠️  Error during shutdown: {e}")

    def handle_signal(signum, loop):
        signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
        if shutdown_state["in_progress"]:
            print(f"\n🛑 {signal_name} received again, shutdown already in progress...")
            return
        shutdown_state["in_progress"] = True
        print(f"\n🛑 Received {signal_name}, performing graceful shutdown...")
        logger.info(f"Received {signal_name}, initiating graceful shutdown")
        loop.create_task(complete_shutdown())

    def register_signal_handlers(loop):
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, handle_signal, sig, loop)
        logger.info("Signal handlers registered for graceful shutdown")

    # The event loop doesn't exist yet (bot.run() creates it), so hook the
    # registration into the bot's setup_hook, which runs inside the loop.
    original_setup_hook = bot_client.bot.setup_hook

    async def setup_hook_with_signals():
        register_signal_handlers(asyncio.get_running_loop())
        await original_setup_hook()

    bot_client.bot.setup_hook = setup_hook_with_signals

    return register_signal_handlers, handle_signal, complete_shutdown


def main() -> None:
    bot_client = create_bot_client()
    setup_signal_handlers(bot_client)
    bot_client.run()

    exit_code = bot_client.services.restart_handler.exit_code
    if exit_code is not None:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
