"""
IRC Log Service - Searches the channel's old IRC log (an irssi-style text file set by ``IRC_LOG``).

The file is memory-mapped rather than read in, so a 100MB log costs no heap; the OS page
cache keeps it warm between searches. Lines look like ``22:58 <@nick> text`` and carry no
date, so the offsets of the ``--- Day changed``/``--- Log opened`` markers are indexed once
and each match is dated by the last marker before it.

The model's queries run on the ``regex`` module, not ``re``: ``re`` holds the GIL for a whole
scan, so a slow pattern (``.*``, a backreference) froze the event loop past Discord's heartbeat
even from a worker thread. ``regex`` lets go of the GIL while it matches (``concurrent=True``)
and gives up on a pattern that runs too long.
"""

import bisect
import hashlib
import logging
import mmap
import re
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Deque, Dict, List, Optional, Sequence, Tuple

import regex

logger = logging.getLogger(__name__)

# irssi markers: "--- Day changed Wed May 20 2020" and "--- Log opened Wed May 20 06:00:00 2020".
_DATE_MARKER_RE = re.compile(
    rb"^--- (?:Day changed|Log opened) (\w{3} \w{3} \d{1,2})(?: [\d:]{8})? (\d{4})", re.M
)

MAX_RESULTS = 50
MAX_CONTEXT_LINES = 5
# Longest line shown; pasted walls of text get cut.
MAX_LINE_CHARS = 400
# Most lines one read_day call returns; busy days are paged.
MAX_DAY_LINES = 500
# Most nicks a count lists per group.
MAX_TOP_NICKS = 50
# A count stops after this many occurrences, so a pattern like '.' can't run for minutes.
MAX_COUNTED = 3_000_000
# How long one search or count may spend matching before it gives up on the pattern.
QUERY_TIMEOUT_SECONDS = 60.0

# A chat line's speaker: "12:00 <@nick> text" or an action, "12:00  * nick does". Joins, quits
# and other "-!-" lines don't match, so they're never counted.
_SPEAKER_RE = re.compile(rb"\d\d:\d\d(?::\d\d)?\s+(?:<[ @+%~&!]?([^>\s]+)>|\*\s+(\S+))")
# The same, found anywhere in the log, for tallying who spoke.
_SPEAKER_LINE_RE = re.compile(rb"^" + _SPEAKER_RE.pattern, re.M)


# The canonical name that drops a nick (bots and junk) in an irssistats nicks file.
NULL_NICK = "<NULL>"


class NickMap:
    """Nick consolidation from an irssistats-style nicks file.

    Each line is ``canonical pattern``; the pattern is a case-sensitive regex searched in the
    nick, and the first matching line wins. A canonical name of ``<NULL>`` drops the nick.
    A pattern that's invalid or matches empty text (``*>``, ``|-_-|``) is taken literally,
    since as a regex it would fail or match every nick.
    """

    def __init__(self, rules: Sequence[Tuple[str, str]]):
        # Identifies these rules, so an index built with them is rebuilt when they change.
        self.signature = hashlib.sha1(repr(list(rules)).encode("utf-8")).hexdigest()
        self._rules: List[Tuple[str, re.Pattern]] = []
        for canonical, pattern in rules:
            try:
                compiled = re.compile(pattern)
                if compiled.search(""):
                    compiled = re.compile(re.escape(pattern))
            except re.error:
                compiled = re.compile(re.escape(pattern))
            self._rules.append((canonical, compiled))
        self._cache: Dict[str, Optional[str]] = {}

    @classmethod
    def from_file(cls, path: str) -> "NickMap":
        rules = []
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            fields = line.split()
            if len(fields) >= 2:
                rules.append((fields[0], fields[1]))
        return cls(rules)

    def canonical(self, nick: str) -> Optional[str]:
        """``nick``'s canonical name, ``nick`` itself when no line matches, or None when it's dropped."""
        if nick not in self._cache:
            name: Optional[str] = nick
            for canonical, pattern in self._rules:
                if pattern.search(nick):
                    name = None if canonical == NULL_NICK else canonical
                    break
            self._cache[nick] = name
        return self._cache[nick]

    def __len__(self) -> int:
        return len(self._rules)


@dataclass(frozen=True)
class _Match:
    start: int  # offset of the matching line's first byte
    end: int    # offset of its newline (or EOF)


def _compile_query(query: str) -> "regex.Pattern":
    """``query`` as a case-insensitive bytes pattern; taken literally when it isn't a valid regex."""
    try:
        return regex.compile(query.encode("utf-8"), regex.I)
    except regex.error:
        return regex.compile(regex.escape(query.encode("utf-8")), regex.I)


