"""
Election Service - Read-only election results for the ``election_results`` chat tool, from civicAPI.

civicAPI (https://civicapi.org/api-documentation) is free and keyless, with races worldwide. Its attribution
rule (link to civicapi.org or name it) is met by the source line on every result.

Search hands races back in no useful order, with a country's national race possibly after hundreds of regional
ones, so a search fetches many and ranks them here, most votes first. Whether a race has been called is only
what civicAPI says: a candidate's ``winner`` flag, never who is ahead. That flag also marks candidates who only
advanced to a runoff. A full race usually says so with ``advanced``, but search rows leave that field out, and
``called_at`` can be empty on a race with a winner.
"""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from src.utils.market_format import num

logger = logging.getLogger(__name__)

API_URL = "https://civicapi.org/api/v2"
SITE_URL = "https://civicapi.org/results/elections/"
SOURCE = "Source: civicAPI (civicapi.org)"
# Name searches "may take a few seconds", and longer the day after a big election.
TIMEOUT = 20.0
# Waits before retrying when it's overloaded, which it says with a 503 right after big elections.
RETRY_DELAYS = (2.0, 5.0)
RETRY_STATUSES = {429, 502, 503, 504}

SEARCH_FETCH = 500
SEARCH_PAGE = 12
MAX_SEARCH_CANDIDATES = 5
MAX_RACE_CANDIDATES = 25
MAX_REGIONS = 12
MAX_REGION_CANDIDATES = 3

_LINK_RE = re.compile(r"civicapi\.org/results/elections/(\d+)", re.I)
_RACE_ID_RE = re.compile(r"\d+")
# Tallies some races count alongside votes: a chamber's seats, a president's electoral votes.
_TALLIES = (("seats", "seats"), ("electoral_votes", "electoral votes"), ("delegates", "delegates"))


class ElectionDataError(Exception):
    """civicAPI couldn't be reached or answered with an error."""


def parse_race_id(value: str) -> Optional[str]:
    """The race ID in a bare ID or a civicapi.org results link, or None when it's neither."""
    value = value.strip()
    if _RACE_ID_RE.fullmatch(value):
        return value
    match = _LINK_RE.search(value)
    return match.group(1) if match else None


