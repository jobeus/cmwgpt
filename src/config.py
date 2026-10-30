import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional

from dotenv import find_dotenv, load_dotenv
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."

# Paths we've already warned about so a missing prompt file only logs once.
_missing_prompt_files_warned: set = set()


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
    anthropic_api_key: str
    runpod_io_api_key: str
    groq_api_key: str
    rapidapi_key: str
    discord_bot_token: str
    discord_guild_id: str
    death_channel_id: str
    transcript_proxy: str
    redis_url: str
    default_model: str
    default_draw_model: str
    default_edit_model: str
    reply_to_mentions: bool
    quiet_updates: bool
    include_num_chatlines: int
    is_testing: bool
    system_prompt_path: str = "system_prompt.txt"
    # Model the nightly user-memory job uses to read chat and update profiles.
    memory_model: str = "sonnet-5.5-direct"
    # Signed deploy webhook (GitHub Actions -> bot); disabled when the secret is empty.
    deploy_webhook_secret: str = ""
    deploy_webhook_host: str = "0.0.0.0"
    deploy_webhook_port: int = 8787
    # The channel's old IRC log, searchable by the direct models; the tools are off when unset.
    irc_log_path: str = ""
    # An irssistats-style nicks file ("canonical regex" per line) that merges IRC aliases.
    irc_nicks_path: str = ""
    # The old IRC quote bot's quotes, one per line; the old_quotes tool is off when unset.
    old_quote_bot_path: str = ""


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
        anthropic_api_key=source.get("ANTHROPIC_API_KEY", ""),
        runpod_io_api_key=source.get("RUNPOD_IO_API_KEY", ""),
        groq_api_key=source.get("GROQ_API_KEY", ""),
        rapidapi_key=source.get("RAPIDAPI_KEY", ""),
        discord_bot_token=source.get("DISCORD_BOT_TOKEN", "test-token-for-ci"),
        discord_guild_id=source.get("DISCORD_GUILD_ID", ""),
        death_channel_id=source.get("DEATH_CHANNEL_ID", ""),
        transcript_proxy=source.get("TRANSCRIPT_PROXY", ""),
        redis_url=source.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
        default_model=source.get("DEFAULT_MODEL", "anthropic/claude-sonnet-5.5"),
        default_draw_model=source.get("DEFAULT_DRAW_MODEL", "seedream"),
        default_edit_model=source.get("DEFAULT_EDIT_MODEL", "seedream"),
        reply_to_mentions=_read_bool(source.get("REPLY_TO_MENTIONS"), True),
        quiet_updates=_read_bool(source.get("QUIET_UPDATES"), False),
        include_num_chatlines=_read_int(source.get("INCLUDE_NUM_CHATLINES"), 10),
        is_testing=source.get("CI") == "true" or source.get("TESTING") == "true",
        system_prompt_path=system_prompt_path,
        memory_model=source.get("MEMORY_MODEL", "sonnet-5.5-direct"),
        deploy_webhook_secret=source.get("DEPLOY_WEBHOOK_SECRET", ""),
        deploy_webhook_host=source.get("DEPLOY_WEBHOOK_HOST", "0.0.0.0"),
        deploy_webhook_port=_read_int(source.get("DEPLOY_WEBHOOK_PORT"), 8787),
        irc_log_path=source.get("IRC_LOG", "").strip(),
        irc_nicks_path=source.get("IRC_NICKS", "").strip(),
        old_quote_bot_path=source.get("OLD_QUOTE_BOT", "").strip(),
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
        if prompt_path not in _missing_prompt_files_warned:
            _missing_prompt_files_warned.add(prompt_path)
            logger.warning(
                f"System prompt file '{prompt_path}' not found; "
                f"falling back to default system prompt.")
    except (OSError, UnicodeDecodeError, PermissionError) as e:
        print(f"Warning: Error loading {prompt_path}: {e}")

    return DEFAULT_SYSTEM_PROMPT


def get_system_prompt(prompt_path: str = "system_prompt.txt") -> str:
    """Return the current system prompt without module import side effects."""
    return load_system_prompt(prompt_path)


# Process-wide config, loaded lazily on first use. Consumers that have no DI seam
# (free functions in src/utils and src/db) call get_config() at call time instead
# of importing module-level constants, so importing src.config has no side effects
# and AppConfig remains the single source of truth.
_cached_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """Return the process-wide :class:`AppConfig`, loading it once on first use."""
    global _cached_config
    if _cached_config is None:
        _cached_config = load_config()
    return _cached_config


def reset_config_cache() -> None:
    """Clear the cached config (primarily for tests)."""
    global _cached_config
    _cached_config = None