def _too_slow(query: str) -> str:
    return (f"Gave up on {query!r} after {QUERY_TIMEOUT_SECONDS:.0f}s: that pattern is too slow on a log "
            "this size (backreferences and nested or stacked repeats like '.*' are the usual cause). "
            "Try a simpler one, e.g. plain words or a short alternation.")


class IrcLog:
    """A lazily opened, memory-mapped IRC log with a date index."""

    def __init__(self, path: str, nicks: Optional[NickMap] = None):
        self.path = Path(path)
        self.nicks = nicks
        self._lock = threading.Lock()
        self._data: Optional[mmap.mmap] = None
        self._mtime: float = 0.0
        self._marker_offsets: List[int] = []
        self._marker_days: List[Optional[date]] = []

    def _load(self) -> mmap.mmap:
        """Map the file (again if it changed on disk) and rebuild the date index."""
        with self._lock:
            mtime = self.path.stat().st_mtime
            if self._data is not None and mtime == self._mtime:
                return self._data
            with open(self.path, "rb") as f:
                data = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            offsets, days = [], []
            for m in _DATE_MARKER_RE.finditer(data):
                offsets.append(m.start())
                days.append(_parse_marker_day(m.group(1), m.group(2)))
            if self._data is not None:
                try:
                    self._data.close()
                except BufferError:
                    pass  # a search in another thread still reads it; it unmaps once that's done
            self._data, self._mtime = data, mtime
            self._marker_offsets, self._marker_days = offsets, days
            logger.info(f"[irc log] Mapped {self.path} ({len(data) / 1e6:.1f}MB, {len(days)} date markers)")
            return data

    def _canonical(self, nick: str) -> Optional[str]:
        """``nick``'s canonical name; None when the nicks file drops it or it isn't a person (``***``)."""
        if not any(c.isalnum() for c in nick):
            return None
        return self.nicks.canonical(nick) if self.nicks else nick

    def _nick_key(self, nick: str) -> Optional[str]:
        """How a nick is compared and grouped: its canonical name, lowercased; None when dropped."""
        name = self._canonical(nick)
        return name.lower() if name is not None else None

    def _speaker(self, data: mmap.mmap, line_start: int, line_end: int) -> Tuple[Optional[str], str, int]:
        """``(nick key, nick as shown, offset where the said text starts)`` for a chat or action line.

        The key is None for other lines ("-!-" joins and quits) and for dropped nicks.
        """
        head = _SPEAKER_RE.match(data, line_start, line_end)
        if head is None:
            return None, "", line_end
        raw = (head.group(1) or head.group(2)).decode("utf-8", "replace")
        shown = self._canonical(raw)
        return (shown.lower() if shown is not None else None), shown or raw, head.end()

    def _date_at(self, offset: int) -> str:
        i = bisect.bisect_right(self._marker_offsets, offset) - 1
        return _day_label(self._marker_days[i] if i >= 0 else None)

    def search(
        self,
        query: str,
        *,
        nick: str = "",
        context: int = 0,
        limit: int = 20,
        newest_first: bool = False,
        skip: int = 0,
    ) -> str:
        """Find lines matching ``query`` (a case-insensitive regex) and format them grouped by date."""
        data = self._load()
        limit = max(1, min(limit, MAX_RESULTS))
        context = max(0, min(context, MAX_CONTEXT_LINES))
        skip = max(0, skip)

        body = _compile_query(query)
        # A nick filter matches every alias of the nick (from the nicks file), case-insensitively.
        wanted = self._nick_key(nick) if nick else None

        # One hit per line: after a match, carry on from the next line.
        kept: Deque[_Match] = deque(maxlen=skip + limit if newest_first else None)
        total = 0
        pos = 0
        deadline = time.monotonic() + QUERY_TIMEOUT_SECONDS
        while pos < len(data):
            try:
                m = body.search(data, pos, concurrent=True, timeout=max(0.001, deadline - time.monotonic()))
            except TimeoutError:
                return _too_slow(query)
            if m is None:
                break
            line_start = data.rfind(b"\n", 0, m.start()) + 1
            line_end = data.find(b"\n", m.start())
            if line_end < 0:
                line_end = len(data)
            pos = line_end + 1
            if m.end() > line_end or (nick and self._speaker(data, line_start, line_end)[0] != wanted):
                continue
            total += 1
            if newest_first or skip < total <= skip + limit:
                kept.append(_Match(line_start, line_end))
        matches = list(kept)[::-1][skip:] if newest_first else list(kept)

        if not matches:
            return f"No matches for {query!r}" + (f" from {nick}" if nick else "") + (
                f" past the first {skip}." if skip and total else ".")

        blocks: List[Tuple[str, List[str]]] = []
        last_end = -1
        for match in matches:
            start, end = match.start, match.end
            for _ in range(context):
                if start == 0:
                    break
                start = data.rfind(b"\n", 0, start - 1) + 1
            for _ in range(context):
                if end >= len(data):
                    break
                nxt = data.find(b"\n", end + 1)
                end = len(data) if nxt < 0 else nxt
            date = self._date_at(match.start)
            lines = [_decode_line(raw) for raw in data[start:end].split(b"\n") if raw]
            # Hits from the same day share a header; "…" marks a gap between excerpts.
            if blocks and blocks[-1][0] == date:
                if not newest_first and start <= last_end + 1:
                    blocks[-1][1].extend(lines[_overlap(blocks[-1][1], lines):])
                else:
                    blocks[-1][1].extend((["…"] if context else []) + lines)
            else:
                blocks.append((date, lines))
            last_end = end

        shown_to = skip + len(matches)
        header = f"{total} matching lines; showing {skip + 1}-{shown_to}, {'newest' if newest_first else 'oldest'} first."
        if shown_to < total:
            header += f" Use skip={shown_to} for more, or narrow the query."
        parts = [header]
        for date, lines in blocks:
            parts.append(f"\n[{date}]\n" + "\n".join(lines))
        return "\n".join(parts)

    def _day_at(self, offset: int) -> Optional[date]:
        i = bisect.bisect_right(self._marker_offsets, offset) - 1
        return self._marker_days[i] if i >= 0 else None

    def count(
        self,
        query: str,
        *,
        nick: str = "",
        group_by: Sequence[str] = (),
        top: int = 15,
    ) -> str:
        """Count occurrences of ``query`` (a case-insensitive regex) in what people said.

        Only chat and action lines count, and only their text (not the nick). Every
        occurrence counts, so a line saying it twice adds two. ``group_by`` takes
        ``"nick"`` and/or ``"year"``; with both, each year lists its top nicks.
        """
        data = self._load()
        top = max(1, min(top, MAX_TOP_NICKS))
        body = _compile_query(query)
        if body.fullmatch(b""):
            return (f"{query!r} matches empty text, so it can't be counted. "
                    "Use a pattern that needs at least one character.")
        wanted = self._nick_key(nick) if nick else None

        total = 0
        lines = 0
        by_nick: Counter = Counter()
        by_year: Counter = Counter()
        by_year_nick: Dict[int, Counter] = {}
        names: Dict[str, str] = {}
        line_start = line_end = -1
        key: Optional[str] = None
        shown = ""
        text_start = 0
        year: Optional[int] = None
        counted_line = False
        truncated = False
        matches = body.finditer(data, concurrent=True, timeout=QUERY_TIMEOUT_SECONDS)
        try:
            for m in matches:
                if m.start() >= line_end:
                    line_start = data.rfind(b"\n", 0, m.start()) + 1
                    line_end = data.find(b"\n", m.start())
                    if line_end < 0:
                        line_end = len(data)
                    key, shown, text_start = self._speaker(data, line_start, line_end)
                    if nick and key != wanted:
                        key = None
                    counted_line = False
                    if key is not None:
                        day = self._day_at(line_start)
                        year = day.year if day else None
                if key is None or m.start() < text_start or m.end() > line_end or m.end() == m.start():
                    continue
                if not counted_line:
                    lines += 1
                    counted_line = True
                total += 1
                names.setdefault(key, shown)
                if "nick" in group_by:
                    by_nick[key] += 1
                if "year" in group_by and year is not None:
                    by_year[year] += 1
                    if "nick" in group_by:
                        by_year_nick.setdefault(year, Counter())[key] += 1
                if total >= MAX_COUNTED:
                    truncated = True
                    break
        except TimeoutError:
            return _too_slow(query)

        who = f" by {self._canonical(nick) or nick}" if nick else ""
        if not total:
            return f"No occurrences of {query!r}{who} in the IRC log."
        parts = [f"{total:,} occurrence{'s' if total != 1 else ''} of {query!r}{who} on "
                 f"{lines:,} line{'s' if lines != 1 else ''} "
                 "(chat and action lines only, nicks not counted)."]
        if truncated:
            parts[0] += f" Stopped counting at {MAX_COUNTED:,}; the real total is higher."

        def ranked(counter: Counter, out_of: int) -> List[str]:
            rows = [f"  {names[k]}: {n:,} ({n / out_of:.1%})" for k, n in counter.most_common(top)]
            rest = sum(counter.values()) - sum(n for _, n in counter.most_common(top))
            if rest:
                rows.append(f"  {len(counter) - top} others: {rest:,}")
            return rows

        if "year" in group_by:
            parts.append("By year:")
            for y in sorted(by_year):
                parts.append(f"{y}: {by_year[y]:,}")
                if "nick" in group_by:
                    parts += ["  " + row for row in ranked(by_year_nick[y], by_year[y])]
        elif "nick" in group_by:
            parts.append(f"Top {min(top, len(by_nick))} of {len(by_nick)} nicks:")
            parts += ranked(by_nick, total)
        return "\n".join(parts)

    def speakers(self, top: int = 60) -> List[Tuple[str, int, Optional[int], Optional[int]]]:
        """The ``top`` people with the most chat and action lines, as ``(name, lines, first year, last year)``.

        Names are canonical when there's a nicks file, and dropped nicks aren't listed.
        """
        data = self._load()
        counts: Counter = Counter()
        names: Dict[str, str] = {}
        first: Dict[str, int] = {}
        last: Dict[str, int] = {}
        marker = 0
        for m in _SPEAKER_LINE_RE.finditer(data):
            shown = self._canonical((m.group(1) or m.group(2)).decode("utf-8", "replace"))
            if shown is None:
                continue
            key = shown.lower()
            counts[key] += 1
            names.setdefault(key, shown)
            # Lines come in order, so the current day only moves forward.
            while marker < len(self._marker_offsets) and self._marker_offsets[marker] <= m.start():
                marker += 1
            day = self._marker_days[marker - 1] if marker else None
            if day:
                first.setdefault(key, day.year)
                last[key] = day.year
        return [(names[k], n, first.get(k), last.get(k)) for k, n in counts.most_common(top)]

    def read_day(self, day: date, *, start_line: int = 0) -> str:
        """Every line logged on ``day``, up to :data:`MAX_DAY_LINES` from ``start_line`` on."""
        data = self._load()
        start_line = max(0, start_line)
        lines: List[str] = []
        # A day can span several markers (a "Day changed" plus "Log opened" after reconnects).
        for i, marker_day in enumerate(self._marker_days):
            if marker_day != day:
                continue
            seg_start = data.find(b"\n", self._marker_offsets[i]) + 1
            seg_end = self._marker_offsets[i + 1] if i + 1 < len(self._marker_offsets) else len(data)
            if seg_start <= 0:
                continue
            for raw in data[seg_start:seg_end].split(b"\n"):
                if raw.strip() and not raw.startswith(b"--- "):
                    lines.append(_decode_line(raw))

        if not lines:
            return f"Nothing logged on {_day_label(day)}. The log covers {self.coverage()}."
        shown = lines[start_line:start_line + MAX_DAY_LINES]
        if not shown:
            return f"{_day_label(day)} has only {len(lines)} lines."
        end_line = start_line + len(shown)
        header = f"[{_day_label(day)}] {len(lines)} lines; showing {start_line + 1}-{end_line}."
        if end_line < len(lines):
            header += f" Use start_line={end_line} for the rest."
        return header + "\n" + "\n".join(shown)

    def conversations(
        self, *, max_lines: int = 40, gap_minutes: int = 30, max_chars: int = 6000,
    ) -> List[Tuple[int, int, Optional[date]]]:
        """Split the log into conversations: ``(start offset, end offset, day)``.

        One ends at a day marker, a pause of ``gap_minutes`` between chat lines,
        ``max_lines`` chat lines or ``max_chars``. Joins, quits and other non-chat lines
        don't count toward a conversation and never start one.
        """
        data = self._load()
        spans: List[Tuple[int, int, Optional[date]]] = []
        start = -1
        end = lines = chars = 0
        last_minute = -1
        day: Optional[date] = None
        marker = 0

        def close() -> None:
            nonlocal start
            if start >= 0:
                spans.append((start, end, day))
            start = -1

        pos = 0
        size = len(data)
        while pos < size:
            nl = data.find(b"\n", pos)
            line_end = size if nl < 0 else nl
            while marker < len(self._marker_offsets) and self._marker_offsets[marker] <= pos:
                close()
                day = self._marker_days[marker]
                marker += 1
                last_minute = -1
            head = _SPEAKER_RE.match(data, pos, line_end)
            if head is not None:
                stamp = data[pos:pos + 5]
                minute = int(stamp[:2]) * 60 + int(stamp[3:5]) if stamp[:2].isdigit() and stamp[3:5].isdigit() else -1
                gap = minute >= 0 and last_minute >= 0 and minute - last_minute >= gap_minutes
                length = line_end - pos
                if start >= 0 and (gap or lines >= max_lines or chars + length > max_chars):
                    close()
                if start < 0:
                    start, lines, chars = pos, 0, 0
                end = line_end
                lines += 1
                chars += length
                if minute >= 0:
                    last_minute = minute
            pos = line_end + 1
        close()
        return spans

    def excerpt(self, start: int, end: int) -> Tuple[str, List[str]]:
        """``(day label, chat lines)`` for a span from :meth:`conversations`.

        Each speaker is shown by their canonical name from the nicks file, so every alias of a
        person reads (and is searched) as the same name. Lines from nicks it drops (bots),
        joins and quits are left out.
        """
        data = self._load()
        lines = []
        for raw in data[start:end].split(b"\n"):
            head = _SPEAKER_RE.match(raw) if raw else None
            if head is None:
                continue
            nick = (head.group(1) or head.group(2)).decode("utf-8", "replace")
            name = self._canonical(nick)
            if name is None:
                continue
            said = _decode_line(raw[head.end():]).strip()
            stamp = raw[:head.start(1) if head.group(1) else head.start(2)].decode("utf-8", "replace").split()[0]
            lines.append(f"{stamp} <{name}> {said}" if head.group(1) else f"{stamp} * {name} {said}")
        return self._date_at(start), lines

    def speaks_in(self, start: int, end: int, nick: str) -> bool:
        """Whether ``nick`` (or any of their aliases) has a chat line between the offsets."""
        wanted = self._nick_key(nick)
        data = self._load()
        pos = start
        while pos < end:
            nl = data.find(b"\n", pos, end)
            line_end = end if nl < 0 else nl
            if self._speaker(data, pos, line_end)[0] == wanted:
                return True
            pos = line_end + 1
        return False

    def first_day(self) -> Optional[date]:
        """The first dated day in the log."""
        self._load()
        return next((d for d in self._marker_days if d), None)

    def coverage(self) -> str:
        """The first date in the log to the last one anything was said on, e.g. ``2014-01-03 (Fri) to 2023-06-18 (Sun)``.

        Day markers keep getting logged after the channel went quiet, so the end comes from
        the last chat line rather than the last marker.
        """
        data = self._load()
        first = self.first_day()
        if first is None:
            return "unknown dates"
        pos = len(data)
        while pos > 0:
            line_start = data.rfind(b"\n", 0, pos) + 1
            line = data[line_start:pos].strip()
            if line and not line.startswith(b"--- "):
                return f"{_day_label(first)} to {self._date_at(line_start)}"
            pos = line_start - 1
        return _day_label(first)


