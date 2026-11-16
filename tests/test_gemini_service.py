"""Tests for GeminiService content conversion, cost estimation, and response handling."""

import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from google.genai import errors as genai_errors

from src.services.cost_service import cost_scope
from src.services.gemini_service import (
    GEMINI_MODELS,
    INTERACTION_CACHE_TTL,
    REQUEST_TIMEOUT_SECONDS,
    GeminiService,
    GeminiServiceError,
    _is_retryable,
    _StreamError,
    function_result,
    function_tool,
    is_gemini_model,
)
from src.services.tool_runner import MAX_TOOL_ROUNDS, ChatTool, ToolResult, text_result

HYDROGEN = GEMINI_MODELS["hydrogen"]


class TestGeminiModelHelpers(unittest.TestCase):
    def test_is_gemini_model(self):
        self.assertTrue(is_gemini_model("hydrogen"))
        self.assertTrue(is_gemini_model("argon"))
        for other in ("sonnet", "google", "gemini-4-hydrogen"):
            self.assertFalse(is_gemini_model(other))

    def test_launch_pricing_ends(self):
        from datetime import date
        argon = GEMINI_MODELS["argon"]
        self.assertEqual(argon.rates(date(2026, 11, 30)), (2.00, 10.00, 0.10))
        self.assertEqual(argon.rates(date(2026, 12, 1)), (4.00, 20.00, 0.20))
        self.assertEqual(HYDROGEN.rates(date(2026, 11, 30)), (1.50, 7.50, 0.075))


def _api_error(code):
    return genai_errors.APIError(code, {"error": {"message": f"err {code}", "status": "X"}})


def _usage(inp=10, out=5, thought=0, cached=0):
    return SimpleNamespace(total_input_tokens=inp, total_output_tokens=out, total_thought_tokens=thought,
                           total_cached_tokens=cached, total_tool_use_tokens=0)


def _reply(text="ok", *, interaction_id="int-1", status="completed", calls=(), steps=(), usage=None, error=None):
    """The events of one streamed response: ``steps`` start first, then the text, then ``calls``."""
    events = [SimpleNamespace(event_type="interaction.created", interaction=SimpleNamespace(id=interaction_id))]
    index = 0
    for step_type in steps:
        events.append(SimpleNamespace(event_type="step.start", index=index, step=SimpleNamespace(type=step_type)))
        index += 1
    if text:
        events.append(SimpleNamespace(event_type="step.start", index=index, step=SimpleNamespace(type="model_output")))
        half = len(text) // 2
        for piece in (text[:half], text[half:]):
            events.append(SimpleNamespace(
                event_type="step.delta", index=index, delta=SimpleNamespace(type="text", text=piece)))
        index += 1
    for call_id, name, arguments in calls:
        events.append(SimpleNamespace(event_type="step.start", index=index, step=SimpleNamespace(
            type="function_call", id=call_id, name=name, arguments={})))
        events.append(SimpleNamespace(event_type="step.delta", index=index, delta=SimpleNamespace(
            type="arguments_delta", arguments=json.dumps(arguments))))
        index += 1
    if error is not None:
        events.append(SimpleNamespace(event_type="error", error=error))
    else:
        events.append(SimpleNamespace(event_type="interaction.completed", interaction=SimpleNamespace(
            id=interaction_id, status="requires_action" if calls else status, usage=usage or _usage())))
    return events


def _client(*responses):
    """A client whose streamed ``interactions.create`` gives each of ``responses`` in turn (an
    exception is raised instead). Its ``create`` mock records a copy of each call's kwargs."""
    queue = list(responses)
    sent = []

    async def create(**kwargs):
        sent.append({k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
                     for k, v in kwargs.items()})
        response = queue.pop(0)
        if isinstance(response, Exception):
            raise response

        async def events():
            for event in response:
                yield event
        return events()

    client = MagicMock()
    client.aio.interactions.create = create
    client.sent = sent
    return client


def _tool(name, run, status="working..."):
    return ChatTool({"name": name, "description": f"{name} tool", "input_schema": {
        "type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"], "additionalProperties": False,
    }, "strict": True}, run, status)


