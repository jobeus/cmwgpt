"""
Main entry point for the Discord bot using the refactored architecture.
"""

from src.bot.client import create_bot
import sys
import os

# Add src directory to Python path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


if __name__ == "__main__":
    bot_client = create_bot()
    bot_client.run()
