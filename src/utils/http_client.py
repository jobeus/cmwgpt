"""
Instrumented HTTP clients for automatic API request logging.

Provides httpx transport wrappers that intercept every request/response
and log them to `api_request_logs` via `log_api_request`.  Works for both
async and sync httpx clients, and can be injected into the OpenAI and
Google GenAI SDKs so their underlying HTTP traffic is captured too.
"""

import base64
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from src.db.logger import log_api_request

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exclusion rules
# ---------------------------------------------------------------------------

# Requests to these domains are never logged.
EXCLUDED_DOMAINS: set[str] = {
    "cdn.discordapp.com",
    "media.discordapp.net",
    "images-ext-1.discordapp.net",
    "images-ext-2.discordapp.net",
    "wikimedia.org",
}

# Requests whose URL path matches any of these patterns are never logged.
EXCLUDED_PATH_PATTERNS: list[re.Pattern] = [
    re.compile(r"/Deaths_in_\d{4}", re.IGNORECASE),
]

# Content-types considered "text" (response body stored as-is).
_TEXT_CONTENT_TYPES = frozenset([
    "application/json",
    "application/xml",
    "application/x-www-form-urlencoded",
    "application/javascript",
])


def _is_excluded(url: str) -> bool:
    """Return True if this URL should NOT be logged."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()

    # Domain exclusion (exact or suffix match)
    for domain in EXCLUDED_DOMAINS:
        if hostname == domain or hostname.endswith("." + domain):
            return True

    # URL-path exclusion (regex)
    for pattern in EXCLUDED_PATH_PATTERNS:
        if pattern.search(parsed.path):
            return True

    return False


# ---------------------------------------------------------------------------
# Service-name inference
# ---------------------------------------------------------------------------

_DOMAIN_TO_SERVICE: Dict[str, str] = {
    "openrouter.ai": "openrouter",
    "api.openai.com": "openai",
    "api.groq.com": "groq",
    "api.runpod.ai": "runpod",
    "generativelanguage.googleapis.com": "gemini",
    "x-com2.p.rapidapi.com": "rapidapi/twitter",
    "paste.rs": "paste.rs",
}


def _infer_service_name(url: str) -> str:
    """Derive a short service name from the request URL."""
    hostname = (urlparse(url).hostname or "").lower()

    # Check exact matches first
    if hostname in _DOMAIN_TO_SERVICE:
        return _DOMAIN_TO_SERVICE[hostname]

    # Check suffix matches (e.g. *.p.rapidapi.com)
    for domain, name in _DOMAIN_TO_SERVICE.items():
        if hostname.endswith("." + domain):
            return name

    # Fallback: strip common prefixes
    for prefix in ("api.", "www."):
        if hostname.startswith(prefix):
            hostname = hostname[len(prefix):]
    return hostname or "unknown"


# ---------------------------------------------------------------------------
# Body encoding helpers
# ---------------------------------------------------------------------------

def _is_text_content_type(content_type: str) -> bool:
    """Return True if the content-type header indicates text/readable data."""
    ct = content_type.lower().split(";")[0].strip()
    if ct.startswith("text/"):
        return True
    if ct in _TEXT_CONTENT_TYPES:
        return True
    return False


def _encode_response_body(response: httpx.Response) -> str:
    """Encode the response body for DB storage.

    Text responses are stored as-is (UTF-8 decoded).
    Binary responses are stored as data: URLs so the content-type is
    preserved and the payload can be rendered or decoded directly.
    """
    content_type = response.headers.get("content-type", "")
    raw = response.content  # bytes, already read by this point

    if _is_text_content_type(content_type):
        return raw.decode("utf-8", errors="replace")

    # Binary → data: URL
    mime = content_type.split(";")[0].strip() or "application/octet-stream"
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _encode_request_body(request: httpx.Request) -> str:
    """Encode the request body for DB storage."""
    raw = request.content  # bytes
    if not raw:
        return ""

    # Multipart form data is binary – store as hex (same convention
    # the existing manual loggers already use for audio uploads).
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type.lower():
        return raw.hex()

    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Async transport wrapper
# ---------------------------------------------------------------------------

class LoggingTransport(httpx.AsyncBaseTransport):
    """Async httpx transport that logs every request/response to the DB."""

    def __init__(
        self,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        *,
        service_name: Optional[str] = None,
        **transport_kwargs: Any,
    ):
        self._transport = transport or httpx.AsyncHTTPTransport(**transport_kwargs)
        self._service_name_override = service_name

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)

        if _is_excluded(url):
            return await self._transport.handle_async_request(request)

        service_name = self._service_name_override or _infer_service_name(url)

        # Ensure request body is read
        await request.aread()

        try:
            response = await self._transport.handle_async_request(request)
            # Ensure response body is read so we can log it
            await response.aread()

            await log_api_request(
                service_name=service_name,
                method=request.method,
                endpoint_url=url,
                request_headers=dict(request.headers),
                request_body=_encode_request_body(request),
                response_status=response.status_code,
                response_headers=dict(response.headers),
                response_body=_encode_response_body(response),
            )
            return response

        except Exception as exc:
            # Log the failed request with the exception as the "response"
            await log_api_request(
                service_name=service_name,
                method=request.method,
                endpoint_url=url,
                request_headers=dict(request.headers),
                request_body=_encode_request_body(request),
                response_status=0,
                response_headers={},
                response_body=f"TRANSPORT_ERROR: {type(exc).__name__}: {exc}",
            )
            raise

    async def aclose(self) -> None:
        await self._transport.aclose()


# ---------------------------------------------------------------------------
# Sync transport wrapper
# ---------------------------------------------------------------------------

class LoggingSyncTransport(httpx.BaseTransport):
    """Sync httpx transport that buffers log data for later async flush.

    Because ``log_api_request`` is async but sync httpx transports run
    outside an event loop (often inside ``asyncio.to_thread``), we buffer
    the log entries and expose them via ``pending_logs``.  The caller is
    responsible for flushing them on the async side.
    """

    def __init__(
        self,
        transport: Optional[httpx.BaseTransport] = None,
        *,
        service_name: Optional[str] = None,
        **transport_kwargs: Any,
    ):
        self._transport = transport or httpx.HTTPTransport(**transport_kwargs)
        self._service_name_override = service_name
        self.pending_logs: List[Dict[str, Any]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)

        if _is_excluded(url):
            return self._transport.handle_request(request)

        service_name = self._service_name_override or _infer_service_name(url)

        # Ensure request body is read
        request.read()

        try:
            response = self._transport.handle_request(request)
            response.read()

            self.pending_logs.append({
                "service_name": service_name,
                "method": request.method,
                "endpoint_url": url,
                "request_headers": dict(request.headers),
                "request_body": _encode_request_body(request),
                "response_status": response.status_code,
                "response_headers": dict(response.headers),
                "response_body": _encode_response_body(response),
            })
            return response

        except Exception as exc:
            self.pending_logs.append({
                "service_name": service_name,
                "method": request.method,
                "endpoint_url": url,
                "request_headers": dict(request.headers),
                "request_body": _encode_request_body(request),
                "response_status": 0,
                "response_headers": {},
                "response_body": f"TRANSPORT_ERROR: {type(exc).__name__}: {exc}",
            })
            raise

    def close(self) -> None:
        self._transport.close()


async def flush_pending_logs(target: Any) -> None:
    """
    Flush buffered log entries to the DB.
    Target can be either a LoggingSyncTransport or a direct list of log dicts.
    """
    if hasattr(target, "pending_logs"):
        logs = target.pending_logs
    else:
        logs = target

    for entry in logs:
        await log_api_request(**entry)
    logs.clear()


# ---------------------------------------------------------------------------
# Client factories
# ---------------------------------------------------------------------------

def create_async_client(
    *,
    service_name: Optional[str] = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Create an ``httpx.AsyncClient`` with automatic request/response logging.

    All keyword arguments are forwarded to ``httpx.AsyncClient`` except
    ``transport``, which is wrapped by ``LoggingTransport``.
    """
    # Extract transport kwargs that should go to the underlying transport
    transport_kwargs = {}
    for key in ("verify", "cert", "trust_env", "proxy"):
        if key in kwargs:
            transport_kwargs[key] = kwargs.pop(key)

    user_transport = kwargs.pop("transport", None)
    wrapped = LoggingTransport(
        transport=user_transport,
        service_name=service_name,
        **transport_kwargs,
    )
    return httpx.AsyncClient(transport=wrapped, **kwargs)


def create_sync_client(
    *,
    service_name: Optional[str] = None,
    **kwargs: Any,
) -> httpx.Client:
    """Create an ``httpx.Client`` with automatic request/response logging.

    Returns a client whose transport buffers log entries.  After using the
    client, call ``flush_pending_logs(client._transport)`` from an async
    context to write them to the DB.

    The ``LoggingSyncTransport`` is accessible via ``client._transport``
    (httpx stores it there internally), or you can keep a reference to it.
    """
    transport_kwargs = {}
    for key in ("verify", "cert", "trust_env", "proxy"):
        if key in kwargs:
            transport_kwargs[key] = kwargs.pop(key)

    user_transport = kwargs.pop("transport", None)
    wrapped = LoggingSyncTransport(
        transport=user_transport,
        service_name=service_name,
        **transport_kwargs,
    )
    return httpx.Client(transport=wrapped, **kwargs)
