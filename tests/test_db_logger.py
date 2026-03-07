"""Tests for safe DB request logging helpers."""

import unittest
from unittest.mock import AsyncMock, patch

from src.db import logger as db_logger


class BadString:
    def __str__(self):
        return "oops"


class TestDbLogger(unittest.IsolatedAsyncioTestCase):
    def test_build_artifact_and_pipeline_payload_helpers(self):
        artifact = db_logger.build_artifact(name="audio.mp3", media_type="audio/mpeg", data=b"abc")
        payload = db_logger.build_pipeline_payload(title="Title", step="demo", data={"ok": True}, artifacts=[artifact])

        self.assertEqual(payload["format"], db_logger.PIPELINE_STEP_FORMAT)
        self.assertEqual(payload["title"], "Title")
        self.assertEqual(payload["step"], "demo")
        self.assertEqual(payload["artifacts"][0]["size_bytes"], 3)
        self.assertIn("sha256", payload["artifacts"][0])

    def test_serialize_json_handles_common_inputs(self):
        self.assertIsNone(db_logger._serialize_json(None))
        self.assertEqual(db_logger._serialize_json("raw"), "raw")
        self.assertEqual(db_logger._serialize_json({"a": 1}), '{"a": 1}')

    def test_serialize_json_falls_back_for_unserializable_content(self):
        with patch("src.db.logger.json.dumps", side_effect=[TypeError("bad"), '{"error": "unserializable content", "raw": "oops"}']):
            result = db_logger._serialize_json(BadString())

        self.assertEqual(result, '{"error": "unserializable content", "raw": "oops"}')

    async def test_log_api_request_skips_work_in_testing_mode(self):
        with patch("src.db.logger.IS_TESTING", True), patch("src.db.logger.asyncio.create_task") as mock_task:
            await db_logger.log_api_request("svc", "GET", "https://example.com")

        mock_task.assert_not_called()

    async def test_log_api_request_schedules_execute_query_with_serialized_args(self):
        with patch("src.db.logger.IS_TESTING", False), patch(
            "src.db.logger.execute_query", new=AsyncMock()
        ) as mock_execute_query, patch(
            "src.db.logger.asyncio.create_task", side_effect=lambda coro: coro.close()
        ) as mock_create_task:
            await db_logger.log_api_request(
                service_name="svc",
                method="post",
                endpoint_url="https://example.com/api",
                request_headers={"X-Test": "1"},
                request_body={"hello": "world"},
                response_status=201,
                response_headers={"Content-Type": "application/json"},
                response_body={"ok": True},
                cost=1.5,
                discord_user_id=12,
                discord_channel_id=34,
            )

        mock_create_task.assert_called_once()
        mock_execute_query.assert_called_once()
        query, args = mock_execute_query.call_args.args
        self.assertIn("INSERT INTO api_request_logs", query)
        self.assertEqual(args[0], "svc")
        self.assertEqual(args[1], "POST")
        self.assertEqual(args[2], "https://example.com/api")
        self.assertEqual(args[8], 1.5)
        self.assertEqual(args[9], 12)
        self.assertEqual(args[10], 34)

    async def test_log_pipeline_step_uses_step_method_and_structured_payloads(self):
        with patch("src.db.logger.log_api_request", new=AsyncMock()) as mock_log_api_request:
            await db_logger.log_pipeline_step(
                service_name="downloader/demo",
                endpoint_url="https://example.com/demo",
                title="Demo step",
                step="demo_step",
                input_data={"input": 1},
                output_data={"output": 2},
                input_summary="demo input",
                output_summary="demo output",
            )

        kwargs = mock_log_api_request.await_args.kwargs
        self.assertEqual(kwargs["method"], "STEP")
        self.assertEqual(kwargs["request_body"]["format"], db_logger.PIPELINE_STEP_FORMAT)
        self.assertEqual(kwargs["response_body"]["data"], {"output": 2})