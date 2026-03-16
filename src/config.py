import os
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional

from dotenv import find_dotenv, load_dotenv
from zoneinfo import ZoneInfo

DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."


def _read_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.lower() in ("true", "1")


def _read_int(value: Optional[str], default: int) -> int:
    if value is None:
        return default
    return int(value)


def resolve_env_file_path(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    source = os.environ if env is None else env
    explicit_path = source.get("CMWGPT_ENV_FILE", "").strip()
    if explicit_path:
        return explicit_path

    for candidate in (".env.production", ".env"):
        resolved_path = find_dotenv(candidate, usecwd=True)
        if resolved_path:
            return resolved_path

    return None


def load_resolved_env_file(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    env_file_path = resolve_env_file_path(env)
    if env_file_path:
        load_dotenv(env_file_path)
    return env_file_path


@dataclass(frozen=True)
class AppConfig:
    openrouter_api_key: str
    gemini_api_key: str
    runpod_io_api_key: str
    groq_api_key: str
    rapidapi_key: str
    discord_bot_token: str
    discord_guild_id: str
    death_channel_id: str
    transcript_proxy: str
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_name: str
    default_model: str
    default_draw_model: str
    default_edit_model: str
    include_usernames: bool
    reply_to_mentions: bool
    keep_up_to_date_with_git: bool
    quiet_updates: bool
    include_num_chatlines: int
    max_cache_size: int
    is_testing: bool
    system_prompt_path: str = "system_prompt.txt"


def load_config(
    env: Optional[Mapping[str, str]] = None,
    *,
    load_env_file: bool = True,
    system_prompt_path: str = "system_prompt.txt",
) -> AppConfig:
    if load_env_file:
        load_resolved_env_file(env)

    source = os.environ if env is None else env
    return AppConfig(
        openrouter_api_key=source.get("OPENROUTER_API_KEY", "test-key-for-ci"),
        gemini_api_key=source.get("GEMINI_API_KEY", ""),
        runpod_io_api_key=source.get("RUNPOD_IO_API_KEY", ""),
        groq_api_key=source.get("GROQ_API_KEY", ""),
        rapidapi_key=source.get("RAPIDAPI_KEY", ""),
        discord_bot_token=source.get("DISCORD_BOT_TOKEN", "test-token-for-ci"),
        discord_guild_id=source.get("DISCORD_GUILD_ID", ""),
        death_channel_id=source.get("DEATH_CHANNEL_ID", ""),
        transcript_proxy=source.get("TRANSCRIPT_PROXY", ""),
        db_host=source.get("DB_HOST", "127.0.0.1"),
        db_port=_read_int(source.get("DB_PORT"), 3306),
        db_user=source.get("DB_USER", "cmwgpt_user"),
        db_password=source.get("DB_PASSWORD", ""),
        db_name=source.get("DB_NAME", "cmwgpt"),
        default_model=source.get("DEFAULT_MODEL", "anthropic/claude-haiku-4.5"),
        default_draw_model=source.get("DEFAULT_DRAW_MODEL", "seedream"),
        default_edit_model=source.get("DEFAULT_EDIT_MODEL", "seedream"),
        include_usernames=_read_bool(source.get("INCLUDE_USERNAMES"), True),
        reply_to_mentions=_read_bool(source.get("REPLY_TO_MENTIONS"), True),
        keep_up_to_date_with_git=_read_bool(source.get("KEEP_UP_TO_DATE_WITH_GIT"), False),
        quiet_updates=_read_bool(source.get("QUIET_UPDATES"), False),
        include_num_chatlines=_read_int(source.get("INCLUDE_NUM_CHATLINES"), 10),
        max_cache_size=_read_int(source.get("MAX_CACHE_SIZE"), 200),
        is_testing=source.get("CI") == "true" or source.get("TESTING") == "true",
        system_prompt_path=system_prompt_path,
    )


def load_system_prompt(prompt_path: str = "system_prompt.txt") -> str:
    """Load the system prompt file with dynamic timestamp replacement."""
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content:
                current_datetime = datetime.now(
                    ZoneInfo("America/Denver")
                ).strftime("%Y-%m-%d %H:%M:%S %Z").strip()
                return content.replace("[[CURRENT_DATE_AND_TIME]]", current_datetime)
    except FileNotFoundError:
        pass
    except (OSError, UnicodeDecodeError, PermissionError) as e:
        print(f"Warning: Error loading {prompt_path}: {e}")

    return DEFAULT_SYSTEM_PROMPT


def get_system_prompt(prompt_path: str = "system_prompt.txt") -> str:
    """Return the current system prompt without module import side effects."""
    return load_system_prompt(prompt_path)


# Backward-compatibility path for modules that still import config constants at
# module import time. This must continue loading `.env` so existing non-Docker
# production startup keeps working until all callers move to injected config.
LEGACY_CONFIG = load_config()

OPENROUTER_API_KEY = LEGACY_CONFIG.openrouter_api_key
GEMINI_API_KEY = LEGACY_CONFIG.gemini_api_key
RUNPOD_IO_API_KEY = LEGACY_CONFIG.runpod_io_api_key
GROQ_API_KEY = LEGACY_CONFIG.groq_api_key
RAPIDAPI_KEY = LEGACY_CONFIG.rapidapi_key
DISCORD_BOT_TOKEN = LEGACY_CONFIG.discord_bot_token
DISCORD_GUILD_ID = LEGACY_CONFIG.discord_guild_id
DEATH_CHANNEL_ID = LEGACY_CONFIG.death_channel_id
TRANSCRIPT_PROXY = LEGACY_CONFIG.transcript_proxy
DB_HOST = LEGACY_CONFIG.db_host
DB_PORT = LEGACY_CONFIG.db_port
DB_USER = LEGACY_CONFIG.db_user
DB_PASSWORD = LEGACY_CONFIG.db_password
DB_NAME = LEGACY_CONFIG.db_name
DEFAULT_MODEL = LEGACY_CONFIG.default_model
DEFAULT_DRAW_MODEL = LEGACY_CONFIG.default_draw_model
DEFAULT_EDIT_MODEL = LEGACY_CONFIG.default_edit_model
INCLUDE_USERNAMES = LEGACY_CONFIG.include_usernames
REPLY_TO_MENTIONS = LEGACY_CONFIG.reply_to_mentions
KEEP_UP_TO_DATE_WITH_GIT = LEGACY_CONFIG.keep_up_to_date_with_git
QUIET_UPDATES = LEGACY_CONFIG.quiet_updates
INCLUDE_NUM_CHATLINES = LEGACY_CONFIG.include_num_chatlines
MAX_CACHE_SIZE = LEGACY_CONFIG.max_cache_size
IS_TESTING = LEGACY_CONFIG.is_testing
