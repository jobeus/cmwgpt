import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from dotenv import find_dotenv, load_dotenv
from zoneinfo import ZoneInfo

from src.services.model_catalog import canonical_model

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."

# Paths we've already warned about so a missing prompt file only logs once.
_missing_prompt_files_warned: set = set()


def _read_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.lower() in ("true", "1")


def _read_optional_int(value: Optional[str]) -> Optional[int]:
    value = (value or "").strip()
    return int(value) if value.isdigit() else None


def _read_int(value: Optional[str], default: int) -> int:
    if value is None:
        return default
    return int(value)


def _read_float(value: Optional[str], default: float) -> float:
    try:
        return float(value) if value and value.strip() else default
    except ValueError:
        return default


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
    gemini_api_key: str
    anthropic_api_key: str
    groq_api_key: str
    rapidapi_key: str
    discord_bot_token: str
    discord_guild_id: str
    death_channel_id: str
    transcript_proxy: str
    redis_url: str
    default_model: str
    default_image_model: str
    reply_to_mentions: bool
    quiet_updates: bool
    include_num_chatlines: int
    is_testing: bool
    system_prompt_path: str = "system_prompt.txt"
    # Model the nightly user-memory job uses to read chat and update profiles.
    memory_model: str = "sonnet"
    # How often to ask pip whether newer packages are out and restart onto them; 0 turns it off.
    package_update_check_hours: float = 6.0
    # The channel's old IRC log, searchable by the chat models; the tools are off when unset.
    irc_log_path: str = ""
    # An irssistats-style nicks file ("canonical regex" per line) that merges IRC aliases.
    irc_nicks_path: str = ""
    # The old IRC quote bot's quotes, one per line; the old_quotes tool is off when unset.
    old_quote_bot_path: str = ""
    # A Logfire key with project:read, so the chat models can query the bot's own traces.
    logfire_read_token: str = ""
    logfire_mcp_url: str = "https://logfire-us.pydantic.dev/mcp"
    # neon (jobe's own logs and traces) instead of Logfire for that tool: both set = neon.
    neon_read_token: str = ""
    neon_mcp_url: str = ""
    # The one channel where mentions get that logs tool; off everywhere when unset.
    logs_channel_id: Optional[int] = None
    # Alpaca paper-account keys: market_data and the paper-trading game's tools are off when unset.
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    # Giphy API key: the search_gifs and post_gif tools are off when unset.
    giphy_api_key: str = ""


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
        gemini_api_key=source.get("GEMINI_API_KEY", ""),
        anthropic_api_key=source.get("ANTHROPIC_API_KEY", ""),
        groq_api_key=source.get("GROQ_API_KEY", ""),
        rapidapi_key=source.get("RAPIDAPI_KEY", ""),
        discord_bot_token=source.get("DISCORD_BOT_TOKEN", "test-token-for-ci"),
        discord_guild_id=source.get("DISCORD_GUILD_ID", ""),
        death_channel_id=source.get("DEATH_CHANNEL_ID", ""),
        transcript_proxy=source.get("TRANSCRIPT_PROXY", ""),
        redis_url=source.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
        default_model=canonical_model(source.get("DEFAULT_MODEL", "sonnet")),
        default_image_model=source.get("DEFAULT_IMAGE_MODEL", "gemini-nano-banana-2.1"),
        reply_to_mentions=_read_bool(source.get("REPLY_TO_MENTIONS"), True),
        quiet_updates=_read_bool(source.get("QUIET_UPDATES"), False),
        include_num_chatlines=_read_int(source.get("INCLUDE_NUM_CHATLINES"), 10),
        is_testing=source.get("CI") == "true" or source.get("TESTING") == "true",
        system_prompt_path=system_prompt_path,
        memory_model=canonical_model(source.get("MEMORY_MODEL", "sonnet")),
        package_update_check_hours=_read_float(source.get("PACKAGE_UPDATE_CHECK_HOURS"), 6.0),
        irc_log_path=source.get("IRC_LOG", "").strip(),
        irc_nicks_path=source.get("IRC_NICKS", "").strip(),
        old_quote_bot_path=source.get("OLD_QUOTE_BOT", "").strip(),
        # Its own key if set, else the tracing key (fine when that one also has project:read).
        logfire_read_token=(
            source.get("LOGFIRE_READ_TOKEN") or source.get("LOGFIRE_TOKEN") or source.get("LOGFIRE_API_KEY") or ""
        ).strip(),
        logfire_mcp_url=source.get("LOGFIRE_MCP_URL", "").strip() or "https://logfire-us.pydantic.dev/mcp",
        neon_read_token=source.get("NEON_READ_TOKEN", "").strip(),
        neon_mcp_url=source.get("NEON_MCP_URL", "").strip(),
        logs_channel_id=_read_optional_int(source.get("DISCORD_CHANNEL_ID")),
        alpaca_api_key=source.get("ALPACA_API_KEY", "").strip(),
        alpaca_api_secret=source.get("ALPACA_API_SECRET", "").strip(),
        giphy_api_key=source.get("GIPHY_API_KEY", "").strip(),
    )


