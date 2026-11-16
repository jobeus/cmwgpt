"""
Giphy Service - GIF search for the ``search_gifs`` and ``post_gif`` chat tools.

Searches Giphy's API, and turns each hit into a filmstrip: 10 frames spread over the GIF, tiled
into one small JPEG, so the model can see what happens in it without being sent an animation.
"""

import asyncio
import io
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx
from PIL import Image

from src.utils.http_client import ResponseTooLarge, fetch_public

logger = logging.getLogger(__name__)

API_URL = "https://api.giphy.com/v1/gifs"
TIMEOUT = 10.0

# GIFs one search returns, each with a filmstrip.
MAX_SEARCH_GIFS = 5
# Giphy won't page past this offset.
MAX_OFFSET = 4999

# Frames in a filmstrip, how many sit in a row, and each one's width in pixels.
FILMSTRIP_FRAMES = 10
FILMSTRIP_COLUMNS = 5
FILMSTRIP_FRAME_WIDTH = 160
FILMSTRIP_QUALITY = 65
# The rendition filmstrips are cut from (200px wide), and the most of it that's downloaded.
PREVIEW_RENDITIONS = ("fixed_width", "fixed_height", "downsized", "original")
MAX_PREVIEW_BYTES = 8 * 1024 * 1024

# Renditions tried for posting, best first, and the biggest file posted (Discord takes 10 MB).
POST_RENDITIONS = ("original", "downsized_large", "downsized_medium", "downsized", "fixed_height")
MAX_POST_BYTES = 9 * 1024 * 1024

_GIF_ID_RE = re.compile(r"[A-Za-z0-9]{5,40}")


class GiphyError(Exception):
    """Giphy couldn't be reached or refused the request. The message is safe to show the model."""


@dataclass(frozen=True)
class Gif:
    id: str
    title: str
    # The GIF's page on giphy.com.
    url: str
    width: int
    height: int
    # (url, bytes) of each rendition worth posting, best first.
    renditions: Tuple[Tuple[str, int], ...]
    preview_url: str


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse(item: Dict[str, Any]) -> Optional[Gif]:
    images = item.get("images") or {}
    renditions = []
    for name in POST_RENDITIONS:
        rendition = images.get(name) or {}
        if rendition.get("url"):
            renditions.append((str(rendition["url"]), _int(rendition.get("size"))))
    preview = next((images[n]["url"] for n in PREVIEW_RENDITIONS if (images.get(n) or {}).get("url")), "")
    if not item.get("id") or not renditions or not preview:
        return None
    original = images.get("original") or {}
    return Gif(
        id=str(item["id"]),
        title=str(item.get("title") or "").strip() or "untitled",
        url=str(item.get("url") or f"https://giphy.com/gifs/{item['id']}"),
        width=_int(original.get("width")),
        height=_int(original.get("height")),
        renditions=tuple(renditions),
        preview_url=str(preview),
    )


async def _get(api_key: str, path: str, params: Dict[str, Any]) -> Any:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(f"{API_URL}/{path}", params={"api_key": api_key, **params})
    except httpx.HTTPError as e:
        raise GiphyError(f"Couldn't reach Giphy: {e.__class__.__name__}") from e
    if response.status_code == 404:
        return None
    if response.status_code == 429:
        raise GiphyError("Giphy's rate limit is used up for now. Try again later.")
    if response.status_code >= 400:
        raise GiphyError(f"Giphy answered HTTP {response.status_code}.")
    try:
        return response.json().get("data")
    except (ValueError, AttributeError) as e:
        raise GiphyError("Giphy sent something that isn't JSON.") from e


async def search_gifs(api_key: str, query: str, offset: int = 0, limit: int = MAX_SEARCH_GIFS) -> List[Gif]:
    """The GIFs matching ``query``, in Giphy's relevance order, starting ``offset`` in."""
    data = await _get(api_key, "search", {
        "q": query[:50], "limit": limit, "offset": max(0, min(offset, MAX_OFFSET)),
    })
    return [gif for gif in map(_parse, data or []) if gif]


async def get_gif(api_key: str, gif_id: str) -> Optional[Gif]:
    """One GIF by ID, or None when Giphy has no such GIF."""
    if not _GIF_ID_RE.fullmatch(gif_id):
        return None
    data = await _get(api_key, gif_id, {})
    return _parse(data) if isinstance(data, dict) else None


def filmstrip(gif_bytes: bytes) -> bytes:
    """Up to 10 frames spread evenly over a GIF, tiled left to right then top to bottom into one
    JPEG (CPU-bound; run in a thread). Raises when Pillow can't decode it."""
    img = Image.open(io.BytesIO(gif_bytes))
    total = getattr(img, "n_frames", 1)
    count = min(FILMSTRIP_FRAMES, total)
    indexes = [round(i * (total - 1) / (count - 1)) for i in range(count)] if count > 1 else [0]
    width = FILMSTRIP_FRAME_WIDTH
    height = max(1, round(img.height * width / img.width))
    columns = min(FILMSTRIP_COLUMNS, count)
    rows = -(-count // columns)
    sheet = Image.new("RGB", (columns * width, rows * height))
    for slot, index in enumerate(indexes):
        img.seek(index)
        frame = img.convert("RGB").resize((width, height), Image.Resampling.BILINEAR)
        sheet.paste(frame, ((slot % columns) * width, (slot // columns) * height))
    out = io.BytesIO()
    sheet.save(out, format="JPEG", quality=FILMSTRIP_QUALITY)
    return out.getvalue()


async def gif_filmstrip(gif: Gif) -> Optional[bytes]:
    """``gif``'s filmstrip JPEG, or None when its preview can't be downloaded or decoded."""
    try:
        response = await fetch_public(gif.preview_url, timeout=TIMEOUT, max_bytes=MAX_PREVIEW_BYTES)
        response.raise_for_status()
        return await asyncio.to_thread(filmstrip, response.content)
    except Exception as e:
        logger.warning("No filmstrip for GIF %s: %s: %s", gif.id, type(e).__name__, e)
        return None


async def download_gif(gif: Gif) -> bytes:
    """The best rendition of ``gif`` small enough to post. Raises ``GiphyError`` when none is."""
    for url, size in gif.renditions:
        if size > MAX_POST_BYTES:
            continue
        try:
            response = await fetch_public(url, timeout=20.0, max_bytes=MAX_POST_BYTES)
            response.raise_for_status()
            return response.content
        except ResponseTooLarge:
            continue
        except (httpx.HTTPError, asyncio.TimeoutError) as e:
            raise GiphyError(f"Couldn't download that GIF: {e.__class__.__name__}") from e
    raise GiphyError("Every version of that GIF is too big to post. Pick another.")
