"""Tracing and logs through Pydantic Logfire (OpenTelemetry).

:func:`setup_telemetry` runs once at startup (``main.py``). With a Logfire write token in
``LOGFIRE_TOKEN`` (or ``LOGFIRE_API_KEY``) it sends:

- a span per Discord REST call (discord.py's aiohttp requests), with Discord's rate-limit headers,
- a span per Claude and Gemini call, with the prompt, reply, tool calls, tokens and model,
- a span per outgoing httpx request (page fetches, Wikipedia, Polymarket, ...), with its headers and,
  when small, its bodies, and a span per Redis command
  (the whole command with values cut short, and a preview of the reply),
- the bot's own spans: each answered mention (with its cost and usage stats), each tool call,
  each slash command, the nightly memory job, a death announcement, a delivered reminder,
- every log record at INFO and above from the ``logging`` module.

Without a token nothing is sent; the bot's own spans still run but go nowhere.

Images and documents sent to (or drawn by) the models are replaced in the spans by a short
``[image/png, 412 KB: https://paste.jobe.wtf/abc.png]`` note, so a pasted screenshot doesn't put
megabytes of base64 into every span of a conversation. PNGs and JPEGs are uploaded to
paste.jobe.wtf for the link: :func:`start_image_upload` starts that as soon as an image comes in,
and :func:`image_links_ready` waits for it (briefly) before a model call, because the span's note is
written the moment the request starts. Links are kept in Redis, so a restart doesn't upload again.

The reminder and death-page pollers run every 15 seconds. Their spans are only kept when the
poll does something (logs, calls a model, announces); an idle poll's trace is dropped.
"""

import asyncio
import base64
import binascii
import hashlib
import logging
import os
import re
import time
from typing import Any, Awaitable, Callable, Dict, Iterator, Optional, Sequence, Tuple

import logfire
from logfire.sampling import SamplingOptions, TailSamplingSpanInfo

from src.utils.cache_utils import LRUCache, PersistentCache

logger = logging.getLogger(__name__)

SERVICE_NAME = "cmwgpt"
# Set on the root span of a routine poll, so tail sampling can drop the idle ones.
ROUTINE_ATTRIBUTE = "cmwgpt.routine"
# Spans from these libraries don't make a routine poll's trace worth keeping on their own.
_ROUTINE_SCOPES = (
    "opentelemetry.instrumentation.redis",
    "opentelemetry.instrumentation.httpx",
)

_configured = False


def telemetry_token(env: Optional[dict] = None) -> str:
    source = os.environ if env is None else env
    return (source.get("LOGFIRE_TOKEN") or source.get("LOGFIRE_API_KEY") or "").strip()


def _environment(env_file: Optional[str]) -> str:
    explicit = os.environ.get("LOGFIRE_ENVIRONMENT", "").strip()
    if explicit:
        return explicit
    return "development" if env_file and "development" in os.path.basename(env_file) else "production"


def keep_trace(info: TailSamplingSpanInfo) -> float:
    """Tail sampling: keep every trace except a routine poll that did nothing worth seeing."""
    root = info.buffer.first_span
    if not (root.attributes or {}).get(ROUTINE_ATTRIBUTE):
        return 1.0
    span = info.span
    # Compared by id: when a span ends, the callback gets a new object for it.
    if _span_id(span) == _span_id(root):
        return 0.0
    scope = getattr(span.instrumentation_scope, "name", "") or ""
    if scope.startswith(_ROUTINE_SCOPES):
        # An idle poll's GET or Redis read; but a failing one is worth keeping.
        status = getattr(getattr(span, "status", None), "status_code", None)
        return 1.0 if getattr(status, "name", "") == "ERROR" else 0.0
    return 1.0


def _span_id(span: Any) -> Optional[int]:
    context = span.get_span_context() if hasattr(span, "get_span_context") else getattr(span, "context", None)
    return getattr(context, "span_id", None)


def routine_span(name: str, **attributes: Any):
    """A span for one iteration of a background poll: dropped unless something happens in it."""
    return logfire.span(name, **{ROUTINE_ATTRIBUTE: True}, **attributes)


