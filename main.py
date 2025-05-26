"""
Main entry point for the Discord bot using the refactored architecture.
"""

from src.bot.client import create_bot
import sys
import os
import signal
import asyncio
import logging

# Add src directory to Python path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

logger = logging.getLogger(__name__)


def setup_signal_handlers(bot_client):
    """Set up signal handlers for graceful shutdown."""

    def signal_handler(signum, frame):
        """Handle SIGINT and SIGTERM signals for graceful shutdown."""
        signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
        print(f"\n🛑 Received {signal_name}, performing graceful shutdown...")
        logger.info(f"Received {signal_name}, initiating graceful shutdown")

        # Import here to avoid circular imports
        from src.services.restart_handler import restart_handler

        try:
            # Create an event loop if one doesn't exist
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            # Run the graceful shutdown
            if loop.is_running():
                # If we're already in an async context, schedule the shutdown
                asyncio.create_task(restart_handler.perform_graceful_shutdown())
            else:
                # If we're not in an async context, run it
                loop.run_until_complete(restart_handler.perform_graceful_shutdown())

        except Exception as e:
            logger.error(f"Error during graceful shutdown: {e}")
            print(f"⚠️  Error during graceful shutdown: {e}")

        # Exit normally (not with restart code)
        print("👋 Graceful shutdown complete")
        sys.exit(0)

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    logger.info("Signal handlers registered for graceful shutdown")


if __name__ == "__main__":
    bot_client = create_bot()

    # Set up signal handlers for graceful shutdown
    setup_signal_handlers(bot_client)

    bot_client.run()
