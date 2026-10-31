"""Tests for sending Claude API requests through Message Batches, and the nightly memory run using it."""

import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.anthropic_service import (
    BATCH_DISCOUNT, DIRECT_MODELS, AnthropicService, AnthropicServiceError, MessageBatcher, _usage_cost,
)
from src.services.cost_service import cost_scope
from src.services.memory_service import MemoryService
from tests.config_helpers import cfg


def _usage():
    return SimpleNamespace(input_tokens=1000, output_tokens=100, cache_creation_input_tokens=0,
                           cache_read_input_tokens=0, server_tool_use=None)


def _succeeded(custom_id, text):
    message = SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], usage=_usage(),
                              model="claude-sonnet-5-5", stop_reason="end_turn")
    return SimpleNamespace(custom_id=custom_id, result=SimpleNamespace(type="succeeded", message=message))


class FakeBatches:
    """Stands in for ``client.beta.messages.batches``: ends after one poll, answers each request."""

    def __init__(self, answer):
        self.answer = answer
        self.created = []
        self.retrieve = AsyncMock(return_value=SimpleNamespace(id="batch_1", processing_status="ended"))
        self.cancel = AsyncMock()

    async def create(self, *, requests):
        self.created.append(list(requests))
        return SimpleNamespace(id="batch_1", processing_status="in_progress")

    async def results(self, batch_id):
        requests = self.created[-1]

        async def entries():
            for request in requests:
                yield self.answer(request)
        return entries()


def _service(batches):
    service = AnthropicService(api_key="test-key")
    client = MagicMock()
    client.beta.messages.batches = batches
    service.set_client(client)
    return service


class TestMessageBatcher(unittest.IsolatedAsyncioTestCase):
    async def test_requests_made_together_share_one_batch_at_half_price(self):
        batches = FakeBatches(lambda r: _succeeded(r["custom_id"], "answer to " + r["params"]["messages"][0]
                                                   ["content"][0]["text"]))
        batcher = MessageBatcher(_service(batches), collect_seconds=0.01, poll_seconds=0.01)

        with cost_scope() as ledger:
            results = await asyncio.gather(
                batcher.complete(model="sonnet-5.5-direct", system_prompt="sys", user_text="a", effort="medium"),
                batcher.complete(model="sonnet-5.5-direct", system_prompt="sys", user_text="b", effort="medium"),
            )

        self.assertEqual(len(batches.created), 1)
        self.assertEqual([r[0] for r in results], ["answer to a", "answer to b"])
        price = _usage_cost(_usage(), DIRECT_MODELS["sonnet-5.5-direct"]) * BATCH_DISCOUNT
        self.assertAlmostEqual(results[0][1], price)
        self.assertAlmostEqual(ledger.by_provider["claude-direct"], 2 * price)
        params = batches.created[0][0]["params"]
        self.assertEqual(params["model"], "claude-sonnet-5-5")
        self.assertEqual(params["output_config"], {"effort": "medium"})
        self.assertEqual(params["system"][0]["cache_control"], {"type": "ephemeral"})

    async def test_a_failed_request_raises_so_the_caller_can_retry(self):
        batches = FakeBatches(lambda r: SimpleNamespace(custom_id=r["custom_id"], result=SimpleNamespace(type="errored")))
        batcher = MessageBatcher(_service(batches), collect_seconds=0.01, poll_seconds=0.01)
        with self.assertRaises(AnthropicServiceError):
            await batcher.complete(model="sonnet-5.5-direct", system_prompt="", user_text="a")

    async def test_a_batch_that_never_ends_is_cancelled(self):
        batches = FakeBatches(lambda r: _succeeded(r["custom_id"], "late"))
        batches.retrieve = AsyncMock(return_value=SimpleNamespace(id="batch_1", processing_status="in_progress"))
        batcher = MessageBatcher(_service(batches), collect_seconds=0.01, poll_seconds=0.01, max_wait_seconds=0.03)
        with self.assertRaises(AnthropicServiceError):
            await batcher.complete(model="sonnet-5.5-direct", system_prompt="", user_text="a")
        batches.cancel.assert_awaited_once_with("batch_1")


class TestNightlyRunIsBatched(unittest.IsolatedAsyncioTestCase):
    def make_service(self):
        service = MemoryService(store=MagicMock(), config=cfg(anthropic_api_key="key"),
                                openai_service=MagicMock(), anthropic_service=MagicMock(),
                                now=lambda: datetime(2026, 10, 16, 8, 0, tzinfo=timezone.utc))
        return service

    async def test_only_the_nightly_run_batches_and_failures_retry_directly(self):
        service = self.make_service()
        seen = []

        async def run_windows(guild, windows, source):
            seen.append(await service._complete("sys", "nightly"))

        fake_batcher = MagicMock()
        fake_batcher.complete = AsyncMock(side_effect=AnthropicServiceError("expired"))
        service._run_windows = run_windows
        service._store.get_processed_through = AsyncMock(return_value=None)
        service.set_bot(SimpleNamespace(guilds=[SimpleNamespace(id=1)], user=SimpleNamespace(id=9)))

        with patch("src.services.memory_service.MessageBatcher", return_value=fake_batcher), patch(
            "src.services.memory_service.dispatch_completion", new=AsyncMock(return_value=("direct", 0.01))
        ) as dispatch:
            await service.run_nightly()
            fake_batcher.complete.assert_awaited_once()
            self.assertEqual(seen, [("direct", 0.01)])  # the batch failed, so it went direct

            # Outside the nightly run, calls go straight to the model.
            await service._complete("sys", "a correction")
        self.assertEqual(fake_batcher.complete.await_count, 1)
        self.assertEqual(dispatch.await_count, 2)

    async def test_search_calls_are_never_batched(self):
        service = self.make_service()
        fake_batcher = MagicMock()
        fake_batcher.complete = AsyncMock()
        from src.services import memory_service
        token = memory_service._batcher.set(fake_batcher)
        try:
            with patch("src.services.memory_service.dispatch_completion", new=AsyncMock(return_value=("x", 0.0))):
                await service._complete("sys", "news", search=True)
        finally:
            memory_service._batcher.reset(token)
        fake_batcher.complete.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