# -- keeping images out of the spans ---------------------------------------------------------

# Images go to paste.jobe.wtf so a span's note can link to them. Only these types are accepted there.
UPLOADABLE_TYPES = {"image/png", "image/jpeg"}
# How long a model call waits for its images' uploads before going ahead without the links.
LINK_WAIT_SECONDS = 5.0

_sending = False
_uploader: Optional[Callable[[bytes, str], Awaitable[str]]] = None
_links: LRUCache[str] = LRUCache(2000)
_pending: Dict[str, "asyncio.Task[None]"] = {}
# When an image's upload may be tried again after failing. Without it, an image the paste
# service won't take is re-uploaded (and waited on) by every model call that includes it.
UPLOAD_RETRY_SECONDS = 600
_retry_at: LRUCache[float] = LRUCache(2000)
_link_store: Optional[PersistentCache] = None


def set_image_uploader(uploader: Optional[Callable[[bytes, str], Awaitable[str]]]) -> None:
    """``uploader(data, media_type)`` returns the image's URL. Only used while tracing is on."""
    global _uploader
    _uploader = uploader


def _raw_bytes(data: Any) -> bytes:
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, str):
        try:
            return base64.b64decode(data, validate=False)
        except (binascii.Error, ValueError):
            return b""
    return b""