def _parse_marker_day(month_day: bytes, year: bytes) -> Optional[date]:
    try:
        return datetime.strptime(f"{month_day.decode('ascii')} {year.decode('ascii')}", "%a %b %d %Y").date()
    except (UnicodeDecodeError, ValueError):
        return None


def _day_label(day: Optional[date]) -> str:
    return f"{day.isoformat()} ({day.strftime('%a')})" if day else "unknown date"


def _decode_line(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace")
    return text[:MAX_LINE_CHARS] + "…" if len(text) > MAX_LINE_CHARS else text


def _overlap(previous: List[str], lines: List[str]) -> int:
    """How many of ``lines``' leading lines repeat ``previous``' trailing ones."""
    for n in range(min(len(previous), len(lines)), 0, -1):
        if previous[-n:] == lines[:n]:
            return n
    return 0


def load_irc_log(path: str, nicks_path: str = "") -> Optional[IrcLog]:
    """An :class:`IrcLog` for ``path``, or None when it's unset or missing.

    ``nicks_path`` is an optional irssistats-style nicks file for consolidating aliases.
    """
    if not path:
        return None
    if not Path(path).is_file():
        logger.warning(f"[irc log] IRC_LOG={path} isn't a file; the search_irc_log tool is off.")
        return None
    nicks = None
    if nicks_path:
        try:
            nicks = NickMap.from_file(nicks_path)
            logger.info(f"[irc log] Loaded {len(nicks)} nick rules from {nicks_path}")
        except OSError as e:
            logger.warning(f"[irc log] Couldn't read IRC_NICKS={nicks_path}: {e}; nicks won't be consolidated.")
    return IrcLog(path, nicks)
