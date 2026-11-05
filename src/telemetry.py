"""Tracing and logs through Pydantic Logfire (OpenTelemetry).

:func:`setup_telemetry` runs once at startup (``main.py``). With a Logfire write token in
``LOGFIRE_TOKEN`` (or ``LOGFIRE_API_KEY``) it sends:

- a span per Claude and Gemini call, with the prompt, reply, tool calls, tokens and model,
- a span per outgoing httpx request (page fetches, Wikipedia, Polymarket, ...) and Redis command,
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
        logger.warning(f"[telemetry] Couldn't upload a {media_type} for its trace link: {e}")
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
    if key in _links:
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
        logger.warning(f"[telemetry] Couldn't get image links ready: {e}")


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


def strip_images_from_spans() -> None:
    """Install the image filters. Logs a warning if a Logfire upgrade moved the hook."""
    for name, wrap in (("Claude", _wrap_anthropic_parts), ("Gemini", _wrap_gemini_parts)):
        try:
            if not wrap():
                logger.warning(f"[telemetry] Couldn't hook {name} spans; images will be sent in full")
        except Exception as e:
            logger.warning(f"[telemetry] Couldn't hook {name} spans ({e}); images will be sent in full")


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
        # Lets Logfire link each span to its line on GitHub.
        code_source=logfire.CodeSource(repository="https://github.com/jobeus/cmwgpt", revision="main"),
    )

    strip_images_from_spans()
    _sending = True
    # Gemini only records prompts and replies when asked to.
    os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "SPAN_ONLY")

    for name, instrument in (
        ("anthropic", logfire.instrument_anthropic),
        ("google-genai", logfire.instrument_google_genai),
        ("httpx", logfire.instrument_httpx),
        ("redis", logfire.instrument_redis),
        ("system metrics", logfire.instrument_system_metrics),
    ):
        try:
            instrument()
        except Exception as e:
            logger.warning(f"[telemetry] Couldn't instrument {name}: {e}")

    # Everything the bot logs at INFO and above, attached to whatever span is current.
    handler = logfire.LogfireLoggingHandler(level=logging.INFO)
    logging.getLogger().addHandler(handler)

    logger.info(f"[telemetry] Sending traces and logs to Logfire ({_environment(env_file)})")
    return True
