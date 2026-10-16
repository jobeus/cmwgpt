"""Tests for AnthropicService message conversion, cost, and response handling."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.services.anthropic_service import (
    AnthropicService,
    AnthropicServiceError,
    build_anthropic_messages,
    is_direct_anthropic_model,
    _usage_cost,
)
from src.services.completion_dispatch import dispatch_completion


def _usage(input_tokens=1000, output_tokens=500, searches=0):
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        server_tool_use=SimpleNamespace(web_search_requests=searches),
    )


def _response(text="hello", stop_reason="end_turn", usage=None, content=None, stop_details=None):
    return SimpleNamespace(
        content=content if content is not None else [SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=usage or _usage(),
        model="claude-sonnet-5-5",
    )


class TestHelpers(unittest.TestCase):
    def test_is_direct_anthropic_model(self):
        self.assertTrue(is_direct_anthropic_model("sonnet-5.5-direct"))
        self.assertFalse(is_direct_anthropic_model("anthropic/claude-sonnet-5.5"))
        self.assertFalse(is_direct_anthropic_model("google"))

    def test_build_messages_converts_text_and_images(self):
        messages = [
            {"role": "system", "content": "ignored"},
            {"role": "user", "content": [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                {"type": "image_url", "image_url": {"url": "https://x.test/a.jpg"}},
                {"type": "image_url", "image_url": {"url": "data:image/bmp;base64,AAAA"}},
            ]},
        ]
        result = build_anthropic_messages(messages)
        self.assertEqual(len(result), 1)
        blocks = result[0]["content"]
        self.assertEqual(blocks[0], {"type": "text", "text": "look"})
        self.assertEqual(blocks[1]["source"], {"type": "base64", "media_type": "image/png", "data": "AAAA"})
        self.assertEqual(blocks[2]["source"], {"type": "url", "url": "https://x.test/a.jpg"})
        self.assertEqual(len(blocks), 3)  # bmp dropped

    def test_build_messages_drops_empty_and_leads_with_user(self):
        messages = [
            {"role": "assistant", "content": [{"type": "text", "text": "earlier reply"}]},
            {"role": "user", "content": [{"type": "text", "text": "   "}]},
            {"role": "user", "content": '[{"type": "text", "text": "json string"}]'},
        ]
        result = build_anthropic_messages(messages)
        self.assertEqual([m["role"] for m in result], ["user", "assistant", "user"])
        self.assertEqual(result[2]["content"][0]["text"], "json string")

    def test_usage_cost(self):
        cost = _usage_cost(_usage(input_tokens=1_000_000, output_tokens=100_000, searches=2))
        self.assertAlmostEqual(cost, 2.00 + 1.00 + 0.02)


class TestAnthropicService(unittest.IsolatedAsyncioTestCase):
    def make_service(self, *responses):
        service = AnthropicService(api_key="test-key")
        client = MagicMock()
        client.beta.messages.create = AsyncMock(side_effect=list(responses))
        service.set_client(client)
        return service, client

    async def test_returns_text_and_cost(self):
        service, client = self.make_service(_response("hi there"))
        text, cost = await service.get_chat_completion(
            model="sonnet-5.5-direct",
            messages=[{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
            system_prompt="be nice",
        )
        self.assertEqual(text, "hi there")
        self.assertGreater(cost, 0)
        kwargs = client.beta.messages.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "claude-sonnet-5-5")
        self.assertEqual(kwargs["system"], "be nice")
        self.assertEqual(kwargs["tools"][0]["type"], "web_search_20260209")
        self.assertEqual(kwargs["fallbacks"], "default")

    async def test_search_false_omits_tools(self):
        service, client = self.make_service(_response("ok"))
        await service.get_chat_completion(
            model="sonnet-5.5-direct",
            messages=[{"role": "user", "content": "hello"}],
            search=False,
        )
        self.assertNotIn("tools", client.beta.messages.create.call_args.kwargs)

    async def test_resumes_pause_turn(self):
        paused = _response(stop_reason="pause_turn", content=[SimpleNamespace(type="server_tool_use")])
        service, client = self.make_service(paused, _response("done"))
        text, _ = await service.get_chat_completion(
            model="sonnet-5.5-direct",
            messages=[{"role": "user", "content": "search something"}],
        )
        self.assertEqual(text, "done")
        self.assertEqual(client.beta.messages.create.await_count, 2)
        second_messages = client.beta.messages.create.call_args.kwargs["messages"]
        self.assertEqual(second_messages[-1]["role"], "assistant")

    async def test_joins_multiple_text_blocks(self):
        content = [
            SimpleNamespace(type="server_tool_use"),
            SimpleNamespace(type="text", text="part one "),
            SimpleNamespace(type="text", text="part two"),
        ]
        service, _ = self.make_service(_response(content=content))
        text, _ = await service.get_chat_completion(
            model="sonnet-5.5-direct", messages=[{"role": "user", "content": "q"}])
        self.assertEqual(text, "part one part two")

    async def test_refusal_raises(self):
        refusal = _response(content=[], stop_reason="refusal",
                            stop_details=SimpleNamespace(category="cyber"))
        service, _ = self.make_service(refusal)
        with self.assertRaises(AnthropicServiceError):
            await service.get_chat_completion(
                model="sonnet-5.5-direct", messages=[{"role": "user", "content": "q"}])

    async def test_empty_response_raises(self):
        service, _ = self.make_service(_response(content=[]))
        with self.assertRaises(AnthropicServiceError):
            await service.get_chat_completion(
                model="sonnet-5.5-direct", messages=[{"role": "user", "content": "q"}])


class TestDispatchRoutesDirect(unittest.IsolatedAsyncioTestCase):
    async def test_direct_model_uses_anthropic_service(self):
        openai_service = MagicMock()
        openai_service.get_chat_completion = AsyncMock()
        anthropic_service = MagicMock()
        anthropic_service.get_chat_completion = AsyncMock(return_value=("reply", 0.01))

        reply, cost = await dispatch_completion(
            model="sonnet-5.5-direct",
            messages=[{"role": "user", "content": "hi"}],
            system_prompt="sys",
            openai_service=openai_service,
            gemini_service=MagicMock(),
            anthropic_service=anthropic_service,
            identity={"channel_id": 1},
        )
        self.assertEqual((reply, cost), ("reply", 0.01))
        openai_service.get_chat_completion.assert_not_awaited()
        anthropic_service.get_chat_completion.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
