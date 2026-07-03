"""
Logger setup with colors, file output, and better clarity.
"""
import copy
import logging
import logging.handlers
import re
import sys
from pathlib import Path

# ANSI colors
COLORS = {
    'DEBUG': '\033[94m',       # Blue
    'INFO': '\033[92m',        # Green
    'WARNING': '\033[93m',     # Yellow
    'ERROR': '\033[91m',       # Red
    'CRITICAL': '\033[1;91m',  # Bold Red
    'RESET': '\033[0m'         # Reset
}

EMOJIS = {
    'DEBUG': '🐛',
    'INFO': 'ℹ️',
    'WARNING': '⚠️',
    'ERROR': '❌',
    'CRITICAL': '🚨'
}


class ColoredEmojiFormatter(logging.Formatter):
    """Custom format with color and emojis"""

    def format(self, record):
        color = COLORS.get(record.levelname, COLORS['RESET'])
        emoji = EMOJIS.get(record.levelname, '')
        # Only add colors if not already ANSI formatted
        msg = str(record.msg)

        # We modify a copy of the record to not affect the file logger
        record_copy = copy.copy(record)

        record_copy.levelname = f"{color}{emoji} {record_copy.levelname}{COLORS['RESET']}"
        record_copy.msg = f"{color}{msg}{COLORS['RESET']}"
        record_copy.name = f"\033[95m{record_copy.name}{COLORS['RESET']}"

        return super().format(record_copy)


def setup_logger():
    # Remove existing default basicConfig handlers if any
    root = logging.getLogger()
    if root.handlers:
        for handler in root.handlers[:]:
            root.removeHandler(handler)

    # Set base level
    root.setLevel(logging.DEBUG)  # Let handlers filter

    class ANSIStripperFormatter(logging.Formatter):
        """Removes ANSI color codes for file logging"""

        def format(self, record):
            msg = super().format(record)
            ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
            return ansi_escape.sub('', msg)

    file_format = ANSIStripperFormatter('%(asctime)s - %(levelname)s - [%(name)s] - %(message)s')

    # Console handler with colors
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)  # Keep console mostly clean
    console_handler.setFormatter(ColoredEmojiFormatter('%(asctime)s - %(levelname)s - [%(name)s] - %(message)s', '%H:%M:%S'))
    root.addHandler(console_handler)

    # File handler. Rotates at midnight so a long-running process doesn't keep
    # writing weeks of logs into its launch-date file. The active file is
    # logs/bot.log; the namer renames rotated files to the historical
    # logs/YYYY-MM-DD.log pattern.
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_dir / "bot.log", when="midnight", backupCount=60, encoding="utf-8"
    )
    file_handler.suffix = "%Y-%m-%d.log"

    def _dated_log_namer(default_name: str) -> str:
        # default_name looks like "logs/bot.log.2026-07-03.log"
        return str(log_dir / Path(default_name).name.replace("bot.log.", "", 1))

    file_handler.namer = _dated_log_namer
    file_handler.setLevel(logging.DEBUG)  # Keep full history in file
    file_handler.setFormatter(file_format)
    root.addHandler(file_handler)

    # Silence noisy loggers in terminal & file
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai._base_client").setLevel(logging.WARNING)

    return root
