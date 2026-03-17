"""Tests for RunpodService request handling and validation."""

import unittest
from unittest.mock import AsyncMock, patch

import httpx

from src.services.runpod_service import RunpodService, RunpodServiceError


class FakeClientContext:
    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeClient:
    def __init__(self, post_response=None, get_response=None):
        self._post_response = post_response
        self._get_response = get_response

    async def post(self, url, headers=None, json=None):
        return self._post_response

    async def get(self, url):
        return self._get_response


class TestRunpodService(unittest.IsolatedAsyncioTestCase):
    def make_service(self):
        service = RunpodService()
        service.api_key = "test-api-key"
        return service

    def test_helper_predicates_report_model_support_and_configuration(self):
        service = self.make_service()

        self.assertTrue(service.has_model("seedream"))
        self.assertFalse(service.has_model("unknown"))
        self.assertTrue(service.has_edit_model("qwen"))
        self.assertFalse(service.has_edit_model("unknown"))
        self.assertTrue(service.is_configured())

        service.api_key = ""
        self.assertFalse(service.is_configured())

    async def test_generate_image_requires_api_key(self):
        service = self.make_service()
        service.api_key = ""

        with self.assertRaises(RunpodServiceError) as ctx:
            await service.generate_image("hello", "seedream")

        self.assertIn("not configured", str(ctx.exception))

    async def test_generate_image_rejects_unknown_model(self):
        service = self.make_service()

        with self.assertRaises(RunpodServiceError) as ctx:
            await service.generate_image("hello", "unknown-model")

        self.assertIn("not supported", str(ctx.exception))

    async def test_edit_image_rejects_unknown_edit_model(self):
        service = self.make_service()

        with self.assertRaises(RunpodServiceError) as ctx:
            await service.edit_image("hello", "unknown-model", ["img1"])

        self.assertIn("not supported for editing", str(ctx.exception))

    async def test_edit_image_requires_api_key(self):
        service = self.make_service()
        service.api_key = ""

        with self.assertRaises(RunpodServiceError) as ctx:
            await service.edit_image("hello", "qwen", ["img1"])

        self.assertIn("not configured", str(ctx.exception))

    async def test_generate_and_edit_delegate_to_execute_request(self):
        service = self.make_service()

        with patch.object(service, "_execute_request", new=AsyncMock(return_value=(b"img", 0.5))) as mock_execute:
            await service.generate_image("hello", "seedream", discord_user_id=1, discord_channel_id=2)
            await service.edit_image("edit", "qwen", ["img1"], discord_user_id=3, discord_channel_id=4)

        generate_call = mock_execute.await_args_list[0]
        self.assertEqual(generate_call.args[0], service.models["seedream"]["url"])
        self.assertEqual(generate_call.args[2], "generate")
        self.assertEqual(generate_call.args[3:], (1, 2))
        self.assertEqual(generate_call.args[1]["input"]["prompt"], "hello")

        edit_call = mock_execute.await_args_list[1]
        self.assertEqual(edit_call.args[0], service.edit_models["qwen"]["url"])
        self.assertEqual(edit_call.args[2], "edit")
        self.assertEqual(edit_call.args[3:], (3, 4))
        self.assertEqual(edit_call.args[1]["input"]["images"], ["img1"])

    async def test_execute_request_returns_image_bytes_and_logs_request(self):
        service = self.make_service()
        post_request = httpx.Request("POST", "https://api.runpod.ai/test", content=b'{"prompt":"hello"}')
        post_response = httpx.Response(
            200,
            json={"status": "COMPLETED", "output": {"image_url": "https://cdn.example.com/img.png", "cost": 0.25}},
            request=post_request,
            headers={"Content-Type": "application/json"},
        )
        image_response = httpx.Response(
            200,
            content=b"PNGDATA",
            request=httpx.Request("GET", "https://cdn.example.com/img.png"),
        )
        fake_client = FakeClient(post_response=post_response, get_response=image_response)

        with patch(
            "src.services.runpod_service.create_async_client",
            side_effect=lambda **kwargs: FakeClientContext(fake_client),
        ):
            content, cost = await service._execute_request(
                "https://api.runpod.ai/test",
                {"input": {"prompt": "hello"}},
                "generate",
                discord_user_id=12,
                discord_channel_id=34,
            )

        self.assertEqual(content, b"PNGDATA")
        self.assertEqual(cost, 0.25)

    async def test_execute_request_accepts_result_field_as_image_url(self):
        service = self.make_service()
        post_response = httpx.Response(
            200,
            json={"status": "COMPLETED", "output": {"result": "https://cdn.example.com/result.png"}},
            request=httpx.Request("POST", "https://api.runpod.ai/test", content=b"{}"),
        )
        image_response = httpx.Response(
            200,
            content=b"RESULTDATA",
            request=httpx.Request("GET", "https://cdn.example.com/result.png"),
        )

        with patch(
            "src.services.runpod_service.create_async_client",
            side_effect=lambda **kwargs: FakeClientContext(FakeClient(post_response, image_response)),
        ):
            content, cost = await service._execute_request(
                "https://api.runpod.ai/test", {"input": {}}, "edit"
            )

        self.assertEqual(content, b"RESULTDATA")
        self.assertIsNone(cost)

    async def test_execute_request_raises_for_failed_status(self):
        service = self.make_service()
        failed_response = httpx.Response(
            200,
            json={"status": "FAILED", "error": "model exploded"},
            request=httpx.Request("POST", "https://api.runpod.ai/test", content=b"{}"),
        )

        with patch(
            "src.services.runpod_service.create_async_client",
            side_effect=lambda **kwargs: FakeClientContext(FakeClient(post_response=failed_response)),
        ):
            with self.assertRaises(RunpodServiceError) as ctx:
                await service._execute_request("https://api.runpod.ai/test", {"input": {}}, "generate")

        self.assertIn("model exploded", str(ctx.exception))

    async def test_execute_request_raises_for_missing_image_url_and_unexpected_status(self):
        service = self.make_service()
        missing_url_response = httpx.Response(
            200,
            json={"status": "COMPLETED", "output": {"image_url": None}},
            request=httpx.Request("POST", "https://api.runpod.ai/test", content=b"{}"),
        )
        weird_status_response = httpx.Response(
            200,
            json={"status": "IN_QUEUE"},
            request=httpx.Request("POST", "https://api.runpod.ai/test", content=b"{}"),
        )

        with patch(
            "src.services.runpod_service.create_async_client",
            side_effect=lambda **kwargs: FakeClientContext(FakeClient(post_response=missing_url_response)),
        ):
            with self.assertRaises(RunpodServiceError) as ctx:
                await service._execute_request("https://api.runpod.ai/test", {"input": {}}, "generate")
        self.assertIn("no valid image URL", str(ctx.exception))

        with patch(
            "src.services.runpod_service.create_async_client",
            side_effect=lambda **kwargs: FakeClientContext(FakeClient(post_response=weird_status_response)),
        ):
            with self.assertRaises(RunpodServiceError) as ctx:
                await service._execute_request("https://api.runpod.ai/test", {"input": {}}, "generate")
        self.assertIn("unexpected status", str(ctx.exception))

    async def test_execute_request_wraps_http_errors(self):
        service = self.make_service()
        error_response = httpx.Response(
            500,
            request=httpx.Request("POST", "https://api.runpod.ai/test", content=b"{}"),
        )

        with patch(
            "src.services.runpod_service.create_async_client",
            side_effect=lambda **kwargs: FakeClientContext(FakeClient(post_response=error_response)),
        ):
            with self.assertRaises(RunpodServiceError) as ctx:
                await service._execute_request("https://api.runpod.ai/test", {"input": {}}, "generate")

        self.assertIn("Failed to communicate with Runpod API", str(ctx.exception))

    async def test_execute_request_wraps_unexpected_errors(self):
        service = self.make_service()

        class BadResponse:
            request = httpx.Request("POST", "https://api.runpod.ai/test", content=b"{}")
            status_code = 200
            headers = {}

            def raise_for_status(self):
                return None

            def json(self):
                raise ValueError("bad json")

        with patch(
            "src.services.runpod_service.create_async_client",
            side_effect=lambda **kwargs: FakeClientContext(FakeClient(post_response=BadResponse())),
        ):
            with self.assertRaises(RunpodServiceError) as ctx:
                await service._execute_request("https://api.runpod.ai/test", {"input": {}}, "generate")

        self.assertIn("Unexpected error: bad json", str(ctx.exception))
