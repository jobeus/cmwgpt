"""Tests for src.telemetry: image stripping, poll sampling and the slash-command span."""

import base64
from types import SimpleNamespace

import pytest

from src import telemetry
from src.telemetry import (
    ROUTINE_ATTRIBUTE,
    instrument_command_tree,
    keep_trace,
    telemetry_token,
    without_blobs,
)

IMAGE_B64 = base64.b64encode(b"\0" * 300_000).decode()


def test_token_prefers_logfire_token_then_api_key():
    assert telemetry_token({"LOGFIRE_TOKEN": "a", "LOGFIRE_API_KEY": "b"}) == "a"
    assert telemetry_token({"LOGFIRE_API_KEY": " b "}) == "b"
    assert telemetry_token({}) == ""


def test_environment_from_env_file(monkeypatch):
    monkeypatch.delenv("LOGFIRE_ENVIRONMENT", raising=False)
    assert telemetry._environment("/app/.env.development") == "development"
    assert telemetry._environment("/app/.env.production") == "production"
    monkeypatch.setenv("LOGFIRE_ENVIRONMENT", "staging")
    assert telemetry._environment("/app/.env.production") == "staging"


def test_anthropic_spans_get_a_note_instead_of_the_image():
    from logfire._internal.integrations.llm_providers import anthropic as module

    assert telemetry._wrap_anthropic_parts()
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": IMAGE_B64}}
    assert module._convert_content_part(image) == {"type": "text", "content": "[image/png, 293 KB]"}
    assert module._convert_content_part({"type": "text", "text": "hi"})["content"] == "hi"
    # Images inside a tool result go through the same function.
    parts = module._convert_content_part_or_parts([image])
    assert "AAAA" not in str(parts)
    # Wrapping twice doesn't stack.
    assert telemetry._wrap_anthropic_parts()


def test_gemini_spans_get_a_note_instead_of_the_image():
    from google.genai import types as genai_types
    from opentelemetry.instrumentation.google_genai import message as module

    assert telemetry._wrap_gemini_parts()
    part = genai_types.Part(inline_data=genai_types.Blob(mime_type="image/jpeg", data=b"\0" * 2048))
    converted = module._to_part(part, 0)
    assert converted.content == "[image/jpeg, 2 KB]"
    assert module._to_part(genai_types.Part(text="hi"), 0).content == "hi"


