"""Main entry point for the Discord bot using explicit startup composition."""

from src.startup import create_bot_client
import signal
import asyncio
import logging

logger = logging.getLogger(__name__)


def setup_signal_handlers(bot_client):
    """Set up signal handlers for graceful shutdown.

    Handlers are registered with ``loop.add_signal_handler`` once the bot's
    event loop exists (via the bot's ``setup_hook``), instead of using a raw
    synchronous ``signal.signal`` handler. The shutdown sequence is guarded by
    a flag so a second Ctrl-C doesn't spawn a duplicate shutdown task.
    """

    shutdown_state = {"in_progress": False}

    async def complete_shutdown():
        """Complete shutdown sequence with proper cleanup."""
        try:
            # Step 1: Save state
            await bot_client.services.restart_handler.perform_graceful_shutdown()
            print("👋 Graceful shutdown complete")

            # Step 2: Stop background services
            print("🛑 Stopping background services...")

            # Stop auto-update service (synchronous)
            try:
                bot_client.services.auto_update_service.stop()
                logger.info("Auto-update service stopped")
            except Exception as e:
                logger.error(f"Error stopping auto-update service: {e}")

            # Stop death service (synchronous)
            try:
                bot_client.services.death_service.stop()
                logger.info("Death service stopped")
            except Exception as e:
                logger.error(f"Error stopping death service: {e}")

            # Stop queue service (asynchronous)
            try:
                await bot_client.services.queue_service.stop()
                logger.info("Queue service stopped")
            except Exception as e:
                logger.error(f"Error stopping queue service: {e}")

            # Close OpenAI service
            try:
                await bot_client.services.openai_service.close()
                logger.info("OpenAI service closed")
            except Exception as e:
                logger.error(f"Error closing OpenAI service: {e}")

            # Step 3: Close Discord bot
            print("🔌 Closing Discord connection...")
            try:
                if bot_client.bot and not bot_client.bot.is_closed():
                    await bot_client.bot.close()
                    logger.info("Discord bot closed")
            except Exception as e:
                logger.error(f"Error closing Discord bot: {e}")
                print(f"⚠️  Error closing Discord bot: {e}")

            # We rely on asyncio.run() to natively cancel remaining tasks upon returning!
            print("✅ Shutdown complete, exiting...")

        except asyncio.CancelledError:
            pass  # Clean exit on cancellation

        except Exception as e:
            logger.error(f"Error during complete shutdown: {e}")
            print(f"⚠️  Error during shutdown: {e}")

        finally:
            # We do not call sys.exit(0) here because it raises a SystemExit exception
            # inside the asyncio event loop, causing a messy stack trace during cleanup!
            pass

    def handle_signal(signum, loop):
        """Loop-safe signal callback. Idempotent: repeated signals are ignored."""
        signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"

        if shutdown_state["in_progress"]:
            print(f"\n🛑 {signal_name} received again, shutdown already in progress...")
            logger.info(
                f"Received {signal_name} while shutdown already in progress, ignoring")
            return

        shutdown_state["in_progress"] = True
        print(f"\n🛑 Received {signal_name}, performing graceful shutdown...")
        logger.info(f"Received {signal_name}, initiating graceful shutdown")

        loop.create_task(complete_shutdown())

    def register_signal_handlers(loop):
        """Register loop-based handlers for SIGINT and SIGTERM."""
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


if __name__ == "__main__":
    bot_client = create_bot_client()

    # Set up signal handlers for graceful shutdown
    setup_signal_handlers(bot_client)

    bot_client.run()
