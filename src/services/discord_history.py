"""
Discord History - Searches and reads a server's Discord messages for the chat model's tools.

Search uses Discord's Search Guild Messages endpoint (open to bots since March 2026),
which discord.py doesn't wrap yet, so it's called through the client's raw HTTP route.
Both search and reads are limited to channels the asker and the bot can both read, so the
tools never show someone messages from a channel they can't see.
"""

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, time, tzinfo
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord

from src.utils.message_utils import format_discord_timestamp

logger = logging.getLogger(__name__)

SEARCH_PAGE_SIZE = 25
# Discord's limit on ``offset``.
MAX_SEARCH_OFFSET = 9975
# Discord's limit on ``channel_id`` filters per search.
MAX_SEARCH_CHANNELS = 500
MAX_READ_MESSAGES = 50
# Longest message text shown; walls of text get cut.
MAX_CONTENT_CHARS = 600
# ``code`` in Discord's 202 reply when the server's messages aren't indexed yet.
INDEX_NOT_READY_CODE = 110000

# Discord's limit on most list filters (authors, mentions, hostnames, ...).
MAX_FILTER_VALUES = 100
MAX_SLOP = 100

# ``snapshot`` is a forwarded message. Both lists take a leading ``-`` to exclude instead.
HAS_FILTERS = ("image", "sound", "video", "file", "sticker", "embed", "link", "poll", "snapshot")
AUTHOR_TYPES = ("user", "bot", "webhook")
EMBED_TYPES = ("image", "video", "gif", "sound", "article")
SORTS = {"newest": ("timestamp", "desc"), "oldest": ("timestamp", "asc"), "relevance": ("relevance", None)}


def _local_tz() -> Optional[tzinfo]:
    """The timezone the history headers use (``TZ``, else the system's)."""
    tz_name = os.environ.get("TZ", "").strip()
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            pass
    return datetime.now().astimezone().tzinfo


def day_snowflake(day: date) -> int:
    """The snowflake at local midnight starting ``day``."""
    return discord.utils.time_snowflake(datetime.combine(day, time(), tzinfo=_local_tz()))


@dataclass(frozen=True)
class SearchFilters:
    """What a search matches; empty fields don't filter. ``after``/``before`` take a day or a message ID."""
    query: str = ""
    # Most words allowed between the query's words (Discord's default is 2; 0 keeps them together).
    slop: Optional[int] = None
    author_ids: Sequence[int] = ()
    author_types: Sequence[str] = ()
    mentions: Sequence[int] = ()
    mention_role_ids: Sequence[int] = ()
    mention_everyone: Optional[bool] = None
    replied_to_user_ids: Sequence[int] = ()
    replied_to_message_ids: Sequence[int] = ()
    pinned: Optional[bool] = None
    has: Sequence[str] = ()
    embed_types: Sequence[str] = ()
    embed_providers: Sequence[str] = ()
    link_hostnames: Sequence[str] = ()
    attachment_filenames: Sequence[str] = ()
    attachment_extensions: Sequence[str] = ()
    after: Optional[Union[date, int]] = None
    before: Optional[Union[date, int]] = None
    include_nsfw: bool = False

    def params(self) -> List[Tuple[str, Any]]:
        """The filters as Discord search query parameters (everything but channels, sort and paging)."""
        params: List[Tuple[str, Any]] = []

        def many(key: str, values: Sequence[Any], allowed: Sequence[str] = (), limit: int = 256) -> None:
            for value in list(values)[:MAX_FILTER_VALUES]:
                value = str(value).strip()[:limit]
                if value and (not allowed or value.lstrip("-") in allowed):
                    params.append((key, value))

        def flag(key: str, value: Optional[bool]) -> None:
            if value is not None:
                params.append((key, "true" if value else "false"))

        if self.query:
            params.append(("content", self.query[:1024]))
        if self.slop is not None:
            params.append(("slop", max(0, min(self.slop, MAX_SLOP))))
        many("author_id", self.author_ids)
        many("author_type", self.author_types, AUTHOR_TYPES)
        many("mentions", self.mentions)
        many("mentions_role_id", self.mention_role_ids)
        flag("mention_everyone", self.mention_everyone)
        many("replied_to_user_id", self.replied_to_user_ids)
        many("replied_to_message_id", self.replied_to_message_ids)
        flag("pinned", self.pinned)
        many("has", self.has, HAS_FILTERS)
        many("embed_type", self.embed_types, EMBED_TYPES)
        many("embed_provider", self.embed_providers)
        many("link_hostname", self.link_hostnames)
        many("attachment_filename", self.attachment_filenames, limit=1024)
        many("attachment_extension", [e.lstrip(".") for e in self.attachment_extensions])
        if self.after is not None:
            params.append(("min_id", str(_snowflake(self.after))))
        if self.before is not None:
            params.append(("max_id", str(_snowflake(self.before))))
        flag("include_nsfw", self.include_nsfw or None)
        return params


def _snowflake(value: Union[date, int]) -> int:
    return value if isinstance(value, int) else day_snowflake(value)


