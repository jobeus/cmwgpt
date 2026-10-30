"""
IRC Log Service - Searches the channel's old IRC log (an irssi-style text file set by ``IRC_LOG``).

The file is memory-mapped rather than read in, so a 100MB log costs no heap; the OS page
cache keeps it warm between searches. Lines look like ``22:58 <@nick> text`` and carry no
date, so the offsets of the ``--- Day changed``/``--- Log opened`` markers are indexed once
and each match is dated by the last marker before it.
"""

import bisect
import logging
import mmap
import re
import threading
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Deque, List, Optional, Tuple

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


@dataclass(frozen=True)
class _Match:
    start: int  # offset of the matching line's first byte
    end: int    # offset of its newline (or EOF)


class IrcLog:
    """A lazily opened, memory-mapped IRC log with a date index."""

    def __init__(self, path: str):
        self.path = Path(path)
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
                self._data.close()
            self._data, self._mtime = data, mtime
            self._marker_offsets, self._marker_days = offsets, days
            logger.info(f"[irc log] Mapped {self.path} ({len(data) / 1e6:.1f}MB, {len(days)} date markers)")
            return data

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

        try:
            body = re.compile(query.encode("utf-8"), re.I)
        except re.error:
            body = re.compile(re.escape(query.encode("utf-8")), re.I)
        # Nicks may carry a mode prefix (<@nick>, <+nick>) and actions look like " * nick does".
        speaker = None
        if nick:
            n = re.escape(nick.encode("utf-8"))
            speaker = re.compile(rb"\d\d:\d\d(?::\d\d)?\s+(?:<[ @+%~&!]?" + n + rb">|\*\s+" + n + rb"\b)", re.I)

        # One hit per line: after a match, carry on from the next line.
        kept: Deque[_Match] = deque(maxlen=skip + limit if newest_first else None)
        total = 0
        pos = 0
        while pos < len(data):
            m = body.search(data, pos)
            if m is None:
                break
            line_start = data.rfind(b"\n", 0, m.start()) + 1
            line_end = data.find(b"\n", m.start())
            if line_end < 0:
                line_end = len(data)
            pos = line_end + 1
            if m.end() > line_end or (speaker and not speaker.match(data, line_start)):
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

    def first_day(self) -> Optional[date]:
        """The first dated day in the log."""
        self._load()
        return next((d for d in self._marker_days if d), None)

    def coverage(self) -> str:
        """The first and last dates in the log, e.g. ``2014-01-03 (Fri) to 2026-10-25 (Sun)``."""
        self._load()
        days = [d for d in self._marker_days if d]
        if not days:
            return "unknown dates"
        return f"{_day_label(days[0])} to {_day_label(days[-1])}"


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


def load_irc_log(path: str) -> Optional[IrcLog]:
    """An :class:`IrcLog` for ``path``, or None when it's unset or missing."""
    if not path:
        return None
    if not Path(path).is_file():
        logger.warning(f"[irc log] IRC_LOG={path} isn't a file; the search_irc_log tool is off.")
        return None
    return IrcLog(path)
