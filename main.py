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
        from src.services.auto_update_service import auto_update_service
        from src.services.queue_service import queue_service
        from src.services.openai_service import openai_service

        async def complete_shutdown():
            """Complete shutdown sequence with proper cleanup."""
            try:
                # Step 1: Save state
                await restart_handler.perform_graceful_shutdown()
                print("👋 Graceful shutdown complete")

                # Step 2: Stop background services
                print("🛑 Stopping background services...")

                # Stop auto-update service (synchronous)
                try:
                    auto_update_service.stop()
                    logger.info("Auto-update service stopped")
                except Exception as e:
                    logger.error(f"Error stopping auto-update service: {e}")

                # Stop queue service (asynchronous)
                try:
                    await queue_service.stop()
                    logger.info("Queue service stopped")
                except Exception as e:
                    logger.error(f"Error stopping queue service: {e}")

                # Close OpenAI service
                try:
                    await openai_service.close()
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

                # Step 4: Cancel any remaining tasks
                print("🧹 Cleaning up remaining tasks...")
                try:
                    # Get all tasks except the current one
                    current_task = asyncio.current_task()
                    tasks = [task for task in asyncio.all_tasks() if task != current_task]

                    if tasks:
                        logger.info(f"Cancelling {len(tasks)} remaining tasks")
                        for task in tasks:
                            task.cancel()

                        # Wait for tasks to complete cancellation
                        await asyncio.gather(*tasks, return_exceptions=True)
                        logger.info("All tasks cancelled")

                except Exception as e:
                    logger.error(f"Error during task cleanup: {e}")

                print("✅ Shutdown complete, exiting...")

            except asyncio.CancelledError:
                pass  # Clean exit on cancellation

            except Exception as e:
                logger.error(f"Error during complete shutdown: {e}")
                print(f"⚠️  Error during shutdown: {e}")

            finally:
                # Force exit after cleanup
                import sys
                sys.exit(0)

        try:
            # Get or create event loop
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            # Run complete shutdown
            if loop.is_running():
                # If we're already in an async context, schedule the shutdown
                asyncio.create_task(complete_shutdown())
            else:
                # If we're not in an async context, run it
                loop.run_until_complete(complete_shutdown())

        except Exception as e:
            logger.error(f"Error during signal handler execution: {e}")
            print(f"⚠️  Critical error during shutdown: {e}")
            # Force exit if something goes wrong
            import sys
            sys.exit(1)

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    logger.info("Signal handlers registered for graceful shutdown")


if __name__ == "__main__":
    bot_client = create_bot()

    # Set up signal handlers for graceful shutdown
    setup_signal_handlers(bot_client)

    bot_client.run()
