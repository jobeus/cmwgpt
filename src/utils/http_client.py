"""Safe fetching of user-supplied URLs.

Every redirect hop is re-checked against the SSRF guard (httpx would follow a
public URL's 302 to ``169.254.169.254`` without asking), bodies are capped so a
huge page or video can't exhaust memory, and the whole fetch gets one overall
deadline instead of a per-read timeout.

The check and the connection use the same DNS answer: the socket is opened to an
address the guard just checked, never to a second lookup of the host name. Without
that, a host could answer the check with a public address and the connection with
``127.0.0.1`` (DNS rebinding). Through a proxy the proxy does the lookup, so there
the check before each hop is all there is.
"""

import asyncio
import os
import socket
import urllib.request
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import urljoin

import httpcore
import httpx

from src.utils.ssrf_guard import assert_public_url, public_addresses

MAX_REDIRECTS = 5
DEFAULT_MAX_BYTES = 20 * 1024 * 1024
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_WIRE_ENCODING_HEADERS = {"content-encoding", "content-length", "transfer-encoding"}


class ResponseTooLarge(Exception):
    """The response body exceeded the caller's byte limit."""


class _PublicOnlyBackend(httpcore.AsyncNetworkBackend):
    """Opens connections only to addresses the SSRF guard checked."""

    def __init__(self) -> None:
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: Optional[float] = None,
        local_address: Optional[str] = None,
        socket_options: Optional[Iterable[Any]] = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            addresses = await public_addresses(host)
        except socket.gaierror as e:
            raise httpcore.ConnectError(f"Couldn't resolve {host}: {e}") from e
        if not addresses:
            raise httpcore.ConnectError(f"{host} has no addresses")
        # TLS still verifies the certificate against the host name: only the socket is pinned.
        for address in addresses[:-1]:
            try:
                return await self._inner.connect_tcp(address, port, timeout, local_address, socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout):
                continue  # e.g. an IPv6 address on a machine without IPv6
        return await self._inner.connect_tcp(addresses[-1], port, timeout, local_address, socket_options)

    async def connect_unix_socket(self, path: str, timeout: Optional[float] = None,
                                  socket_options: Optional[Iterable[Any]] = None) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("Unix sockets aren't public addresses")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class _PublicOnlyTransport(httpx.AsyncHTTPTransport):
    """httpx's transport, connecting through ``_PublicOnlyBackend``."""

    def __init__(self) -> None:
        super().__init__()
        # httpx doesn't take a network backend, so swap the pool it built for one that does.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(), network_backend=_PublicOnlyBackend())


def _transport(proxy: Optional[str]) -> Optional[httpx.AsyncBaseTransport]:
    """The pinned transport, or None when a proxy (given or from the environment) connects for us."""
    if proxy or urllib.request.getproxies():
        return None
    return _PublicOnlyTransport()


async def _read_capped(response: httpx.Response, max_bytes: Optional[int], sink: Any = None) -> bytes:
    declared = response.headers.get("content-length")
    if max_bytes is not None and declared and declared.isdigit() and int(declared) > max_bytes:
        raise ResponseTooLarge(f"{response.url} declares {declared} bytes (limit {max_bytes})")

    chunks = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if max_bytes is not None and total > max_bytes:
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
    max_bytes: Optional[int],
    sink: Any = None,
) -> httpx.Response:
    async with httpx.AsyncClient(
            proxy=proxy, timeout=timeout, follow_redirects=False, transport=_transport(proxy)) as client:
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
                decoded_headers = [
                    (k, v) for k, v in response.headers.multi_items()
                    if k.lower() not in _WIRE_ENCODING_HEADERS
                ]
                return httpx.Response(
                    response.status_code,
                    headers=decoded_headers,
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
    max_bytes: Optional[int] = None,
    timeout: float = 180.0,
) -> None:
    """Stream a user-supplied URL to ``path``, SSRF-checking every redirect hop.

    No size limit by default (videos can be hours long); ``timeout`` bounds
    the whole download. Raises on a non-2xx status; a partial file is removed
    on failure.
    """
    try:
        with open(path, "wb") as f:
            response = await asyncio.wait_for(
                _fetch(url, headers=headers, params=None, proxy=None,
                       timeout=60.0, max_bytes=max_bytes, sink=f),
                timeout,
            )
        response.raise_for_status()
    except BaseException:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