def _image_key(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _blob_note(media_type: str, data: Any) -> str:
    raw = _raw_bytes(data)
    size = f"{len(raw) / 1024:.0f} KB" if len(raw) >= 1024 else f"{len(raw)} B"
    note = f"{media_type or 'binary'}, {size}"
    link = _links.get(_image_key(raw)) if raw else None
    return f"[{note}: {link}]" if link else f"[{note}]"


async def _upload(key: str, raw: bytes, media_type: str) -> None:
    global _link_store
    try:
        if _link_store is None:
            _link_store = PersistentCache("paste_image_links")
        link = await _link_store.get(key)
        if not link and _uploader is not None:
            link = await _uploader(raw, media_type)
            await _link_store.set(key, link)
        if link:
            _links[key] = link
    except Exception as e:
        logger.warning("[telemetry] Couldn't upload a %s for its trace link: %s", media_type, e)
        _retry_at[key] = time.monotonic() + UPLOAD_RETRY_SECONDS
    finally:
        _pending.pop(key, None)


def start_image_upload(data: Any, media_type: str) -> "Optional[asyncio.Task[None]]":
    """Start uploading an image (base64 or bytes) for the span notes, unless it's already up.

    Returns the upload to wait on, or None. Does nothing while tracing is off.
    """
    if not (_sending and _uploader) or (media_type or "").lower() not in UPLOADABLE_TYPES:
        return None
    raw = _raw_bytes(data)
    if not raw:
        return None
    key = _image_key(raw)
    if key in _links or time.monotonic() < (_retry_at.get(key) or 0.0):
        return None
    task = _pending.get(key)
    if task is None:
        task = asyncio.create_task(_upload(key, raw, media_type.lower()))
        _pending[key] = task
    return task


def _base64_sources(value: Any) -> Iterator[Dict[str, Any]]:
    """Every ``{"type": "base64", ...}`` source in a Messages API request, tool results included."""
    if isinstance(value, dict):
        if value.get("type") == "base64" and "data" in value:
            yield value
        for item in value.values():
            if isinstance(item, (dict, list)):
                yield from _base64_sources(item)
    elif isinstance(value, list):
        for item in value:
            yield from _base64_sources(item)


async def image_links_ready(value: Any = None, *, images: Sequence[Tuple[Any, str]] = ()) -> None:
    """Upload the images in ``value`` (Messages API content) and ``images`` (data, media type) pairs,
    waiting up to ``LINK_WAIT_SECONDS`` so the model call's span can link to them. Never raises."""
    if not (_sending and _uploader):
        return
    try:
        found = [(s.get("data"), s.get("media_type", "")) for s in _base64_sources(value)]
        tasks = {t for data, media_type in [*found, *images] if (t := start_image_upload(data, media_type))}
        if tasks:
            await asyncio.wait(tasks, timeout=LINK_WAIT_SECONDS)
    except Exception as e:
        logger.warning("[telemetry] Couldn't get image links ready: %s", e)


def without_blobs(blocks: Any) -> Any:
    """Content blocks (Messages API shape) with any base64 image or document swapped for a note."""
    if not isinstance(blocks, list):
        return blocks
    cleaned = []
    for block in blocks:
        source = block.get("source") if isinstance(block, dict) else None
        if isinstance(source, dict) and source.get("type") == "base64":
            cleaned.append({"type": "text", "text": _blob_note(source.get("media_type", ""), source.get("data"))})
        else:
            cleaned.append(block)
    return cleaned


def _wrap_anthropic_parts() -> bool:
    """Swap base64 image/document parts in Claude spans for a size note."""
    try:
        from logfire._internal.integrations.llm_providers import anthropic as module
    except ImportError:
        return False
    original: Optional[Callable[[object], Any]] = getattr(module, "_convert_content_part", None)
    if original is None or getattr(original, "_cmwgpt_wrapped", False):
        return original is not None

    def convert(part: object) -> Any:
        converted = original(part)
        if isinstance(converted, dict) and converted.get("type") == "blob":
            note = _blob_note(converted.get("media_type", ""), converted.get("content"))
            return {"type": "text", "content": note}
        return converted

    convert._cmwgpt_wrapped = True  # type: ignore[attr-defined]
    module._convert_content_part = convert
    return True


def _wrap_gemini_parts() -> bool:
    """Swap inline image parts (sent or generated) in Gemini spans for a size note."""
    try:
        from opentelemetry.instrumentation.google_genai import message as module
        from opentelemetry.util.genai.types import BlobPart, TextPart
    except ImportError:
        return False
    original = getattr(module, "_to_part", None)
    if original is None or getattr(original, "_cmwgpt_wrapped", False):
        return original is not None

    def to_part(part: Any, idx: int) -> Any:
        converted = original(part, idx)
        if isinstance(converted, BlobPart):
            return TextPart(content=_blob_note(converted.mime_type or "", converted.content))
        return converted

    to_part._cmwgpt_wrapped = True  # type: ignore[attr-defined]
    module._to_part = to_part
    return True


# -- scrubbing: secrets only ---------------------------------------------------------------------

# Logfire's default scrubbing blanks any value that merely mentions "session", "cookie", "auth",
# "secret" and the like, which would hide chat text that says those words. Fields *named* like a
# secret (an Authorization or API-key header) are still redacted, and so is any value that looks
# like an actual key.
# Lookaheads keep each match to the key's prefix: Logfire puts the matched text in its redaction note.
_SECRET_VALUE = re.compile(
    r"pylf_v\d+_|sk-ant-(?=[\w-]{10})|AIza(?=[\w-]{30})|\bBearer\s(?=\s*[\w.~+/=-]{16})|xox[abpr]-(?=[\w-]{10})"
    r"|gh[pousr]_(?=\w{20})|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)


def scrub_secrets_only(match: Any) -> Any:
    """``ScrubbingOptions.callback``: keep a value whose *text* tripped a pattern, unless it looks like a key.

    Returning the value keeps it; returning None lets Logfire redact it.
    """
    value = match.value
    searched = match.pattern_match.string
    if isinstance(value, str) and searched == value and not _SECRET_VALUE.search(value):
        return value
    return None


# -- HTTP: headers always, bodies when small -------------------------------------------------------

# Bodies are kept only when the Content-Length is known and at most this, so page fetches, the deaths
# page and Gemini requests carrying images stay out. Streamed downloads are never captured.
HTTP_BODY_LIMIT = 16 * 1024


def _small_body(headers: Any) -> bool:
    length = (headers.get("content-length") or "").strip() if headers is not None else ""
    return length.isdigit() and 0 < int(length) <= HTTP_BODY_LIMIT


def _httpx_request_hook(span: Any, request: Any) -> None:
    if _small_body(getattr(request, "headers", None)) and hasattr(request, "capture_body"):
        request.capture_body()


def _httpx_response_hook(span: Any, request: Any, response: Any) -> None:
    if _small_body(getattr(response, "headers", None)) and hasattr(response, "capture_body_if_text"):
        response.capture_body_if_text()


async def _httpx_async_request_hook(span: Any, request: Any) -> None:
    _httpx_request_hook(span, request)


async def _httpx_async_response_hook(span: Any, request: Any, response: Any) -> None:
    _httpx_response_hook(span, request, response)


def instrument_gemini() -> None:
    """Trace the Gemini SDK, unless its instrumentation doesn't support the installed version.

    The bot always runs the newest google-genai, and the instrumentation can lag a major
    version behind. It then instruments nothing and logs an error; this says so once, as a
    warning. Gemini's requests are still traced as HTTP calls, and tracing comes back by
    itself when a release that supports the new version is installed.
    """
    try:
        from opentelemetry.instrumentation.dependencies import get_dependency_conflicts
        from opentelemetry.instrumentation.google_genai import GoogleGenAiSdkInstrumentor

        conflict = get_dependency_conflicts(GoogleGenAiSdkInstrumentor().instrumentation_dependencies())
    except Exception:
        conflict = None  # can't tell; let Logfire try
    if conflict:
        logger.warning("[telemetry] Gemini SDK calls aren't traced until its instrumentation catches up: %s", conflict)
        return
    logfire.instrument_google_genai()


def instrument_http() -> None:
    logfire.instrument_httpx(
        capture_headers=True,
        request_hook=_httpx_request_hook,
        response_hook=_httpx_response_hook,
        async_request_hook=_httpx_async_request_hook,
        async_response_hook=_httpx_async_response_hook,
    )


# -- Redis: whole commands and their replies ----------------------------------------------------

# The OpenTelemetry Redis instrumentation records ``GETEX ? ? ?``. The bot's Redis holds nothing
# private, so the spans get the real keys and values, each value cut short (cached pages and
# base64 images run to megabytes), plus a preview of the reply.
REDIS_VALUE_CHARS = 200
REDIS_STATEMENT_CHARS = 4000


def _short(value: Any, limit: int = REDIS_VALUE_CHARS) -> str:
    if isinstance(value, (bytes, bytearray)):
        text = bytes(value[: limit * 4]).decode("utf-8", errors="replace")
        total = len(value)
    else:
        text = str(value)
        total = len(text)
    if total > limit:
        return f"{text[:limit]}…(+{total - limit} more)"
    return text


def format_redis_command(args: Sequence[Any]) -> str:
    """``GETEX cmwgpt:cache:pages:https://… EX 2592000``, values cut at ``REDIS_VALUE_CHARS``."""
    return _short(" ".join(_short(arg) for arg in args), REDIS_STATEMENT_CHARS)


def redis_reply_preview(response: Any) -> str:
    """A short, readable form of a Redis reply: ``nil`` for a miss, else the value (cut short)."""
    if response is None:
        return "nil"
    if isinstance(response, (list, tuple)):
        items = [redis_reply_preview(item) for item in response[:20]]
        more = f", …(+{len(response) - 20} more)" if len(response) > 20 else ""
        return _short("[" + ", ".join(items) + more + "]", 1000)
    if isinstance(response, dict):
        items = [f"{_short(k, 80)}: {redis_reply_preview(v)}" for k, v in list(response.items())[:20]]
        more = f", …(+{len(response) - 20} more)" if len(response) > 20 else ""
        return _short("{" + ", ".join(items) + more + "}", 1000)
    return _short(response)


def _redis_response_hook(span: Any, instance: Any, response: Any) -> None:
    if not span.is_recording():
        return
    span.set_attribute("db.redis.reply", redis_reply_preview(response))
    if response is None or isinstance(response, (list, tuple, dict, bytes, bytearray, str)):
        span.set_attribute("db.redis.reply_size", 0 if response is None else len(response))


def capture_redis_commands() -> bool:
    """Make the Redis spans record whole commands (single and pipelined), not ``? ? ?``."""
    try:
        import opentelemetry.instrumentation.redis as instrumentation
        from opentelemetry.instrumentation.redis import util
    except ImportError:
        return False
    hooked = False
    for module in (instrumentation, util):
        if hasattr(module, "_format_command_args"):
            module._format_command_args = format_redis_command  # type: ignore[attr-defined]
            hooked = True
    if not hooked:
        logger.warning("[telemetry] Couldn't hook the Redis spans; commands stay as ? placeholders")
    return hooked


def strip_images_from_spans() -> None:
    """Install the image filters. Logs a warning if a Logfire upgrade moved the hook."""
    for name, wrap in (("Claude", _wrap_anthropic_parts), ("Gemini", _wrap_gemini_parts)):
        try:
            if not wrap():
                logger.warning("[telemetry] Couldn't hook %s spans; images will be sent in full", name)
        except Exception as e:
            logger.warning("[telemetry] Couldn't hook %s spans (%s); images will be sent in full", name, e)


# -- slash commands ----------------------------------------------------------------------------


def instrument_command_tree(tree: Any) -> None:
    """Wrap each slash command or context-menu call in a span (discord.py has no hook around them)."""
    original = getattr(tree, "_call", None)
    if original is None or getattr(original, "_cmwgpt_wrapped", False):
        return

    async def call(interaction: Any) -> None:
        data = getattr(interaction, "data", None) or {}
        command = data.get("name", "?") if isinstance(data, dict) else "?"
        channel = getattr(interaction, "channel", None)
        user = getattr(interaction, "user", None)
        with logfire.span(
            "/{command} by {user}",
            command=command,
            user=getattr(user, "name", None),
            user_id=getattr(user, "id", None),
            channel=getattr(channel, "name", None),
            channel_id=getattr(interaction, "channel_id", None),
            guild_id=getattr(interaction, "guild_id", None),
            options=data.get("options") if isinstance(data, dict) else None,
        ):
            await original(interaction)

    call._cmwgpt_wrapped = True  # type: ignore[attr-defined]
    tree._call = call


# -- setup -------------------------------------------------------------------------------------


def setup_telemetry(env_file: Optional[str] = None) -> bool:
    """Configure Logfire and instrument the libraries. Returns whether data is being sent."""
    global _configured
    if _configured:
        return bool(telemetry_token())
    _configured = True

    global _sending
    token = telemetry_token()
    if not token:
        # Keeps logfire.span() quiet and cheap; nothing is exported.
        logfire.configure(send_to_logfire=False, console=False, metrics=False)
        logger.info("[telemetry] No LOGFIRE_TOKEN; tracing is off")
        return False

    logfire.configure(
        token=token,
        service_name=SERVICE_NAME,
        environment=_environment(env_file),
        console=False,
        sampling=SamplingOptions(tail=keep_trace),
        scrubbing=logfire.ScrubbingOptions(callback=scrub_secrets_only, extra_patterns=[_SECRET_VALUE.pattern]),
        # Lets Logfire link each span to its line on GitHub.
        code_source=logfire.CodeSource(repository="https://github.com/jobeus/cmwgpt", revision="main"),
    )

    strip_images_from_spans()
    capture_redis_commands()
    _sending = True
    # Gemini only records prompts and replies when asked to.
    os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "SPAN_ONLY")

    for name, instrument in (
        ("anthropic", logfire.instrument_anthropic),
        ("google-genai", instrument_gemini),
        ("httpx", instrument_http),
        # discord.py talks to Discord's REST API through aiohttp: every send, edit, reaction and
        # history fetch, with Discord's X-RateLimit-* headers. The bot token header is scrubbed.
        ("aiohttp client (Discord API)", lambda: logfire.instrument_aiohttp_client(capture_headers=True)),
        ("redis", lambda: logfire.instrument_redis(response_hook=_redis_response_hook)),
        ("system metrics", logfire.instrument_system_metrics),
    ):
        try:
            instrument()
        except Exception as e:
            logger.warning("[telemetry] Couldn't instrument %s: %s", name, e)

    # Everything the bot logs at INFO and above, attached to whatever span is current.
    handler = logfire.LogfireLoggingHandler(level=logging.INFO)
    logging.getLogger().addHandler(handler)

    logger.info("[telemetry] Sending traces and logs to Logfire (%s)", _environment(env_file))
    return True