def readable_channels(guild: discord.Guild, member: Any) -> List[discord.TextChannel]:
    """Text channels whose history both ``member`` and the bot can read."""
    channels = []
    for channel in guild.text_channels:
        mine = channel.permissions_for(guild.me)
        theirs = channel.permissions_for(member)
        if mine.view_channel and mine.read_message_history and theirs.view_channel and theirs.read_message_history:
            channels.append(channel)
    return channels


def _cut(text: str) -> str:
    text = " ".join(text.split())
    return text[:MAX_CONTENT_CHARS] + "…" if len(text) > MAX_CONTENT_CHARS else text


def _format_line(
    when: datetime, message_id: Any, channel_name: str, author_id: Any, author_name: str,
    content: str, extras: Sequence[str],
) -> str:
    body = _cut(content) or "(no text)"
    if extras:
        body += " | " + "; ".join(extras)
    return f"[{format_discord_timestamp(when)}] [{message_id}] #{channel_name} <@{author_id}> ({author_name}): {body}"


def format_raw_message(message: Dict[str, Any], guild: discord.Guild) -> str:
    """One search hit (a raw message object) as a history-style line."""
    author = message.get("author") or {}
    channel = guild.get_channel(int(message.get("channel_id") or 0))
    extras = []
    reference = (message.get("message_reference") or {}).get("message_id")
    if reference and message.get("type") == 19:
        extras.append(f"reply to [{reference}]")
    if message.get("pinned"):
        extras.append("pinned")
    extras += [f"attachment: {a.get('filename')}" for a in message.get("attachments") or []]
    for embed in message.get("embeds") or []:
        label = embed.get("title") or embed.get("url") or (embed.get("provider") or {}).get("name")
        if label:
            extras.append(f"embed: {_cut(str(label))}")
    return _format_line(
        datetime.fromisoformat(message["timestamp"]),
        message.get("id"),
        getattr(channel, "name", "unknown-channel"),
        author.get("id"),
        author.get("global_name") or author.get("username") or "unknown",
        message.get("content") or "",
        extras,
    )


def format_message(message: discord.Message) -> str:
    """A fetched message as a history-style line."""
    extras = [f"attachment: {a.filename}" for a in message.attachments]
    extras += [f"embed: {_cut(e.title or e.url or '')}" for e in message.embeds if e.title or e.url]
    return _format_line(
        message.created_at,
        message.id,
        getattr(message.channel, "name", "unknown-channel"),
        message.author.id,
        getattr(message.author, "display_name", str(message.author)),
        message.content,
        extras,
    )


async def search_messages(
    guild: discord.Guild,
    channels: Sequence[discord.abc.Snowflake],
    filters: SearchFilters,
    *,
    sort: str = "newest",
    skip: int = 0,
) -> str:
    """Search ``channels`` and return the hits as history-style lines under a header."""
    if not channels:
        return "There are no channels you and the bot can both read."
    skip = max(0, min(skip, MAX_SEARCH_OFFSET))
    sort_by, sort_order = SORTS.get(sort, SORTS["newest"])
    params: List[Tuple[str, Any]] = [("limit", SEARCH_PAGE_SIZE), ("offset", skip), ("sort_by", sort_by)]
    if sort_order:
        params.append(("sort_order", sort_order))
    params += [("channel_id", str(c.id)) for c in channels[:MAX_SEARCH_CHANNELS]]
    params += filters.params()

    route = discord.http.Route("GET", "/guilds/{guild_id}/messages/search", guild_id=guild.id)
    # ``_state.http`` is discord.py's authenticated client; it has no wrapper for this endpoint.
    data = await guild._state.http.request(route, params=params)
    if data.get("code") == INDEX_NOT_READY_CODE:
        return "Discord is still indexing this server's messages, so search isn't available yet. Try later."

    hits = [group[0] for group in data.get("messages") or [] if group]
    total = data.get("total_results") or 0
    if not hits:
        return "No messages matched." + (f" (Skipped the first {skip}.)" if skip else "")
    shown_to = skip + len(hits)
    header = f"{total} matching messages; showing {skip + 1}-{shown_to}, sorted by {sort}."
    if shown_to < total:
        header += f" Use skip={shown_to} for more, or narrow the search."
    if data.get("doing_deep_historical_index"):
        header += " Discord is still indexing older messages, so some may be missing."
    return header + "\n" + "\n".join(format_raw_message(m, guild) for m in hits)


async def read_around(channel: discord.TextChannel, message_id: int, count: int = 20) -> str:
    """Up to ``count`` messages around ``message_id`` in ``channel``, oldest first."""
    count = max(1, min(count, MAX_READ_MESSAGES))
    messages = [m async for m in channel.history(around=discord.Object(id=message_id), limit=count)]
    if not messages:
        return f"No messages found around {message_id} in #{channel.name}."
    messages.sort(key=lambda m: m.id)
    lines = [("→ " if m.id == message_id else "") + format_message(m) for m in messages]
    return f"{len(messages)} messages around {message_id} in #{channel.name}, oldest first:\n" + "\n".join(lines)