def get_system_prompt(prompt_path: str = "system_prompt.txt") -> str:
    """Read the system prompt file, replacing ``[[CURRENT_DATE_AND_TIME]]`` with today's date."""
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content:
                # The date only: the system prompt is cached, and a clock time would change it on
                # every request. The model gets the exact time from the mention's timestamp.
                today = datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d (%A)")
                return content.replace("[[CURRENT_DATE_AND_TIME]]", today)
    except FileNotFoundError:
        if prompt_path not in _missing_prompt_files_warned:
            _missing_prompt_files_warned.add(prompt_path)
            logger.warning(
                "System prompt file '%s' not found; falling back to default system prompt.", prompt_path)
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("Couldn't read system prompt file '%s', using the default: %s", prompt_path, e)

    return DEFAULT_SYSTEM_PROMPT


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


def _read_private_json(path: str) -> Dict[str, Any]:
    """An untracked JSON file keyed by Discord user ID, or ``{}`` when it's missing or unreadable.

    Read on every mention, so edits apply without a restart.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        logger.warning("Couldn't read '%s': %s", path, e)
        return {}
    if not isinstance(data, dict):
        logger.warning("'%s' should be a JSON object keyed by user ID; ignoring it.", path)
        return {}
    return data


def get_owner_note(user_id: int, notes_path: str = "owner_notes.json") -> str:
    """The owner's private note for mentions from ``user_id``, or "" when there is none.

    ``owner_notes.json`` sits beside ``system_prompt.txt`` (untracked, like it) and maps a
    Discord user ID to text.
    """
    note = _read_private_json(notes_path).get(str(user_id))
    return note.strip() if isinstance(note, str) else ""


# Longest private status line kept; a longer one is cut.
MAX_STATUS_LINE_CHARS = 150


def _status_line(value: Any) -> str:
    """A private status line made fit to show: one line, ending in ``...`` like every status."""
    line = " ".join(str(value).split()).rstrip(".")[:MAX_STATUS_LINE_CHARS].rstrip()
    return f"{line}..." if line else ""


def get_user_status_lines(user_id: int, lines_path: str = "status_lines.json") -> Dict[str, List[str]]:
    """The owner's private status lines for mentions from ``user_id``, by step, or ``{}``.

    ``status_lines.json`` sits beside ``owner_notes.json`` (untracked, like it), so a running joke
    about one person isn't in the repo. It maps a Discord user ID to either a list of lines that
    fit any step, or an object of step name (``"thinking..."``, or ``"*"`` for any step) to lines::

        {"123": ["wondering what he's broken now"],
         "456": {"*": ["bracing"], "drawing...": ["drawing it, against my better judgment"]}}
    """
    entry = _read_private_json(lines_path).get(str(user_id))
    if isinstance(entry, list):
        entry = {"*": entry}
    if not isinstance(entry, dict):
        return {}
    result: Dict[str, List[str]] = {}
    for step, lines in entry.items():
        if isinstance(lines, str):
            lines = [lines]
        if not isinstance(lines, list):
            continue
        kept = [line for line in map(_status_line, lines) if line]
        if kept:
            result[str(step)] = kept
    return result