async def _get(client: httpx.AsyncClient, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """GET a civicAPI path as JSON. A 404 is ``None``; anything else that fails raises ``ElectionDataError``."""
    for delay in (*RETRY_DELAYS, None):
        try:
            response = await client.get(f"{API_URL}/{path}", params=params)
        except httpx.HTTPError as e:
            raise ElectionDataError(f"Couldn't reach civicAPI: {e.__class__.__name__}") from e
        if response.status_code not in RETRY_STATUSES or delay is None:
            break
        logger.info("[civicapi] HTTP %s on %s, retrying in %ss", response.status_code, path, delay)
        await asyncio.sleep(delay)
    if response.status_code == 404:
        return None
    if response.status_code in RETRY_STATUSES:
        raise ElectionDataError(f"civicAPI is overloaded (HTTP {response.status_code}); try again in a bit.")
    if response.status_code >= 400:
        raise ElectionDataError(f"civicAPI answered HTTP {response.status_code}.")
    try:
        return response.json()
    except ValueError as e:
        raise ElectionDataError("civicAPI sent something that isn't JSON.") from e


def _candidates(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [c for c in data.get("candidates") or [] if isinstance(c, dict)]


def _total_votes(data: Dict[str, Any]) -> float:
    return sum(num(c.get("votes")) or 0.0 for c in _candidates(data))


def _ranked_candidates(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Called winners first, then by votes, then by any seats for races counted that way."""
    return sorted(_candidates(data), key=lambda c: (
        not c.get("winner"), -(num(c.get("votes")) or 0.0), -(num(c.get("seats")) or 0.0)))


def _number(value: float) -> str:
    return f"{value:,.0f}" if value == int(value) else f"{value:,.1f}"


def _outright_winners(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [c for c in _candidates(data) if c.get("winner") and not c.get("advanced")]


def _outcome(candidate: Dict[str, Any], winners: int) -> str:
    """What this candidate's flags mean, given how many outright winners the race has."""
    if candidate.get("advanced"):
        return "advanced to the next round"
    if not candidate.get("winner"):
        return ""
    # Several usually means a first round's runoff pair, which search rows can't tell from a multi-seat race.
    return "WINNER or advanced to a runoff" if winners > 1 else "WINNER"


def _candidate_line(candidate: Dict[str, Any], any_votes: bool, winners: int) -> str:
    name, party = str(candidate.get("name") or "?"), str(candidate.get("party") or "")
    label = f"{name} ({party})" if party and party != name else name
    parts = []
    if any_votes:
        parts.append(f"{num(candidate.get('percent')) or 0:g}%")
        parts.append(f"{_number(num(candidate.get('votes')) or 0)} votes")
    for key, tally in _TALLIES:
        count = num(candidate.get(key))
        if count:
            parts.append(f"{_number(count)} {tally}")
    outcome = _outcome(candidate, winners)
    if outcome:
        parts.append(outcome)
    if candidate.get("incumbent"):
        parts.append("incumbent")
    return f"{label}: " + " · ".join(parts) if parts else label


def _candidate_lines(data: Dict[str, Any], limit: int) -> List[str]:
    ranked = _ranked_candidates(data)
    any_votes = _total_votes(data) > 0
    winners = len(_outright_winners(data))
    lines = [_candidate_line(c, any_votes, winners) for c in ranked[:limit]]
    if len(ranked) > limit:
        lines.append(f"...and {len(ranked) - limit} more")
    return lines


def _progress(race: Dict[str, Any]) -> str:
    if _total_votes(race) <= 0 and not any(c.get("winner") for c in _candidates(race)):
        return "no results reported"
    reporting = num(race.get("percent_reporting"))
    return f"{reporting:g}% reporting" if reporting is not None else "share reporting unknown"


def _place(race: Dict[str, Any]) -> str:
    return " / ".join(str(race[k]) for k in ("country", "province", "district", "municipality") if race.get(k))


def _day(value: Any) -> str:
    return str(value or "")[:10] or "date unknown"


def _search_row(race: Dict[str, Any]) -> str:
    details = [f"race {race.get('id')}", _day(race.get("election_date")), _place(race), str(race.get("type") or ""),
               _progress(race)]
    if race.get("seats"):
        details.append(f"{race['seats']} seats")
    head = f"{race.get('election_name') or '?'} ({', '.join(d for d in details if d)})"
    lines = [head, f"{SITE_URL}{race.get('id')}"]
    lines += [f"  {line}" for line in _candidate_lines(race, MAX_SEARCH_CANDIDATES)]
    return "\n".join(lines)


async def search_races(*, query: str = "", country: str = "", province: str = "", district: str = "",
                       start_date: str = "", end_date: str = "", skip: int = 0) -> str:
    """Races matching the filters, most votes first, as text. Returns "" when none match."""
    params = {"query": query, "country": country.upper(), "province": province.upper(), "district": district,
              "startDate": start_date, "endDate": end_date, "limit": SEARCH_FETCH}
    params = {k: v for k, v in params.items() if v}
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": "cmwgpt"}) as client:
        data = await _get(client, "race/search", params) or {}
    races = [r for r in data.get("races") or [] if isinstance(r, dict)]
    if not races:
        return ""
    # Votes rank a nation over its regions; races with none yet go newest first.
    races.sort(key=lambda r: str(r.get("election_date") or ""), reverse=True)
    races.sort(key=_total_votes, reverse=True)

    skip = max(skip, 0)
    page = races[skip:skip + SEARCH_PAGE]
    if not page:
        return f"Only {len(races)} races match, so skip={skip} is past the end.\n{SOURCE}"
    count = f"At least {len(races)}" if len(races) >= SEARCH_FETCH else str(len(races))
    header = f"{count} races match. Showing {skip + 1}-{skip + len(page)}, most votes first."
    if len(races) >= SEARCH_FETCH:
        header += " That's only the first batch civicAPI gave, so the biggest races may be missing: add filters."
    elif skip + len(page) < len(races):
        header += f" Use skip={skip + len(page)} for more."
    return "\n\n".join([header, *(_search_row(r) for r in page), SOURCE])


def _regions(race: Dict[str, Any]) -> List[Dict[str, Any]]:
    regions = race.get("region_results")
    values = regions.values() if isinstance(regions, dict) else regions if isinstance(regions, list) else []
    return [r for r in values if isinstance(r, dict)]


def _region_line(region: Dict[str, Any]) -> str:
    name = str(region.get("name") or "?")
    kind = str(region.get("type") or "")
    label = f"{name} {kind}" if kind and not name.endswith(kind) else name
    ranked = sorted(_candidates(region), key=lambda c: -(num(c.get("votes")) or 0.0))[:MAX_REGION_CANDIDATES]
    results = "; ".join(
        f"{c.get('name') or '?'} {num(c.get('percent')) or 0:g}% ({_number(num(c.get('votes')) or 0)})" for c in ranked)
    return f"{label} ({_progress(region)}): {results or 'no candidates listed'}"


def _region_lines(race: Dict[str, Any], region_filter: str) -> List[str]:
    regions = sorted(_regions(race), key=_total_votes, reverse=True)
    if not regions:
        return ["No breakdown by region."] if region_filter else []
    wanted = region_filter.casefold()
    matches = [r for r in regions if wanted in str(r.get("name") or "").casefold()] if wanted else regions
    if wanted and not matches:
        lines = [f'No region matches "{region_filter}". The biggest of {len(regions)}:']
        matches = regions
    elif wanted:
        lines = [f'Regions matching "{region_filter}":']
    else:
        lines = [f"Biggest of {len(regions)} regions (use region to see a particular one):"]
    return lines + [_region_line(r) for r in matches[:MAX_REGIONS]]


def _call_status(race: Dict[str, Any]) -> str:
    if race.get("called_at"):
        return f"Called: {race['called_at']}"
    if _outright_winners(race):
        return "Called by civicAPI; it doesn't say when."
    if any(c.get("advanced") for c in _candidates(race)):
        return "No outright winner: the candidates marked advanced go to the next round."
    return "Not called by civicAPI."


async def race_results(race_id: str, region: str = "") -> str:
    """One race in full, as text, optionally with the named regions. Returns "" when there's no such race."""
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": "cmwgpt"}) as client:
        race = await _get(client, f"race/{race_id}")
    if not isinstance(race, dict) or not race.get("election_name"):
        return ""
    kinds = ", ".join(str(race[k]) for k in ("election_type", "election_scope") if race.get(k))
    lines = [
        f"{race['election_name']} (race {race_id})",
        f"{SITE_URL}{race_id}",
        f"{_day(race.get('election_date'))} · {_place(race)} · {kinds} · {_progress(race)}",
    ]
    if (num(race.get("round")) or 0) > 1:
        lines.append(f"Round {race['round']}")
    if race.get("registered_voters"):
        lines.append(f"Registered voters: {_number(num(race['registered_voters']) or 0)}")
    lines.append(_call_status(race))
    if race.get("last_updated"):
        lines.append(f"Last updated: {race['last_updated']}")
    if race.get("is_disputed"):
        lines.append("civicAPI flags this result as disputed.")
    if race.get("test_data"):
        lines.append("civicAPI marks this as test data, not real results.")
    lines += ["", *_candidate_lines(race, MAX_RACE_CANDIDATES)]
    region_lines = _region_lines(race, region)
    if region_lines:
        lines += ["", *region_lines]
    lines += ["", SOURCE]
    return "\n".join(lines)
