"""Guard against SSRF when fetching user-supplied URLs server-side.

Resolves the target host and rejects URLs that point at loopback, private
(RFC1918), link-local, multicast, or unspecified addresses, so a user-supplied
link can't be used to probe the local network or cloud metadata endpoints.
Public URLs pass through unchanged.
"""

import asyncio
import ipaddress
import logging
import socket
from typing import List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _blocked_reason(addr) -> Optional[str]:
    """Return why an address is non-public, or None if it is fine to fetch."""
    if addr.is_loopback:
        return "loopback"
    if addr.is_link_local:
        return "link-local"
    if addr.is_private:
        return "private"
    if addr.is_multicast:
        return "multicast"
    if addr.is_unspecified:
        return "unspecified"
    return None


def _resolve_addresses(host: str) -> List:
    """Return all IP addresses for a host (literal IPs short-circuit DNS)."""
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass

    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    addresses = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    return addresses


def assert_public_url_sync(url: str) -> None:
    """Raise ValueError if ``url`` resolves to a non-public address.

    DNS resolution failures do not raise: the subsequent fetch will fail on
    its own, and we don't want transient DNS errors blocking real URLs.
    """
    parsed = urlparse(url if '://' in url else f'http://{url}')
    host = parsed.hostname
    if not host:
        raise ValueError(f"Cannot determine host for URL: {url}")

    try:
        addresses = _resolve_addresses(host)
    except socket.gaierror as e:
        logger.debug(f"DNS resolution failed during SSRF check for {host}: {e}")
        return

    for addr in addresses:
        reason = _blocked_reason(addr)
        if reason:
            raise ValueError(
                f"Refusing to fetch non-public URL {url}: host {host} resolves to {reason} address {addr}"
            )


async def assert_public_url(url: str) -> None:
    """Async wrapper around assert_public_url_sync; DNS runs in a thread."""
    await asyncio.to_thread(assert_public_url_sync, url)
