"""Safe fetching of user-supplied URLs.

Every redirect hop is re-checked against the SSRF guard (httpx would follow a
public URL's 302 to ``169.254.169.254`` without asking), bodies are capped so a
huge page or video can't exhaust memory, and the whole fetch gets one overall
deadline instead of a per-read timeout.
"""

import asyncio
import os
from typing import Any, Mapping, Optional
from urllib.parse import urljoin

import httpx

from src.utils.ssrf_guard import assert_public_url

MAX_REDIRECTS = 5
DEFAULT_MAX_BYTES = 20 * 1024 * 1024
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_WIRE_ENCODING_HEADERS = {"content-encoding", "content-length", "transfer-encoding"}


class ResponseTooLarge(Exception):
    """The response body exceeded the caller's byte limit."""


async def _read_capped(response: httpx.Response, max_bytes: int, sink: Any = None) -> bytes:
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise ResponseTooLarge(f"{response.url} declares {declared} bytes (limit {max_bytes})")

    chunks = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLarge(f"{response.url} exceeded {max_bytes} bytes")
        if sink is not None:
            sink.write(chunk)
        else:
            chunks.append(chunk)
    return b"".join(chunks)


async def _fetch(
    url: str,
    *,
    headers: Optional[Mapping[str, str]],
    params: Optional[Mapping[str, Any]],
    proxy: Optional[str],
    timeout: float,
    max_bytes: int,
    sink: Any = None,
) -> httpx.Response:
    async with httpx.AsyncClient(proxy=proxy, timeout=timeout, follow_redirects=False) as client:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            await assert_public_url(current)
            async with client.stream("GET", current, headers=headers, params=params) as response:
                location = response.headers.get("location")
                if response.status_code in _REDIRECT_STATUSES and location:
                    current = urljoin(str(response.url), location)
                    params = None  # already baked into the redirect target
                    continue
                content = await _read_capped(response, max_bytes, sink)
                # The body is already decoded, so drop the headers describing
                # the wire encoding or httpx would try to decompress it again.
                headers = [
                    (k, v) for k, v in response.headers.multi_items()
                    if k.lower() not in _WIRE_ENCODING_HEADERS
                ]
                return httpx.Response(
                    response.status_code,
                    headers=headers,
                    content=content,
                    request=response.request,
                )
        raise httpx.TooManyRedirects(f"More than {MAX_REDIRECTS} redirects for {url}")


async def fetch_public(
    url: str,
    *,
    headers: Optional[Mapping[str, str]] = None,
    params: Optional[Mapping[str, Any]] = None,
    proxy: Optional[str] = None,
    timeout: float = 15.0,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> httpx.Response:
    """GET a user-supplied URL, SSRF-checking every hop and capping the body.

    ``timeout`` bounds the whole fetch, redirects included. The returned
    response is fully read; call ``raise_for_status()`` as usual.
    """
    return await asyncio.wait_for(
        _fetch(url, headers=headers, params=params, proxy=proxy, timeout=timeout, max_bytes=max_bytes),
        timeout,
    )


async def download_public(
    url: str,
    path: str,
    *,
    headers: Optional[Mapping[str, str]] = None,
    timeout: float = 120.0,
    max_bytes: int = 100 * 1024 * 1024,
) -> None:
    """Stream a user-supplied URL to ``path`` under the same guards as :func:`fetch_public`.

    Raises on a non-2xx status; a partial file is removed on failure.
    """
    try:
        with open(path, "wb") as f:
            response = await asyncio.wait_for(
                _fetch(url, headers=headers, params=None, proxy=None, timeout=timeout,
                       max_bytes=max_bytes, sink=f),
                timeout,
            )
        response.raise_for_status()
    except BaseException:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
