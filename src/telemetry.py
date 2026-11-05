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
``[image/png, 412 KB]`` note, so a pasted screenshot doesn't put megabytes of base64 into every
span of a conversation.

The reminder and death-page pollers run every 15 seconds. Their spans are only kept when the
poll does something (logs, calls a model, announces); an idle poll's trace is dropped.
"""

import logging
import os
from typing import Any, Callable, Optional

import logfire
from logfire.sampling import SamplingOptions, TailSamplingSpanInfo

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
    if span is root:
        return 0.0
    scope = getattr(span.instrumentation_scope, "name", "") or ""
    if scope.startswith(_ROUTINE_SCOPES):
        # An idle poll's GET or Redis read; but a failing one is worth keeping.
        status = getattr(getattr(span, "status", None), "status_code", None)
        return 1.0 if getattr(status, "name", "") == "ERROR" else 0.0
    return 1.0


def routine_span(name: str, **attributes: Any):
    """A span for one iteration of a background poll: dropped unless something happens in it."""
    return logfire.span(name, **{ROUTINE_ATTRIBUTE: True}, **attributes)


# -- keeping images out of the spans ---------------------------------------------------------


def _blob_note(media_type: str, size: int) -> str:
    return f"[{media_type or 'binary'}, {size / 1024:.0f} KB]"


def _base64_size(data: Any) -> int:
    if isinstance(data, (bytes, bytearray)):
        return len(data)
    if isinstance(data, str):
        return len(data) * 3 // 4
    return 0


def without_blobs(blocks: Any) -> Any:
    """Content blocks (Messages API shape) with any base64 image or document swapped for a size note."""
    if not isinstance(blocks, list):
        return blocks
    cleaned = []
    for block in blocks:
        source = block.get("source") if isinstance(block, dict) else None
        if isinstance(source, dict) and source.get("type") == "base64":
            cleaned.append({"type": "text", "text": _blob_note(source.get("media_type", ""), _base64_size(source.get("data")))})
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
            note = _blob_note(converted.get("media_type", ""), _base64_size(converted.get("content")))
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
            return TextPart(content=_blob_note(converted.mime_type or "", _base64_size(converted.content)))
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