class TestIsRetryable(unittest.TestCase):
    def test_api_errors_retry_only_on_429_and_5xx(self):
        self.assertTrue(_is_retryable(_api_error(429)))
        self.assertTrue(_is_retryable(_api_error(500)))
        self.assertTrue(_is_retryable(_api_error(503)))
        self.assertFalse(_is_retryable(_api_error(400)))
        self.assertFalse(_is_retryable(_api_error(403)))

    def test_transport_timeouts_and_connection_errors_are_retryable(self):
        request = httpx.Request("POST", "https://example.test")
        self.assertTrue(_is_retryable(httpx.ConnectError("down", request=request)))
        self.assertTrue(_is_retryable(httpx.ReadTimeout("slow", request=request)))
        self.assertTrue(_is_retryable(asyncio.TimeoutError()))
        self.assertTrue(_is_retryable(ConnectionResetError()))

    def test_stream_errors_retry_unless_they_are_a_plain_4xx(self):
        self.assertTrue(_is_retryable(_StreamError("Deadline expired before operation could complete.")))
        self.assertTrue(_is_retryable(_StreamError("overloaded", 503)))
        self.assertFalse(_is_retryable(_StreamError("bad request", 400)))

    def test_other_errors_are_not_retryable_even_if_message_looks_transient(self):
        self.assertFalse(_is_retryable(RuntimeError("503 Service Unavailable")))
        self.assertFalse(_is_retryable(ValueError("429 rate limit")))


