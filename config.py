import os
from dotenv import load_dotenv

load_dotenv()

# API Keys with test-friendly defaults
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "test-key-for-ci")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "test-token-for-ci")

# Bot Configuration
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", "You are a helpful assistant.")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gpt-4.1-nano")
DEFAULT_IMAGE_MODEL = os.getenv("DEFAULT_IMAGE_MODEL", "gpt-image-1")

# Boolean Configuration
INCLUDE_USERNAMES = os.getenv(
    "INCLUDE_USERNAMES",
    "True").lower() in (
        "true",
    "1")
REPLY_TO_MENTIONS = os.getenv(
    "REPLY_TO_MENTIONS",
    "True").lower() in (
        "true",
    "1")

# Numeric Configuration
INCLUDE_NUM_CHATLINES = int(os.getenv("INCLUDE_NUM_CHATLINES", 100))

# Check if we're in a testing environment
IS_TESTING = os.getenv("CI") == "true" or os.getenv("TESTING") == "true"
