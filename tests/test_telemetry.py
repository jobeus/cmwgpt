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
    monkeypatch.setattr(telemetry, "_retry_at", telemetry.LRUCache(10))
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


@pytest.mark.asyncio
async def test_a_failed_upload_is_not_tried_again_straight_away(linking, monkeypatch):
    calls = []

    async def broken(data, media_type):
        calls.append(data)
        raise RuntimeError("413 too large")

    monkeypatch.setattr(telemetry, "_uploader", broken)
    await telemetry.image_links_ready(images=[(b"png", "image/png")])
    await telemetry.image_links_ready(images=[(b"png", "image/png")])
    assert len(calls) == 1

    monkeypatch.setattr(telemetry, "UPLOAD_RETRY_SECONDS", -1)
    telemetry._retry_at[telemetry._image_key(b"png")] = 0.0
    await telemetry.image_links_ready(images=[(b"png", "image/png")])
    assert len(calls) == 2


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


def test_redis_commands_keep_their_keys_and_cut_long_values():
    from src.telemetry import REDIS_VALUE_CHARS, format_redis_command

    assert format_redis_command(["GETEX", "cmwgpt:cache:pages:abc", "EX", 2592000]) == (
        "GETEX cmwgpt:cache:pages:abc EX 2592000")
    image = "data:image/jpeg;base64," + "A" * 5000
    statement = format_redis_command([b"SET", b"cmwgpt:cache:attachments:1", image.encode()])
    assert statement.startswith("SET cmwgpt:cache:attachments:1 data:image/jpeg;base64,AAA")
    assert f"…(+{len(image) - REDIS_VALUE_CHARS} more)" in statement


def test_redis_reply_preview():
    from src.telemetry import redis_reply_preview

    assert redis_reply_preview(None) == "nil"
    assert redis_reply_preview(b"hello") == "hello"
    assert redis_reply_preview(3) == "3"
    assert redis_reply_preview([b"a", None]) == "[a, nil]"
    assert redis_reply_preview({b"total": b"1.5"}) == "{total: 1.5}"
    assert redis_reply_preview(list(range(25))).endswith("…(+5 more)]")


def test_redis_spans_get_the_whole_command():
    import opentelemetry.instrumentation.redis as instrumentation
    from opentelemetry.instrumentation.redis import util

    original = util._format_command_args
    try:
        assert telemetry.capture_redis_commands()
        assert util._format_command_args(["GET", "k"]) == "GET k"
        assert instrumentation._format_command_args(["GET", "k"]) == "GET k"
    finally:
        util._format_command_args = original
        instrumentation._format_command_args = original


def test_redis_response_hook_records_the_reply():
    attributes = {}
    span = SimpleNamespace(is_recording=lambda: True, set_attribute=attributes.__setitem__)
    telemetry._redis_response_hook(span, None, None)
    assert attributes == {"db.redis.reply": "nil", "db.redis.reply_size": 0}


def _scrub(path, value, searched):
    import re

    from logfire import ScrubMatch

    match = re.search(r"session|api[._ -]?key|" + telemetry._SECRET_VALUE.pattern, searched)
    return telemetry.scrub_secrets_only(ScrubMatch(path, value, match))


def test_scrubbing_keeps_chat_text_but_not_secrets():
    text = "I lost my session cookie"
    assert _scrub(("attributes", "text"), text, text) == text
    # A field named like a secret is still redacted, whatever its value.
    assert _scrub(("attributes", "http.request.header.x-rapidapi-key"), "abc", "x-rapidapi-key") is None
    # So is text that carries an actual key.
    leaked = "my key is sk-ant-api03-AAAAAAAAAAAAAAAAAAAA"
    assert _scrub(("attributes", "text"), leaked, leaked) is None


def test_secret_matches_stop_at_the_prefix():
    # Logfire puts the matched text in its redaction note, so it must not be the key itself.
    for secret, prefix in (("sk-ant-api03-AAAAAAAAAAAAAAAAAAAA", "sk-ant-"),
                           ("AIza" + "B" * 35, "AIza"), ("pylf_v2_us_" + "C" * 40, "pylf_v2_")):
        assert telemetry._SECRET_VALUE.search(secret).group(0) == prefix


def test_http_bodies_are_captured_only_when_small():
    calls = []
    small = SimpleNamespace(headers={"content-length": "512"}, capture_body=lambda: calls.append("request"),
                            capture_body_if_text=lambda: calls.append("response"))
    big = SimpleNamespace(headers={"content-length": str(telemetry.HTTP_BODY_LIMIT + 1)},
                          capture_body=lambda: calls.append("big"), capture_body_if_text=lambda: calls.append("big"))
    unknown = SimpleNamespace(headers={}, capture_body=lambda: calls.append("unknown"),
                              capture_body_if_text=lambda: calls.append("unknown"))
    for info in (small, big, unknown):
        telemetry._httpx_request_hook(None, info)
        telemetry._httpx_response_hook(None, info, info)
    assert calls == ["request", "response"]


def _gemini_calls(monkeypatch, conflict):
    calls = []
    monkeypatch.setattr(telemetry.logfire, "instrument_google_genai", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr("opentelemetry.instrumentation.dependencies.get_dependency_conflicts", lambda deps: conflict)
    return calls


def test_gemini_is_traced_past_the_version_check_when_its_instrumentation_is_behind(monkeypatch, caplog):
    monkeypatch.delenv("GEMINI_TRACING_STRICT", raising=False)
    calls = _gemini_calls(monkeypatch, "only instruments google-genai<3")
    with caplog.at_level("WARNING"):
        telemetry.instrument_gemini()
    assert calls == [{"skip_dep_check": True}]
    assert "hasn't declared" in caplog.text


def test_gemini_strict_mode_leaves_an_undeclared_version_untraced(monkeypatch):
    monkeypatch.setenv("GEMINI_TRACING_STRICT", "true")
    calls = _gemini_calls(monkeypatch, "only instruments google-genai<3")
    telemetry.instrument_gemini()
    assert calls == []


def test_gemini_is_instrumented_normally_when_versions_match(monkeypatch):
    calls = _gemini_calls(monkeypatch, None)
    telemetry.instrument_gemini()
    assert calls == [{}]