class TestGeminiService(unittest.IsolatedAsyncioTestCase):
    def make_service(self):
        service = GeminiService(api_key="test-key")
        return service

    # ── Content conversion (Interactions API _build_input) ──

    def test_build_input_simple_text_messages(self):
        service = self.make_service()
        messages = [
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
        ]
        # No cached interaction -> has_prev_interaction = False
        result = service._build_input(messages, None)

        # Expected: single consolidated text part containing full transcript in order,
        # with NO missed context wrapper because has_prev_interaction=False.
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "text")
        self.assertEqual(
            result[0]["text"],
            "User: Hello!\n\nAssistant: Hi there!\n\nUser: How are you?"
        )

    def test_build_input_simple_text_messages_with_prev_interaction(self):
        service = self.make_service()
        messages = [
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
            {"role": "user", "content": "Are you there?"},
        ]
        # Cached interaction exists -> has_prev_interaction = True
        result = service._build_input(messages, {"interaction_id": "i"})

        # Expected: single consolidated text part containing the missed context wrapper
        # around the messages sent since the last bot response.
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "text")
        expected_text = (
            "The following messages occurred since your last response:\n\n"
            "User: How are you?\n\n"
            "--- End missed context ---\n\n"
            "User: Are you there?"
        )
        self.assertEqual(result[0]["text"], expected_text)

    def test_build_input_multimodal_messages(self):
        service = self.make_service()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is this?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ],
            }
        ]
        result = service._build_input(messages, None)

        # Only one message, so no history transcript
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["type"], "text")
        self.assertEqual(result[0]["text"], "User: What is this?")
        self.assertEqual(result[1]["type"], "image")
        self.assertEqual(result[1]["data"], "AAAA")
        self.assertEqual(result[1]["mime_type"], "image/png")

    def test_build_input_remote_image_url(self):
        service = self.make_service()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/img.jpg"}},
                ],
            }
        ]
        result = service._build_input(messages, None)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]["type"], "image")
        self.assertEqual(result[1]["uri"], "https://example.com/img.jpg")

    def test_build_input_history_multimodal(self):
        service = self.make_service()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Look at this:"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ],
            },
            {
                "role": "assistant",
                "content": "A nice image!",
            },
            {
                "role": "user",
                "content": "Indeed.",
            }
        ]
        result = service._build_input(messages, None)
        
        # Verify that the image in history is preserved as a native image part!
        image_parts = [p for p in result if p["type"] == "image"]
        self.assertEqual(len(image_parts), 1)
        self.assertEqual(image_parts[0]["data"], "AAAA")

    def test_cost_estimation(self):
        usage = SimpleNamespace(total_input_tokens=5500, total_output_tokens=100, total_thought_tokens=200,
                                total_cached_tokens=5000, total_tool_use_tokens=1000)
        price_input, price_output, price_cached = HYDROGEN.rates()
        expected = (1500 * price_input + 5000 * price_cached + 300 * price_output) / 1_000_000
        self.assertAlmostEqual(GeminiService._estimate_interaction_cost(usage, HYDROGEN), expected)
        as_dict = {"total_input_tokens": 5500, "total_output_tokens": 100, "total_thought_tokens": 200,
                   "total_cached_tokens": 5000, "total_tool_use_tokens": 1000}
        self.assertAlmostEqual(GeminiService._estimate_interaction_cost(as_dict, HYDROGEN), expected)
        self.assertEqual(GeminiService._estimate_interaction_cost(None, HYDROGEN), 0.0)

    # ── Tool conversion ──

    def test_function_tool_and_result_shapes(self):
        tool = _tool("lookup", AsyncMock())
        self.assertEqual(function_tool(tool.definition), {
            "type": "function", "name": "lookup", "description": "lookup tool",
            "parameters": tool.definition["input_schema"],
        })
        call = {"id": "call_1", "name": "lookup", "arguments": {}}
        result = ToolResult(is_error=True, content=[
            {"type": "text", "text": "two images"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}},
            {"type": "image", "source": {"type": "base64", "media_type": "image/gif", "data": "R0lG"}},
            {"type": "image", "source": {"type": "url", "url": "https://example.com/a.png"}},
            {"type": "video", "uri": "files/abc", "resolution": "low", "processing": "static"},
        ])
        self.assertEqual(function_result(call, result), {
            "type": "function_result", "call_id": "call_1", "name": "lookup", "is_error": True,
            "result": [
                {"type": "text", "text": "two images"},
                {"type": "image", "mime_type": "image/jpeg", "data": "QUJD"},
                {"type": "text", "text": "[An image (image/gif) that can't be shown here.]"},
                {"type": "image", "uri": "https://example.com/a.png"},
                {"type": "video", "uri": "files/abc", "resolution": "low", "processing": "static"},
            ],
        })
        self.assertEqual(function_result(call, ToolResult(content=[]))["result"],
                         [{"type": "text", "text": "(no output)"}])

    # ── get_chat_completion ──

    async def test_streams_the_reply_and_reports_status(self):
        service = self.make_service()
        service._client = _client(_reply("Hello from Gemini!", steps=("thought", "google_search_call"),
                                         usage=_usage(1000, 50, 20)))
        shown, statuses = [], []

        with cost_scope() as ledger:
            reply, cost = await service.get_chat_completion(
                model="hydrogen", messages=[{"role": "user", "content": "Hi"}], system_prompt="Be helpful",
                channel_id=123, discord_user_id=456, on_text=shown.append, on_status=statuses.append)

        self.assertEqual(reply, "Hello from Gemini!")
        self.assertEqual(shown[-1], "Hello from Gemini!")
        self.assertGreater(len(shown), 1)
        self.assertEqual(statuses, ["thinking...", "thinking...", "searching the web..."])
        self.assertAlmostEqual(cost, (1000 * 1.50 + 70 * 7.50) / 1_000_000)
        self.assertAlmostEqual(ledger.by_provider["gemini-direct"], cost)
        sent = service._client.sent[0]
        self.assertEqual(sent["model"], "gemini-4-hydrogen")
        self.assertTrue(sent["store"] and sent["stream"])
        self.assertEqual(sent["system_instruction"], "Be helpful")
        # No client tools: Google's search tools only, at the model's own thinking level.
        self.assertEqual(sent["tools"], [{"type": "google_search"}, {"type": "url_context"}])
        self.assertEqual(sent["generation_config"], {"thinking_level": "medium"})

    async def test_model_effort_and_search_shape_the_request(self):
        service = self.make_service()
        service._client = _client(_reply("a"), _reply("b"), _reply("c"))
        messages = [{"role": "user", "content": "Hi"}]

        await service.get_chat_completion(model="argon", messages=messages, search=False)
        await service.get_chat_completion(model="argon", messages=messages, effort="high")
        await service.get_chat_completion(model="argon", messages=messages, effort="xhigh")

        plain, high, unsupported = service._client.sent
        self.assertEqual(plain["model"], "gemini-4-argon")
        self.assertNotIn("tools", plain)
        self.assertEqual(plain["generation_config"]["thinking_level"], "low")
        self.assertEqual(high["generation_config"]["thinking_level"], "high")
        # A level the model doesn't take falls back to its own.
        self.assertEqual(unsupported["generation_config"]["thinking_level"], "low")

    async def test_runs_client_tools_and_sends_their_results_back(self):
        service = self.make_service()
        file = MagicMock()

        async def lookup(args):
            return ToolResult(content=[{"type": "text", "text": f"found {args['q']}"}], cost=0.5, files=[file])

        async def broken(_args):
            raise RuntimeError("it broke")

        service._client = _client(
            _reply("Let me check.", interaction_id="int-1",
                   calls=[("call_a", "lookup", {"q": "towels"}), ("call_b", "broken", {"q": "x"})]),
            _reply("All done.", interaction_id="int-2"),
        )
        shown, statuses = [], []

        reply, cost = await service.get_chat_completion(
            model="hydrogen", messages=[{"role": "user", "content": "Hi"}], channel_id=5,
            tools=[_tool("lookup", lookup, "looking..."), _tool("broken", broken, "breaking...")],
            on_text=shown.append, on_status=statuses.append)

        self.assertEqual(reply["text"], "Let me check.\n\nAll done.")
        self.assertEqual(reply["files"], [file])
        self.assertGreater(cost, 0.5)
        self.assertEqual(shown[-1], "Let me check.\n\nAll done.")
        self.assertIn("looking...", statuses)
        self.assertIn("breaking...", statuses)
        first, second = service._client.sent
        # Google's tools, code execution and the client tools together, as Google asks for them.
        self.assertEqual(first["tools"][:3], [{"type": "google_search"}, {"type": "url_context"},
                                              {"type": "code_execution"}])
        self.assertEqual([t["name"] for t in first["tools"][3:]], ["lookup", "broken"])
        self.assertEqual(first["tools"][3]["type"], "function")
        self.assertEqual(first["generation_config"]["tool_choice"], "validated")
        # The results answer the first interaction, in the order the calls were made.
        self.assertEqual(second["previous_interaction_id"], "int-1")
        self.assertEqual(second["input"], [
            {"type": "function_result", "call_id": "call_a", "name": "lookup",
             "result": [{"type": "text", "text": "found towels"}]},
            {"type": "function_result", "call_id": "call_b", "name": "broken", "is_error": True,
             "result": [{"type": "text", "text": "broken failed: it broke"}]},
        ])
        self.assertEqual(service._get_previous_interaction_id(5, "hydrogen"), "int-2")

    async def test_tool_rounds_are_capped(self):
        service = self.make_service()
        rounds = [_reply("", interaction_id=f"int-{i}", calls=[(f"c{i}", "lookup", {"q": "x"})])
                  for i in range(MAX_TOOL_ROUNDS)]
        service._client = _client(*rounds, _reply("Enough.", interaction_id="int-last"))

        reply, _ = await service.get_chat_completion(
            model="hydrogen", messages=[{"role": "user", "content": "Hi"}],
            tools=[_tool("lookup", AsyncMock(return_value=text_result("ok")))])

        self.assertEqual(reply, "Enough.")
        self.assertEqual(len(service._client.sent), MAX_TOOL_ROUNDS + 1)
        self.assertEqual(service._client.sent[-1]["generation_config"]["tool_choice"], "none")

    async def test_a_watched_video_ends_the_conversation_chain(self):
        """A video in a tool result would be billed again on every later mention."""
        service = self.make_service()
        video = {"type": "video", "uri": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "resolution": "low",
                 "processing": "static"}
        watch = AsyncMock(return_value=ToolResult(content=[{"type": "text", "text": "The video follows."}, video]))
        service._client = _client(
            _reply("", interaction_id="int-1", calls=[("c1", "watch_youtube", {"q": "x"})]),
            _reply("It's a song.", interaction_id="int-2", steps=("processing_call",)),
        )
        service._update_interaction_cache(9, "hydrogen", "int-0")
        statuses = []

        reply, _ = await service.get_chat_completion(
            model="hydrogen", messages=[{"role": "user", "content": "Hi"}], channel_id=9,
            tools=[_tool("watch_youtube", watch, "watching the video...")], on_status=statuses.append)

        self.assertEqual(reply, "It's a song.")
        self.assertEqual(service._client.sent[1]["input"][0]["result"][1], video)
        # While the tool loads the video, and again while the model moves around in it.
        self.assertGreaterEqual(statuses.count("watching the video..."), 2)
        self.assertIsNone(service._get_previous_interaction_id(9, "hydrogen"))

    # ── Continuing a channel's conversation ──

    def test_interaction_ids_survive_a_restart(self):
        import fakeredis
        from src.db.state_store import StateStore

        redis = fakeredis.FakeRedis(decode_responses=True)
        GeminiService(api_key="k", context_store=StateStore(lambda: redis))._update_interaction_cache(
            123, "hydrogen", "int-1", prompt_tokens=900, seen_message_id=77, reply="Hello   there")
        restored = GeminiService(api_key="k", context_store=StateStore(lambda: redis))
        self.assertEqual(restored._get_previous_interaction_id(123, "hydrogen"), "int-1")
        self.assertIsNone(restored._get_previous_interaction_id(123, "argon"))
        self.assertEqual(restored.history_tokens(123, "hydrogen"), 900)
        self.assertEqual(restored._chain(123, "hydrogen")["seen_message_id"], 77)
        self.assertEqual(restored._chain(123, "hydrogen")["reply_head"], "Hello there")

        restored.clear_channel_cache(123)
        again = GeminiService(api_key="k", context_store=StateStore(lambda: redis))
        self.assertIsNone(again._get_previous_interaction_id(123, "hydrogen"))

    def test_a_conversation_lasts_an_hour(self):
        service = self.make_service()
        service._update_interaction_cache(1, "hydrogen", "int-1", prompt_tokens=10)
        self.assertEqual(INTERACTION_CACHE_TTL, 3600)
        with patch("src.services.gemini_service.time.time", return_value=time.time() + 3500):
            self.assertEqual(service._get_previous_interaction_id(1, "hydrogen"), "int-1")
        with patch("src.services.gemini_service.time.time", return_value=time.time() + 3700):
            self.assertIsNone(service._get_previous_interaction_id(1, "hydrogen"))
            self.assertEqual(service.history_tokens(1, "hydrogen"), 0)

    async def test_switching_models_and_back_continues_each_models_own_conversation(self):
        service = self.make_service()
        service._client = _client(_reply("Gemini one", interaction_id="h-1"), _reply("Argon one", interaction_id="a-1"),
                                  _reply("Gemini two", interaction_id="h-2"))

        def history(*entries):
            return [{"role": role, "content": f"[t] [{mid}] <@{who}>:\n{text}", "message_id": mid}
                    for mid, role, who, text in entries]

        first = history((1, "user", 5, "hello hydrogen"))
        await service.get_chat_completion(model="hydrogen", messages=first, channel_id=7)
        # One question to another model in between (it could as well be a Claude model).
        between = history((1, "user", 5, "hello hydrogen"), (2, "assistant", 9, "Gemini one"),
                          (3, "user", 5, "hello argon"))
        await service.get_chat_completion(model="argon", messages=between, channel_id=7)
        back = history((1, "user", 5, "hello hydrogen"), (2, "assistant", 9, "Gemini one"),
                       (3, "user", 5, "hello argon"), (4, "assistant", 9, "Argon one"),
                       (5, "user", 6, "what did I miss"), (6, "user", 5, "hydrogen again"))
        await service.get_chat_completion(model="hydrogen", messages=back, channel_id=7)

        first_sent, argon_sent, back_sent = service._client.sent
        self.assertNotIn("previous_interaction_id", first_sent)
        # Argon has no conversation of its own yet, so it gets the whole history.
        self.assertNotIn("previous_interaction_id", argon_sent)
        self.assertIn("hello hydrogen", argon_sent["input"][0]["text"])
        # Hydrogen continues its own, and is caught up on everything since except its own reply.
        self.assertEqual(back_sent["previous_interaction_id"], "h-1")
        text = back_sent["input"][0]["text"]
        self.assertNotIn("hello hydrogen", text)
        self.assertNotIn("Gemini one", text)
        for expected in ("hello argon", "Argon one", "what did I miss", "hydrogen again"):
            self.assertIn(expected, text)
        self.assertEqual(service._get_previous_interaction_id(7, "hydrogen"), "h-2")
        self.assertEqual(service._get_previous_interaction_id(7, "argon"), "a-1")

    async def test_disable_cache_neither_continues_nor_remembers(self):
        service = self.make_service()
        service._update_interaction_cache(123, "hydrogen", "seeded-id")
        service._client = _client(_reply("Cache bypassed answer", interaction_id="new-id"))

        await service.get_chat_completion(
            model="hydrogen", messages=[{"role": "user", "content": "Hi"}], channel_id=123, disable_cache=True)

        self.assertNotIn("previous_interaction_id", service._client.sent[0])
        self.assertEqual(service._get_previous_interaction_id(123, "hydrogen"), "seeded-id")

    # ── Failures ──

    async def test_empty_response_raises_but_is_still_billed(self):
        service = self.make_service()
        service._client = _client(_reply("", usage=_usage(1000, 0)))
        with cost_scope() as ledger:
            with self.assertRaises(GeminiServiceError):
                await service.get_chat_completion(model="hydrogen", messages=[{"role": "user", "content": "Hi"}])
        self.assertGreater(ledger.by_provider["gemini-direct"], 0)

    async def test_retryable_errors_are_retried_then_succeed(self):
        service = self.make_service()
        deadline = SimpleNamespace(message="Deadline expired before operation could complete.", code=None)
        service._client = _client(_api_error(503), _reply("half an ans", error=deadline), _reply("ok"))
        shown = []
        with patch("src.services.gemini_service.asyncio.sleep", new=AsyncMock()) as sleep:
            reply, _ = await service.get_chat_completion(
                model="hydrogen", messages=[{"role": "user", "content": "Hi"}], on_text=shown.append)
        self.assertEqual(reply, "ok")
        self.assertEqual(len(service._client.sent), 3)
        self.assertEqual(sleep.await_count, 2)
        # What streamed in before the failure was cleared.
        self.assertIn("", shown)

    async def test_retryable_error_exhausts_attempts_and_drops_the_conversation(self):
        service = self.make_service()
        service._update_interaction_cache(4, "hydrogen", "int-0")
        service._client = _client(_api_error(503), _api_error(503), _api_error(503))
        with patch("src.services.gemini_service.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(GeminiServiceError):
                await service.get_chat_completion(
                    model="hydrogen", messages=[{"role": "user", "content": "Hi"}], channel_id=4)
        self.assertEqual(len(service._client.sent), 3)
        self.assertIsNone(service._get_previous_interaction_id(4, "hydrogen"))

    async def test_non_retryable_error_raises_immediately(self):
        service = self.make_service()
        service._client = _client(_api_error(400))
        with self.assertRaises(GeminiServiceError):
            await service.get_chat_completion(model="hydrogen", messages=[{"role": "user", "content": "Hi"}])
        self.assertEqual(len(service._client.sent), 1)

    async def test_strips_bot_mention_via_bot_id_loader(self):
        service = self.make_service()
        service.set_bot_id_loader(lambda: 999)
        service._client = _client(_reply("<@999> hello"))
        reply, _ = await service.get_chat_completion(
            model="hydrogen", messages=[{"role": "user", "content": "Hi"}])
        self.assertNotIn("<@999>", reply)

    # ── Client lifecycle ──

    def test_get_client_sets_http_timeout_in_ms(self):
        service = self.make_service()
        with patch("google.genai.Client") as mock_client_cls:
            client = service._get_client()
            self.assertIs(service._get_client(), client)

        mock_client_cls.assert_called_once()
        kwargs = mock_client_cls.call_args.kwargs
        self.assertEqual(kwargs["api_key"], "test-key")
        self.assertEqual(kwargs["http_options"].timeout, int(REQUEST_TIMEOUT_SECONDS * 1000))

    async def test_close_awaits_async_aclose_and_resets_client(self):
        service = self.make_service()
        client = MagicMock()
        client.aio.aclose = AsyncMock()
        service._client = client

        await service.close()

        client.aio.aclose.assert_awaited_once_with()
        self.assertIsNone(service._client)
        await service.close()  # no client: no-op

    async def test_close_swallows_errors(self):
        service = self.make_service()
        client = MagicMock()
        client.aio.aclose = AsyncMock(side_effect=RuntimeError("boom"))
        service._client = client

        await service.close()

        self.assertIsNone(service._client)


if __name__ == "__main__":
    unittest.main()
