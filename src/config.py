import os
from datetime import datetime
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

load_dotenv()

# API Keys with test-friendly defaults
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "test-key-for-ci")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "test-token-for-ci")

VECTOR_STORE_ID = os.getenv("VECTOR_STORE_ID", "")
# Bot Configuration
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gpt-5-mini")
DEFAULT_IMAGE_MODEL = os.getenv("DEFAULT_IMAGE_MODEL", "gpt-image-1.5")

# Default system prompt (fallback if file doesn't exist)
DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."


def load_system_prompt() -> str:
    """
    Load system prompt from system_prompt.txt file with variable replacement.

    Returns:
        System prompt string with [[CURRENT_DATE_AND_TIME]] replaced
    """
    try:
        with open("system_prompt.txt", "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content:
                # Replace [[CURRENT_DATE_AND_TIME]] with current date and time
                current_datetime = datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d %H:%M:%S %Z").strip()
                content = content.replace("[[CURRENT_DATE_AND_TIME]]", current_datetime)
                return content
    except FileNotFoundError:
        pass
    except (OSError, UnicodeDecodeError, PermissionError) as e:
        # Log error but don't crash - fall back to default
        print(f"Warning: Error loading system_prompt.txt: {e}")

    return DEFAULT_SYSTEM_PROMPT


def get_system_prompt() -> str:
    """
    Get the current system prompt with dynamic variable replacement.

    Returns:
        System prompt string with current date/time
    """
    return load_system_prompt()


# Boolean Configuration
INCLUDE_USERNAMES = os.getenv("INCLUDE_USERNAMES", "True").lower() in ("true", "1")
REPLY_TO_MENTIONS = os.getenv("REPLY_TO_MENTIONS", "True").lower() in ("true", "1")
KEEP_UP_TO_DATE_WITH_GIT = os.getenv("KEEP_UP_TO_DATE_WITH_GIT", "False").lower() in ("true", "1")
QUIET_UPDATES = os.getenv("QUIET_UPDATES", "False").lower() in ("true", "1")

# Numeric Configuration
INCLUDE_NUM_CHATLINES = int(os.getenv("INCLUDE_NUM_CHATLINES", 100))

# User Context Configuration
USER_CONTEXT_URL = os.getenv("USER_CONTEXT_URL", "")

# Proxy Configuration for yt-dlp
PROXY_ADDRESS = os.getenv("PROXY_ADDRESS", "")

# Check if we're in a testing environment
IS_TESTING = os.getenv("CI") == "true" or os.getenv("TESTING") == "true"
