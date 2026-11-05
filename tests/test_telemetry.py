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


def _span(name, *, attributes=None, scope="logfire", error=False):
    status = SimpleNamespace(status_code=SimpleNamespace(name="ERROR" if error else "UNSET"))
    return SimpleNamespace(
        name=name, attributes=attributes or {}, instrumentation_scope=SimpleNamespace(name=scope), status=status,
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
