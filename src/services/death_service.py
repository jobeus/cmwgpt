"""
Death Service — Monitor notable deaths via Wikipedia.

Polls the Wikipedia "Deaths in <YEAR>" page every 15 seconds, detects newly-added
names, checks their average monthly pageviews, and announces notable deaths
(≥ MIN_AVG_MONTHLY_VIEWS) to a configured Discord channel.

State (the set of known names) is persisted to a temp file on shutdown and
reloaded on startup so deaths that occur while the bot is offline are still
detected.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Optional, Set, Tuple, Any
from urllib.parse import unquote

import aiohttp
import discord
from discord.ext import commands

from src.config import DEATH_CHANNEL_ID
from src.services.state_service import state_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POLL_INTERVAL_SECONDS = 15

# Minimum average monthly pageviews to qualify for an announcement
MIN_AVG_MONTHLY_VIEWS = 180_000

# How many months of pageview data to average
PAGEVIEW_MONTHS = 12

# User-Agent per Wikimedia policy
USER_AGENT = "cmwgpt-bot/1.0 (jobeus@gmail.com)"

# Temp file for persisting known-names across restarts
STATE_FILE = "/tmp/cmwgpt_death_names.json"


# ---------------------------------------------------------------------------
# HTML parser for the deaths page
# ---------------------------------------------------------------------------

class _DeathPageParser(HTMLParser):
    """
    Extract (display_name, article_title) tuples from the Deaths-in-YYYY page.

    The page structure has <li> items where the first <a> link is the person.
    We collect the first <a href="/wiki/..."> inside each <li>.
    """

    def __init__(self):
        super().__init__()
        self._in_li = 0  # nesting depth
        self._found_link_in_li = False
        self._current_href: Optional[str] = None
        self._current_text_parts: list[str] = []
        self._collecting_text = False
        self.results: list[Tuple[str, str]] = []

    # ---- handler methods ----

    def handle_starttag(self, tag: str, attrs):
        if tag == "li":
            self._in_li += 1
            self._found_link_in_li = False

        if tag == "a" and self._in_li > 0 and not self._found_link_in_li:
            href = dict(attrs).get("href", "")
            # Only care about internal wiki links (not external, not anchors)
            if href.startswith("/wiki/") and ":" not in href.split("/wiki/", 1)[1]:
                self._found_link_in_li = True
                # Article title is the path after /wiki/
                self._current_href = unquote(href.split("/wiki/", 1)[1])
                self._current_text_parts = []
                self._collecting_text = True

    def handle_endtag(self, tag: str):
        if tag == "a" and self._collecting_text:
            self._collecting_text = False
            name = "".join(self._current_text_parts).strip()
            if name and self._current_href:
                self.results.append((name, self._current_href))
            self._current_href = None
            self._current_text_parts = []

        if tag == "li" and self._in_li > 0:
            self._in_li -= 1

    def handle_data(self, data: str):
        if self._collecting_text:
            self._current_text_parts.append(data)


def parse_deaths_html(html: str) -> list[Tuple[str, str]]:
    """Return a list of (display_name, article_title) from deaths-page HTML."""
    parser = _DeathPageParser()
    parser.feed(html)
    return parser.results


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class DeathService:
    """Background service that monitors Wikipedia for notable deaths."""

    def __init__(self):
        self._bot: Optional[commands.Bot] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._known_names: Set[Tuple[str, str]] = set()
        self._session: Optional[aiohttp.ClientSession] = None
        self._first_poll = True

        # Try to load persisted state
        self._load_state()

    def _get_setting(self, key: str, default: Any) -> Any:
        """Helper to get a global death setting or default."""
        settings = state_service.get_death_settings()
        if settings and key in settings:
            return settings[key]
        return default

    # -- lifecycle ----------------------------------------------------------

    def set_bot(self, bot: commands.Bot) -> None:
        self._bot = bot

    def start(self) -> None:
        """Start the polling background task."""
        if not DEATH_CHANNEL_ID:
            logger.info("DEATH_CHANNEL_ID not set — death service disabled")
            return
        if self._running:
            logger.warning("DeathService is already running")
            return
        self._running = True

        # If we loaded state from disk, skip the first-poll baseline
        if self._known_names:
            self._first_poll = False

        self._task = asyncio.create_task(self._poll_loop())
        logger.info("DeathService started")

    def stop(self) -> None:
        """Stop the polling task and persist state."""
        if not self._running:
            return
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._save_state()
        logger.info("DeathService stopped")

    # -- state persistence --------------------------------------------------

    def _save_state(self) -> None:
        """Persist current known names to disk."""
        try:
            data = [list(t) for t in self._known_names]
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
            logger.debug(f"Saved {len(data)} known death names to {STATE_FILE}")
        except (OSError, ValueError) as exc:
            logger.error(f"Failed to save death state: {exc}")

    def _load_state(self) -> None:
        """Load known names from a previous session, if available."""
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._known_names = {tuple(item) for item in data}
            logger.info(
                f"Loaded {len(self._known_names)} known death names from {STATE_FILE}"
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.error(f"Failed to load death state: {exc}")

    # -- main loop ----------------------------------------------------------

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Api-User-Agent": USER_AGENT, "User-Agent": USER_AGENT}
            )
        return self._session

    async def _poll_loop(self) -> None:
        """Fetch the deaths page every POLL_INTERVAL_SECONDS."""
        try:
            while self._running:
                try:
                    await self._poll_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Error during death-page poll")
                
                interval = self._get_setting("interval", POLL_INTERVAL_SECONDS)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
        finally:
            if self._session and not self._session.closed:
                await self._session.close()

    async def _poll_once(self) -> None:
        """Single poll iteration."""
        year = datetime.now(timezone.utc).year
        url = f"https://en.wikipedia.org/api/rest_v1/page/html/Deaths_in_{year}"

        session = await self._get_session()
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.warning(f"Deaths page returned {resp.status}")
                return
            html = await resp.text()

        current = set(parse_deaths_html(html))

        if self._first_poll:
            # Baseline — don't announce anything
            self._known_names = current
            self._first_poll = False
            logger.info(
                f"Death service baseline: {len(current)} names for {year}"
            )
            return

        new_names = current - self._known_names
        self._known_names = current

        if not new_names:
            return

        logger.info(f"Detected {len(new_names)} new name(s) on deaths page")

        for display_name, article_title in new_names:
            try:
                min_views = self._get_setting("min_views", MIN_AVG_MONTHLY_VIEWS)
                avg_views = await self._get_avg_monthly_views(article_title, session)
                if avg_views is not None and avg_views >= min_views:
                    await self._announce(display_name, article_title, avg_views)
                else:
                    logger.debug(
                        f"Skipping {display_name}: "
                        f"{avg_views or 0:,} avg monthly views"
                    )
            except Exception:
                logger.exception(f"Error checking views for {display_name}")

    # -- pageviews ----------------------------------------------------------

    async def _get_avg_monthly_views(
        self, article_title: str, session: aiohttp.ClientSession
    ) -> Optional[int]:
        """
        Return the average monthly pageviews for *article_title* over the
        last PAGEVIEW_MONTHS months, or None on failure.
        """
        now = datetime.now(timezone.utc)
        months = self._get_setting("pageview_months", PAGEVIEW_MONTHS)
        
        # End = last full month
        end_year = now.year
        end_month = now.month - 1
        if end_month < 1:
            end_month = 12
            end_year -= 1

        # Start = months before end
        start_month = end_month - months + 1
        start_year = end_year
        while start_month < 1:
            start_month += 12
            start_year -= 1

        start = f"{start_year}{start_month:02d}01"
        end = f"{end_year}{end_month:02d}01"

        # Percent-encode the title (spaces → underscores is fine for this API)
        safe_title = article_title.replace(" ", "_")

        url = (
            f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
            f"en.wikipedia/all-access/all-agents/{safe_title}/monthly/{start}/{end}"
        )

        async with session.get(url) as resp:
            if resp.status != 200:
                logger.warning(
                    f"Pageviews API returned {resp.status} for {article_title}"
                )
                return None
            data = await resp.json()

        items = data.get("items", [])
        if not items:
            return None

        total = sum(item.get("views", 0) for item in items)
        return total // len(items)

    # -- announcement -------------------------------------------------------

    async def _announce(
        self, display_name: str, article_title: str, avg_views: int
    ) -> None:
        """Send a death announcement to the configured channel."""
        if not self._bot:
            logger.error("Bot not set — cannot announce death")
            return

        channel = self._bot.get_channel(int(DEATH_CHANNEL_ID))
        if channel is None:
            logger.error(f"Could not find channel {DEATH_CHANNEL_ID}")
            return

        wiki_link = (
            f"https://en.wikipedia.org/wiki/{article_title.replace(' ', '_')}"
        )
        message = f"RIP {display_name} - {wiki_link}"
        logger.info(f"Announcing death: {message} ({avg_views:,} avg monthly views)")

        try:
            await channel.send(message)
        except discord.HTTPException as exc:
            logger.error(f"Failed to send death announcement: {exc}")


# Global singleton
death_service = DeathService()
