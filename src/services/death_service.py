"""
Death Service — Monitor notable deaths via Wikipedia.

Polls the Wikipedia "Deaths in <YEAR>" page every 15 seconds, detects newly-added
names, checks their average monthly pageviews, and announces notable deaths
(≥ MIN_AVG_MONTHLY_VIEWS) to a configured Discord channel. Before announcing,
Wikidata must confirm the article is about a human, so vandal entries such as
a football club added to the page are skipped.

State (the set of known names, plus the set of names that have already been
announced) is persisted to a temp file on shutdown and reloaded on startup so
deaths that occur while the bot is offline are still detected. Once a name has
been announced it is never announced again, even if it drops off the deaths
page and later reappears. A name whose pageview lookup fails is never recorded
as seen, so the next poll picks it up and tries again.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, List, Optional, Set, Tuple
from urllib.parse import unquote

import httpx
import discord
from bs4 import BeautifulSoup
from discord.ext import commands

from src.db.state_store import StateStore
from src.services.completion_dispatch import dispatch_completion
from src.telemetry import routine_span
from src.utils.url_utils import get_article_text, wiki_title_candidates

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

# How much of the article goes into a limerick prompt. The lead and career come first, and this
# keeps a huge article from costing much.
MAX_LIMERICK_ARTICLE_CHARS = 12_000
# What the model answers instead of a limerick when it can't write one.
NO_LIMERICK = "NO_LIMERICK"

# Wikidata "instance of" property and the "human" item it must point at.
WIKIDATA_INSTANCE_OF = "P31"
WIKIDATA_HUMAN = "Q5"

# Temp file for persisting known-names across restarts
# Known names and announced titles, in Redis (cmwgpt:death:names).
STATE_KEY = "death:names"
# The JSON file they lived in before Redis, imported once.
LEGACY_STATE_FILE = ".cache/cmwgpt_death_names.json"


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
        store: Optional[StateStore] = None,
        legacy_state_file: str = LEGACY_STATE_FILE,
        model: Optional[str] = None,
        gemini_service=None,
        anthropic_service=None,
    ):
        self._state_service = state_service
        self._death_channel_id = death_channel_id
        self._store = store or StateStore(None)
        self._legacy_state_file = legacy_state_file
        self._gemini_service = gemini_service
        self._anthropic_service = anthropic_service
        self._model = model
        self._bot: Optional[commands.Bot] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._known_names: Set[Tuple[str, str]] = set()
        # Article titles that have already been announced. Persisted so a name
        # is never announced twice, even across restarts or if it disappears
        # from the deaths page and later reappears.
        self._announced_titles: Set[str] = set()
        self._http: Optional[httpx.AsyncClient] = None
        # Validator for the deaths page, so an unchanged page isn't re-parsed.
        self._deaths_page_etag: Optional[str] = None
        self._first_poll = True

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

        # If we loaded saved state, skip the first-poll baseline
        if self._known_names:
            self._first_poll = False

        self._task = asyncio.create_task(self._poll_loop())
        logger.info("DeathService started")

    def stop(self) -> None:
        """Stop the polling task and persist state."""
        if self._running:
            self._running = False
            if self._task and not self._task.done():
                self._task.cancel()
            self._save_state()
            logger.info("DeathService stopped")

    async def close(self) -> None:
        """Stop polling and close the HTTP client."""
        self.stop()
        http, self._http = self._http, None
        if http is not None:
            await http.aclose()

    # -- state persistence --------------------------------------------------

    def _save_state(self) -> None:
        """Save the known names and announced titles to Redis."""
        data = {
            "known": sorted(list(t) for t in self._known_names),
            "announced": sorted(self._announced_titles),
        }
        if self._store.set(STATE_KEY, data):
            logger.debug(
                "Saved %s known / %s announced death names", len(self._known_names), len(self._announced_titles))

    def _load_state(self) -> None:
        """Load known names and announced titles from a previous run, if any."""
        data = self._store.import_legacy_file(self._legacy_state_file, STATE_KEY)
        imported = data is not None
        if not imported:
            data = self._store.get(STATE_KEY)
        if data is None:
            return
        try:
            if isinstance(data, dict):
                self._known_names = {tuple(item) for item in data.get("known", [])}
                self._announced_titles = set(data.get("announced", []))
            else:
                # Legacy format: a bare list of [name, title] pairs with no
                # announced-set. Treat every previously-known name as already
                # announced so we don't re-post historic deaths on upgrade.
                self._known_names = {tuple(item) for item in data}
                self._announced_titles = {t[1] for t in self._known_names if len(t) > 1}
        except (TypeError, ValueError) as exc:
            logger.error("Ignoring unreadable saved death names: %s", exc)
            return
        if imported:
            self._save_state()
        logger.info(
            "Loaded %s known / %s announced death names", len(self._known_names), len(self._announced_titles))

    # -- main loop ----------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                headers={"Api-User-Agent": USER_AGENT, "User-Agent": USER_AGENT},
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        return self._http

    async def _poll_loop(self) -> None:
        """Fetch the deaths page every POLL_INTERVAL_SECONDS."""
        try:
            while self._running:
                try:
                    with routine_span("death page poll"):
                        await self._poll_once()
                except asyncio.CancelledError:
                    raise
                except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                    logger.warning("Network error during death-page poll: %s", exc)
                except Exception:
                    logger.exception("Error during death-page poll")

                interval = self._get_setting("interval", POLL_INTERVAL_SECONDS)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    async def _poll_once(self) -> None:
        """Single poll iteration."""
        year = datetime.now(timezone.utc).year
        url = f"https://en.wikipedia.org/api/rest_v1/page/html/Deaths_in_{year}"

        headers = {"If-None-Match": self._deaths_page_etag} if self._deaths_page_etag else {}
        resp = await self._client().get(url, headers=headers)
        if resp.status_code == 304:
            return
        if resp.status_code != 200:
            logger.warning("Deaths page returned %s", resp.status_code)
            return
        self._deaths_page_etag = resp.headers.get("etag")

        # The page is large and grows all year; parse it off the event loop.
        current = set(await asyncio.to_thread(parse_deaths_html, resp.text))

        if self._first_poll:
            # Baseline — don't announce anything
            self._known_names = current
            self._first_poll = False
            logger.info(
                "Death service baseline: %s names for %s", len(current), year
            )
            return

        new_names = current - self._known_names

        if not new_names:
            self._known_names = current
            return

        logger.info("Detected %s new name(s) on deaths page", len(new_names))

        # Names whose view count we could not determine this round. They are
        # deliberately left out of the known-names set so the next poll sees
        # them as new again and retries: a transient pageviews-API failure must
        # never silently swallow a death.
        unchecked: Set[Tuple[str, str]] = set()

        for display_name, article_title in new_names:
            try:
                # Never announce the same person twice, even if they dropped off
                # the deaths page and reappeared.
                if article_title in self._announced_titles:
                    logger.debug(
                        "Already announced %s (%s) — skipping", display_name, article_title
                    )
                    continue

                min_views = self._get_setting("min_views", MIN_AVG_MONTHLY_VIEWS)
                avg_views = await self.get_avg_monthly_views(article_title)

                if avg_views is None:
                    logger.warning(
                        "Could not determine view count for %s (%s) — not marking as seen, will retry next poll", display_name, article_title
                    )
                    unchecked.add((display_name, article_title))
                    continue

                if avg_views >= min_views:
                    # The deaths page is open to anyone, so vandal entries
                    # (football clubs, bands, ...) do turn up. Only announce
                    # articles Wikidata confirms are about a human.
                    person = await self.is_person(article_title)
                    if person is None:
                        logger.warning(
                            "Could not confirm %s (%s) is a person — not marking as seen, will retry next poll", display_name, article_title
                        )
                        unchecked.add((display_name, article_title))
                        continue
                    if not person:
                        logger.info(
                            "Skipped: %s (%s) is not a person per Wikidata", display_name, article_title
                        )
                        continue
                    logger.info(
                        "Met threshold: %s (%s avg monthly views) -> ANNOUNCING", display_name, f"{avg_views:,}"
                    )
                    if await self._announce(display_name, article_title, avg_views):
                        self._announced_titles.add(article_title)
                    else:
                        logger.warning(
                            "Couldn't post the announcement for %s (%s) — not marking as seen, will retry next poll", display_name, article_title
                        )
                        unchecked.add((display_name, article_title))
                else:
                    logger.info(
                        "Skipped: %s (%s avg monthly views, below %s threshold)",
                        display_name, f"{avg_views:,}", f"{min_views:,}",
                    )
            except Exception:
                logger.exception(
                    "Error checking views for %s — not marking as seen, will retry next poll", display_name
                )
                unchecked.add((display_name, article_title))

        # Everything currently on the page is now known, except the names we
        # could not check — those stay "new" so the next poll retries them.
        self._known_names = current - unchecked
        if unchecked:
            # Or the next poll gets a 304 and returns before retrying them.
            self._deaths_page_etag = None
        self._save_state()

    # -- person check -------------------------------------------------------

    async def _resolve_title(self, candidates: List[str]) -> Optional[str]:
        """The real article title for the first of ``candidates`` that exists, following redirects.

        So "Michael jackson" gives ``Michael_Jackson``. Returns None when none of them is an
        article. If Wikipedia can't answer, the best guess is returned so the caller can go on.
        """
        for title in candidates:
            try:
                resp = await self._client().get(
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "query",
                        "redirects": 1,
                        "titles": title.replace("_", " "),
                        "format": "json",
                        "formatversion": 2,
                    },
                )
                if resp.status_code != 200:
                    logger.warning("Wikipedia title lookup returned %s for %s", resp.status_code, title)
                    return candidates[0]
                pages = resp.json().get("query", {}).get("pages", [])
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("Could not look up Wikipedia title %s: %s", title, exc)
                return candidates[0]
            # More than one page means the input held a "|", which the API takes as a separator.
            if len(pages) == 1 and pages[0].get("ns") == 0 and not {"missing", "invalid"} & pages[0].keys():
                return pages[0]["title"].replace(" ", "_")
        return None

    async def is_person(self, article_title: str) -> Optional[bool]:
        """Whether Wikidata says *article_title* is about a human.

        Looks up the article's Wikidata item (following redirects), then checks
        its "instance of" (P31) claims for "human" (Q5). Returns True when it
        is, False when the item says it is something else, and None when the
        lookup failed and should be retried. An article with no Wikidata item
        yet (brand-new pages) or no P31 claims is given the benefit of the doubt.
        """
        try:
            resp = await self._client().get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "prop": "pageprops",
                    "ppprop": "wikibase_item",
                    "redirects": 1,
                    "titles": article_title.replace("_", " "),
                    "format": "json",
                    "formatversion": 2,
                },
            )
            if resp.status_code != 200:
                logger.warning("Wikipedia pageprops returned %s for %s", resp.status_code, article_title)
                return None
            pages = resp.json().get("query", {}).get("pages", [])
            item = pages[0].get("pageprops", {}).get("wikibase_item") if pages else None
            if not item:
                return True

            resp = await self._client().get(
                "https://www.wikidata.org/w/api.php",
                params={
                    "action": "wbgetclaims",
                    "entity": item,
                    "property": WIKIDATA_INSTANCE_OF,
                    "format": "json",
                },
            )
            if resp.status_code != 200:
                logger.warning("Wikidata returned %s for %s (%s)", resp.status_code, item, article_title)
                return None
            claims = resp.json().get("claims", {}).get(WIKIDATA_INSTANCE_OF, [])
            if not claims:
                return True
            kinds = {
                c.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id")
                for c in claims
            }
            return WIKIDATA_HUMAN in kinds
        except (httpx.HTTPError, asyncio.TimeoutError, ValueError) as exc:
            logger.warning("Person check failed for %s: %s", article_title, exc)
            return None

    # -- pageviews ----------------------------------------------------------

    async def get_avg_monthly_views(self, article_title: str) -> Optional[int]:
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
            last_attempt = attempt == max_retries - 1
            try:
                resp = await self._client().get(url)
                if resp.status_code == 200:
                    items = resp.json().get("items", [])
                    if not items:
                        return 0
                    return sum(item.get("views", 0) for item in items) // len(items)

                if resp.status_code == 404:
                    return 0  # No data / page doesn't exist

                # Rate limits and upstream 5xx are transient: back off and retry.
                if resp.status_code == 429 or resp.status_code >= 500:
                    if last_attempt:
                        logger.warning("Pageviews API returned %s for %s: %s", resp.status_code, article_title, resp.text)
                        break
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        "Pageviews API returned %s for %s: %s — retrying in %ss (attempt %s/%s)", resp.status_code, article_title, resp.text, delay, attempt + 1, max_retries
                    )
                    await asyncio.sleep(delay)
                    continue

                logger.warning("Pageviews API returned %s for %s: %s", resp.status_code, article_title, resp.text)
                return None
            except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                if last_attempt:
                    logger.warning("Network error fetching pageviews for %s: %s", article_title, exc)
                    break
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "Network error fetching pageviews for %s: %s — retrying in %ss (attempt %s/%s)", article_title, exc, delay, attempt + 1, max_retries
                )
                await asyncio.sleep(delay)

        logger.error("Failed to fetch pageviews for %s after %s attempts.", article_title, max_retries)
        return None

    # -- announcement -------------------------------------------------------

    async def _announce(
        self, display_name: str, article_title: str, avg_views: int
    ) -> bool:
        """Send a death announcement to the configured channel.

        False only when Discord refused the RIP message, which is worth retrying. A missing
        bot or channel won't fix itself by the next poll, so those count as handled.
        """
        if not self._bot:
            logger.error("Bot not set — cannot announce death")
            return True

        channel = self._bot.get_channel(int(self._death_channel_id))
        if channel is None:
            logger.error("Could not find channel %s", self._death_channel_id)
            return True

        wiki_link = (
            f"https://en.wikipedia.org/wiki/{article_title.replace(' ', '_')}"
        )
        message = f"RIP {display_name} - {wiki_link}"

        logger.info("Announcing death: %s (%s avg monthly views)", message, f"{avg_views:,}")

        # Post the RIP line with the Wikipedia URL as-is first, so Discord can
        # unfurl the link embed, then follow up with the AI summary as a
        # separate message.
        try:
            await channel.send(message)
        except discord.HTTPException as exc:
            logger.error("Failed to send death announcement: %s", exc)
            return False

        summary = await self._summarize_person(display_name, wiki_link, channel.id)
        if not summary:
            return True

        try:
            await channel.send(summary)
        except discord.HTTPException as exc:
            logger.error("Failed to send death summary follow-up: %s", exc)
        return True

    def _channel_model(self, channel_id: Optional[int]) -> Optional[str]:
        """The model set for ``channel_id``, or the default model when it has none."""
        if channel_id is not None:
            model = self._state_service.get_model(channel_id)
            if model:
                return model
        return self._model

    async def _summarize_person(
        self, display_name: str, wiki_link: Optional[str], channel_id: Optional[int] = None
    ) -> Optional[str]:
        """Ask the chat model for a limerick about the deceased.

        This call has no tools, so it can't open the link: the article's text goes in the prompt.
        Uses the model set for the channel it's posted in, like a mention there would.
        Best-effort: any failure, a model that says it can't write one, or an unconfigured
        model/service just returns None and the plain RIP message is sent.
        """
        model = self._channel_model(channel_id)
        if not model:
            return None

        subject = f"{display_name} ({wiki_link})" if wiki_link else display_name
        prompt = (
            f"Write a funny limerick about what {subject} is known for. It's for an "
            f"adult Discord server, so edgy is fine, as long as the jokes are about their public life and work. "
            f"Use as many verses as it takes to cover the main things they're known for. Your reply is "
            f"posted as-is, so give only a title line and the limerick. If you don't know enough about "
            f"them to write it, reply with only {NO_LIMERICK}."
        )
        article = await get_article_text(wiki_link) if wiki_link else None
        if article:
            prompt += (
                f"\n\nTheir Wikipedia article, as background facts:\n"
                f"<article>\n{article[:MAX_LIMERICK_ARTICLE_CHARS]}\n</article>"
            )
        try:
            reply, _cost = await dispatch_completion(
                model=model,
                messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
                system_prompt=None,
                gemini_service=self._gemini_service,
                anthropic_service=self._anthropic_service,
                identity={},
                search=False,
            )
            if isinstance(reply, dict):
                reply = reply.get("text", "")
            text = str(reply or "").strip()
            if NO_LIMERICK in text:
                logger.info("The model had no limerick for %s", display_name)
                return None
            return text or None
        except Exception:
            logger.exception("Failed to summarize %s for death announcement", display_name)
            return None

    async def write_limerick(self, target: str, channel_id: Optional[int] = None) -> Tuple[bool, str]:
        """Write a limerick about any person, with no announcement attached.

        Accepts a name or Wikipedia URL, and uses the model set for ``channel_id``.
        Returns ``(ok, message)`` where the message is the limerick itself on success.
        """
        if not self._channel_model(channel_id):
            return False, "No chat model is configured for limericks."

        candidates = wiki_title_candidates(target)
        if not candidates:
            return False, "Could not determine a person from that input."

        # Someone with no article can still get a limerick if the model knows them.
        article_title = await self._resolve_title(candidates)
        display_name = (article_title or candidates[0]).replace("_", " ")
        wiki_link = f"https://en.wikipedia.org/wiki/{article_title}" if article_title else None

        limerick = await self._summarize_person(display_name, wiki_link, channel_id)
        if not limerick:
            return False, f"The model failed to produce a limerick for **{display_name}**."
        return True, limerick

    async def force_announce(self, target: str) -> Tuple[bool, str]:
        """Force a death announcement for a Wikipedia URL/title (for testing).

        Bypasses the newness diff and the pageview threshold, and does NOT
        touch the announced-set, so it can be run repeatedly and never
        interferes with the real dedup state. Returns ``(ok, message)``.
        """
        if not self._death_channel_id:
            return False, "DEATH_CHANNEL_ID is not configured — no channel to announce to."

        candidates = wiki_title_candidates(target)
        if not candidates:
            return False, "Could not determine a Wikipedia article title from that input."

        article_title = await self._resolve_title(candidates)
        if not article_title:
            return False, f"No Wikipedia article found for **{candidates[0].replace('_', ' ')}**."

        display_name = article_title.replace("_", " ")

        # Fetch real pageviews purely for a realistic log line; the threshold is
        # intentionally not enforced here.
        avg_views = 0
        try:
            avg_views = await self.get_avg_monthly_views(article_title) or 0
        except Exception:
            logger.exception("force_announce: failed to fetch pageviews for %s", article_title)

        logger.info("[force_announce] Announcing %s (%s avg monthly views)", display_name, f"{avg_views:,}")
        await self._announce(display_name, article_title, avg_views)
        return True, f"Forced death announcement for **{display_name}** in the death channel."
