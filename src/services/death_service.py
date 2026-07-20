"""
Death Service — Monitor notable deaths via Wikipedia.

Polls the Wikipedia "Deaths in <YEAR>" page every 15 seconds, detects newly-added
names, checks their average monthly pageviews, and announces notable deaths
(≥ MIN_AVG_MONTHLY_VIEWS) to a configured Discord channel.

State (the set of known names, plus the set of names that have already been
announced) is persisted to a temp file on shutdown and reloaded on startup so
deaths that occur while the bot is offline are still detected. Once a name has
been announced it is never announced again, even if it drops off the deaths
page and later reappears.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional, Set, Tuple, Any
from urllib.parse import unquote, urlparse

import aiohttp
import discord
from bs4 import BeautifulSoup
from discord.ext import commands

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POLL_INTERVAL_SECONDS = 15

# Minimum average monthly pageviews to qualify for an announcement
MIN_AVG_MONTHLY_VIEWS = 40_000

# How many months of pageview data to average
PAGEVIEW_MONTHS = 3

# User-Agent per Wikimedia policy
USER_AGENT = "cmwgpt-bot/1.0 (jobeus@gmail.com)"

# Temp file for persisting known-names across restarts
STATE_FILE = ".cache/cmwgpt_death_names.json"


def parse_deaths_html(html: str) -> list[Tuple[str, str]]:
    """Return a list of (display_name, article_title) from deaths-page HTML."""
    results = []
    soup = BeautifulSoup(html, "html.parser")

    # Restrict to list items within the main content area to avoid nav menus
    for li in soup.find_all("li"):
        # The primary link is usually the first anchor tag
        a_tag = li.find("a")
        if not a_tag:
            continue

        href = a_tag.get("href", "")
        # Only process internal wiki links that are not categories, files, or anchors
        if (href.startswith("/wiki/") or href.startswith("./")) and ":" not in href and "#" not in href:
            title = href.split("/wiki/", 1)[1] if href.startswith("/wiki/") else href.split("./", 1)[1]
            title = unquote(title)
            name = a_tag.get_text(strip=True)

            # Check if there is an age-like pattern (comma space digits) to filter out nav links
            desc = li.get_text(strip=True)
            if name and re.search(r',\s*\d+[\s,]', desc):
                results.append((name, title))

    return results


def parse_article_title(target: str) -> str:
    """Extract a Wikipedia article title from a URL or bare title.

    Accepts a full ``https://en.wikipedia.org/wiki/...`` URL or a plain title
    like ``Kevin Keegan``. Returns the underscore-joined article title, or an
    empty string if nothing usable could be parsed.
    """
    if not target:
        return ""
    target = target.strip()

    if "wikipedia.org/wiki/" in target:
        parsed = urlparse(target)
        path_parts = parsed.path.split("/wiki/")
        if len(path_parts) > 1:
            return unquote(path_parts[1]).replace(" ", "_")
        return ""

    title = target.replace(" ", "_")
    # Wikipedia capitalises the first letter of article titles.
    if title:
        title = title[0].upper() + title[1:]
    return title


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class DeathService:
    """Background service that monitors Wikipedia for notable deaths."""

    def __init__(
        self,
        *,
        state_service,
        death_channel_id: str,
        state_file: str = STATE_FILE,
        openai_service=None,
        model: Optional[str] = None,
    ):
        self._state_service = state_service
        self._death_channel_id = death_channel_id
        self._state_file = state_file
        self._openai_service = openai_service
        self._model = model
        self._bot: Optional[commands.Bot] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._known_names: Set[Tuple[str, str]] = set()
        # Article titles that have already been announced. Persisted so a name
        # is never announced twice, even across restarts or if it disappears
        # from the deaths page and later reappears.
        self._announced_titles: Set[str] = set()
        self._session: Optional[aiohttp.ClientSession] = None
        self._first_poll = True

        # Try to load persisted state
        self._load_state()

    def _get_setting(self, key: str, default: Any) -> Any:
        """Helper to get a global death setting or default."""
        settings = self._state_service.get_death_settings()
        if settings and key in settings:
            return settings[key]
        return default

    # -- lifecycle ----------------------------------------------------------

    def set_bot(self, bot: commands.Bot) -> None:
        self._bot = bot

    def start(self) -> None:
        """Start the polling background task."""
        if not self._death_channel_id:
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
        """Stop the polling task, close the HTTP session, and persist state."""
        if self._running:
            self._running = False
            if self._task and not self._task.done():
                self._task.cancel()
            self._save_state()
            logger.info("DeathService stopped")
        # Close the session even if the poll loop never ran (e.g. when
        # DEATH_CHANNEL_ID is unset but /wikicount created a session).
        self._close_session()

    def _close_session(self) -> None:
        """Close the shared HTTP session if one was created."""
        session = self._session
        if session is None or session.closed:
            return
        self._session = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            loop.create_task(session.close())
        else:
            asyncio.run(session.close())

    # -- state persistence --------------------------------------------------

    def _save_state(self) -> None:
        """Persist current known names and announced titles to disk."""
        if not self._state_file:
            return
        try:
            os.makedirs(os.path.dirname(self._state_file) or ".", exist_ok=True)
            data = {
                "known": [list(t) for t in self._known_names],
                "announced": sorted(self._announced_titles),
            }
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
            logger.debug(
                f"Saved {len(self._known_names)} known / {len(self._announced_titles)} "
                f"announced death names to {self._state_file}"
            )
        except (OSError, ValueError) as exc:
            logger.error(f"Failed to save death state: {exc}")

    def _load_state(self) -> None:
        """Load known names and announced titles from a previous session, if available."""
        if not self._state_file or not os.path.exists(self._state_file):
            return
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._known_names = {tuple(item) for item in data.get("known", [])}
                self._announced_titles = set(data.get("announced", []))
            else:
                # Legacy format: a bare list of [name, title] pairs with no
                # announced-set. Treat every previously-known name as already
                # announced so we don't re-post historic deaths on upgrade.
                self._known_names = {tuple(item) for item in data}
                self._announced_titles = {t[1] for t in self._known_names if len(t) > 1}
            logger.info(
                f"Loaded {len(self._known_names)} known / {len(self._announced_titles)} "
                f"announced death names from {self._state_file}"
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.error(f"Failed to load death state: {exc}")

    # -- main loop ----------------------------------------------------------

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Api-User-Agent": USER_AGENT, "User-Agent": USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=30, sock_connect=10),
            )
        return self._session

    async def get_session(self) -> aiohttp.ClientSession:
        """Public accessor: get or create the shared HTTP session.

        Used by callers outside this service (e.g. /wikicount) so they don't
        have to reach into private state. The session is closed by ``stop()``
        even if the poll loop never ran.
        """
        return await self._get_session()

    async def _poll_loop(self) -> None:
        """Fetch the deaths page every POLL_INTERVAL_SECONDS."""
        try:
            while self._running:
                try:
                    await self._poll_once()
                except asyncio.CancelledError:
                    raise
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    logger.warning(f"Network error during death-page poll: {exc}")
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

        # Persist immediately so restarts don't lose track of new names
        self._save_state()

        logger.info(f"Detected {len(new_names)} new name(s) on deaths page")

        announced_any = False
        for display_name, article_title in new_names:
            try:
                # Never announce the same person twice, even if they dropped off
                # the deaths page and reappeared.
                if article_title in self._announced_titles:
                    logger.debug(
                        f"Already announced {display_name} ({article_title}) — skipping"
                    )
                    continue

                min_views = self._get_setting("min_views", MIN_AVG_MONTHLY_VIEWS)
                avg_views = await self.get_avg_monthly_views(article_title, session)

                if avg_views is None:
                    continue

                if avg_views >= min_views:
                    logger.info(
                        f"Met threshold: {display_name} ({avg_views:,} avg monthly views) -> ANNOUNCING"
                    )
                    await self._announce(display_name, article_title, avg_views)
                    self._announced_titles.add(article_title)
                    announced_any = True
                else:
                    logger.info(
                        f"Skipped: {display_name} ({avg_views:,} avg monthly views, below {min_views:,} threshold)"
                    )
            except Exception:
                logger.exception(f"Error checking views for {display_name}")

        # Persist the updated announced-set so restarts don't re-post.
        if announced_any:
            self._save_state()

    # -- pageviews ----------------------------------------------------------

    async def get_avg_monthly_views(
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

        # Wikimedia API requires YYYYMMDD00 format
        start = f"{start_year}{start_month:02d}0100"
        end = f"{end_year}{end_month:02d}0100"

        # Properly URL-encode the title (e.g., handling accented characters)
        from urllib.parse import quote
        safe_title = article_title.replace(" ", "_")
        encoded_title = quote(safe_title, safe='')

        url = (
            f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
            f"en.wikipedia/all-access/all-agents/{encoded_title}/monthly/{start}/{end}"
        )

        max_retries = 3
        base_delay = 1.0

        for attempt in range(max_retries):
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("items", [])
                    if not items:
                        return 0
                    total = sum(item.get("views", 0) for item in items)
                    return total // len(items)

                if resp.status == 404:
                    return 0  # No data / page doesn't exist

                if resp.status == 429:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"Rate limited (429) on {article_title}, retrying in {delay}s..."
                    )
                    await asyncio.sleep(delay)
                    continue

                text = await resp.text()
                logger.warning(
                    f"Pageviews API returned {resp.status} for {article_title}: {text}"
                )
                return None

        logger.error(f"Failed to fetch pageviews for {article_title} after {max_retries} attempts.")
        return None

    # -- announcement -------------------------------------------------------

    async def _announce(
        self, display_name: str, article_title: str, avg_views: int
    ) -> None:
        """Send a death announcement to the configured channel."""
        if not self._bot:
            logger.error("Bot not set — cannot announce death")
            return

        channel = self._bot.get_channel(int(self._death_channel_id))
        if channel is None:
            logger.error(f"Could not find channel {self._death_channel_id}")
            return

        wiki_link = (
            f"https://en.wikipedia.org/wiki/{article_title.replace(' ', '_')}"
        )
        message = f"RIP {display_name} - {wiki_link}"

        logger.info(f"Announcing death: {message} ({avg_views:,} avg monthly views)")

        # Post the RIP line with the Wikipedia URL as-is first, so Discord can
        # unfurl the link embed, then follow up with the AI summary as a
        # separate message.
        try:
            await channel.send(message)
        except discord.HTTPException as exc:
            logger.error(f"Failed to send death announcement: {exc}")
            return

        summary = await self._summarize_person(display_name, wiki_link)
        if not summary:
            return

        try:
            await channel.send(summary)
        except discord.HTTPException as exc:
            logger.error(f"Failed to send death summary follow-up: {exc}")

    async def _summarize_person(
        self, display_name: str, wiki_link: str
    ) -> Optional[str]:
        """Ask the chat model for a limerick about the deceased.

        Uses the same model the mention handler uses. Best-effort: any failure
        (or an unconfigured model/service) just returns None and the plain RIP
        message is sent.
        """
        if not self._openai_service or not self._model:
            return None

        prompt = (
            f"Write a funny limerick about {display_name} ({wiki_link}). "
            f"Respond with only the limerick."
        )
        try:
            summary, _cost = await self._openai_service.get_chat_completion(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
            )
            summary = (summary or "").strip()
            return summary or None
        except Exception:
            logger.exception(f"Failed to summarize {display_name} for death announcement")
            return None

    async def force_announce(self, target: str) -> Tuple[bool, str]:
        """Force a death announcement for a Wikipedia URL/title (for testing).

        Bypasses the newness diff and the pageview threshold, and does NOT
        touch the announced-set, so it can be run repeatedly and never
        interferes with the real dedup state. Returns ``(ok, message)``.
        """
        if not self._death_channel_id:
            return False, "DEATH_CHANNEL_ID is not configured — no channel to announce to."

        article_title = parse_article_title(target)
        if not article_title:
            return False, "Could not determine a Wikipedia article title from that input."

        display_name = article_title.replace("_", " ")

        # Fetch real pageviews purely for a realistic log line; the threshold is
        # intentionally not enforced here.
        avg_views = 0
        try:
            session = await self._get_session()
            avg_views = await self.get_avg_monthly_views(article_title, session) or 0
        except Exception:
            logger.exception(f"force_announce: failed to fetch pageviews for {article_title}")

        logger.info(f"[force_announce] Announcing {display_name} ({avg_views:,} avg monthly views)")
        await self._announce(display_name, article_title, avg_views)
        return True, f"Forced death announcement for **{display_name}** in the death channel."