def test_without_blobs_replaces_base64_blocks_only():
    blocks = [
        {"type": "text", "text": "drew it"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": IMAGE_B64}},
        {"type": "image", "source": {"type": "url", "url": "https://example.com/x.png"}},
    ]
    cleaned = without_blobs(blocks)
    assert cleaned[0] == blocks[0]
    assert cleaned[1] == {"type": "text", "text": "[image/png, 293 KB]"}
    assert cleaned[2] == blocks[2]
    assert without_blobs("plain") == "plain"


_next_span_id = iter(range(1, 10_000))


def _span(name, *, attributes=None, scope="logfire", error=False, span_id=None):
    status = SimpleNamespace(status_code=SimpleNamespace(name="ERROR" if error else "UNSET"))
    return SimpleNamespace(
        name=name, attributes=attributes or {}, instrumentation_scope=SimpleNamespace(name=scope), status=status,
        context=SimpleNamespace(span_id=span_id or next(_next_span_id)),
    )


def _info(span, root):
    return SimpleNamespace(span=span, buffer=SimpleNamespace(first_span=root))


def test_keep_trace_keeps_everything_that_isnt_a_routine_poll():
    root = _span("mention by x")
    assert keep_trace(_info(root, root)) == 1.0
    assert keep_trace(_info(_span("GET", scope="opentelemetry.instrumentation.httpx"), root)) == 1.0


def test_keep_trace_drops_an_idle_poll_and_keeps_a_busy_one():
    root = _span("reminder poll", attributes={ROUTINE_ATTRIBUTE: True})
    assert keep_trace(_info(root, root)) == 0.0
    # The root ending arrives as a different object with the same span id.
    ended_root = _span("reminder poll", attributes={ROUTINE_ATTRIBUTE: True}, span_id=root.context.span_id)
    assert keep_trace(_info(ended_root, root)) == 0.0
    assert keep_trace(_info(_span("ZRANGEBYSCORE", scope="opentelemetry.instrumentation.redis"), root)) == 0.0
    # A failed GET, a log line or a model call makes it worth keeping.
    assert keep_trace(_info(_span("GET", scope="opentelemetry.instrumentation.httpx", error=True), root)) == 1.0
    assert keep_trace(_info(_span("[reminder] Posted ..."), root)) == 1.0


@pytest.mark.asyncio
async def test_command_tree_call_is_wrapped_once():
    calls = []

    class Tree:
        async def _call(self, interaction):
            calls.append(interaction)

    tree = Tree()
    instrument_command_tree(tree)
    wrapped = tree._call
    instrument_command_tree(tree)
    assert tree._call is wrapped

    interaction = SimpleNamespace(
        data={"name": "cost", "options": []}, channel=SimpleNamespace(name="general"),
        user=SimpleNamespace(name="jobe", id=1), channel_id=2, guild_id=3,
    )
    await tree._call(interaction)
    assert calls == [interaction]


class _MemoryStore:
    def __init__(self, data=None):
        self.data = dict(data or {})

    async def get(self, key, default=None):
        return self.data.get(key, default)

    async def set(self, key, value):
        self.data[key] = value


@pytest.fixture
def linking(monkeypatch):
    """Tracing on, a fake uploader and an in-memory link store."""
    uploads = []

    async def uploader(data, media_type):
        uploads.append((data, media_type))
        return f"https://paste.jobe.wtf/p{len(uploads)}{'.jpg' if media_type == 'image/jpeg' else '.png'}"

    store = _MemoryStore()
    monkeypatch.setattr(telemetry, "_sending", True)
    monkeypatch.setattr(telemetry, "_uploader", uploader)
    monkeypatch.setattr(telemetry, "_links", telemetry.LRUCache(10))
    monkeypatch.setattr(telemetry, "_pending", {})
    monkeypatch.setattr(telemetry, "_link_store", store)
    return SimpleNamespace(uploads=uploads, store=store)


@pytest.mark.asyncio
async def test_request_images_are_uploaded_once_and_linked_in_the_note(linking):
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": IMAGE_B64}}
    request = [
        {"role": "user", "content": [image, {"type": "text", "text": "what"}]},
        # The same image again inside a tool result, plus a type paste.jobe.wtf doesn't take.
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": [image]}]},
        {"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/webp", "data": "AAAA"}}]},
    ]
    await telemetry.image_links_ready(request)
    await telemetry.image_links_ready(request)
    assert linking.uploads == [(b"\0" * 300_000, "image/png")]
    assert without_blobs([image])[0]["text"] == "[image/png, 293 KB: https://paste.jobe.wtf/p1.png]"
    assert len(linking.store.data) == 1


@pytest.mark.asyncio
async def test_a_stored_link_is_reused_without_uploading(linking):
    raw = b"\xff\xd8 jpeg bytes"
    linking.store.data[telemetry._image_key(raw)] = "https://paste.jobe.wtf/old.jpg"
    await telemetry.image_links_ready(images=[(raw, "image/jpeg")])
    assert linking.uploads == []
    assert telemetry._blob_note("image/jpeg", raw) == "[image/jpeg, 13 B: https://paste.jobe.wtf/old.jpg]"


@pytest.mark.asyncio
async def test_a_failed_upload_leaves_the_plain_note(linking, monkeypatch):
    async def broken(data, media_type):
        raise RuntimeError("429 rate limited")

    monkeypatch.setattr(telemetry, "_uploader", broken)
    await telemetry.image_links_ready(images=[(b"png", "image/png")])
    assert telemetry._blob_note("image/png", b"png") == "[image/png, 3 B]"
    assert telemetry._pending == {}


def test_nothing_is_uploaded_while_tracing_is_off(monkeypatch):
    monkeypatch.setattr(telemetry, "_sending", False)
    monkeypatch.setattr(telemetry, "_uploader", lambda *a: pytest.fail("uploaded"))
    assert telemetry.start_image_upload(IMAGE_B64, "image/png") is None


@pytest.mark.asyncio
async def test_paste_service_uploads_an_image():
    import httpx

    from src.services.paste_service import PasteService

    seen = []

    def handler(request):
        seen.append(request.content)
        return httpx.Response(201, text="https://paste.jobe.wtf/abc.jpg\n" if seen[-1] == b"new" else "https://paste.jobe.wtf/abc\n")

    service = PasteService(client_factory=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw))
    assert await service.upload_image(b"\xff\xd8", "image/jpeg") == "https://paste.jobe.wtf/abc.jpg"
    assert seen == [b"\xff\xd8"]
    # The server's own extension isn't doubled.
    assert await service.upload_image(b"new", "image/jpeg") == "https://paste.jobe.wtf/abc.jpg"

    def too_big(request):
        return httpx.Response(413, text="too large")

    service = PasteService(client_factory=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(too_big), **kw))
    with pytest.raises(Exception, match="413"):
        await service.upload_image(b"x")
