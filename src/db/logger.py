"""
Database Logger Utility
Provides a safe, non-blocking interface to log API requests to the database.
"""

import logging
import json
import asyncio
import hashlib
from typing import Optional, Any, Dict, Union

from src.db.connection import execute_query
from src.config import get_config

logger = logging.getLogger(__name__)

PIPELINE_STEP_FORMAT = "pipeline_step.v1"

# Strong references to in-flight fire-and-forget log tasks so the event loop
# can't garbage-collect them mid-flight (asyncio only keeps weak references).
_pending_log_tasks: set = set()


def _serialize_json(data: Any) -> Optional[str]:
    """Helper to safely serialize dictionaries/lists to JSON strings."""
    if data is None:
        return None
    if isinstance(data, str):
        # Allow passing pre-serialized strings
        return data
    try:
        return json.dumps(data, default=str)
    except (TypeError, ValueError) as e:
        logger.warning(f"Failed to serialize JSON for DB logging: {e}")
        return json.dumps({"error": "unserializable content", "raw": str(data)})


def build_artifact(
    *,
    name: str,
    media_type: str,
    data: Optional[bytes] = None,
    text: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a compact artifact descriptor for transformation-step logs."""
    raw_bytes = data
    if raw_bytes is None and text is not None:
        raw_bytes = text.encode("utf-8", errors="replace")

    artifact: Dict[str, Any] = {
        "name": name,
        "media_type": media_type,
    }
    if raw_bytes is not None:
        artifact["size_bytes"] = len(raw_bytes)
        artifact["sha256"] = hashlib.sha256(raw_bytes).hexdigest()
    if extra:
        artifact.update(extra)
    return artifact


def build_pipeline_payload(
    *,
    title: str,
    step: str,
    summary: Optional[str] = None,
    data: Any = None,
    artifacts: Optional[list[Dict[str, Any]]] = None,
    replay: Optional[Dict[str, str]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a structured payload that the log viewer can prettily render."""
    payload: Dict[str, Any] = {
        "format": PIPELINE_STEP_FORMAT,
        "title": title,
        "step": step,
    }
    if summary:
        payload["summary"] = summary
    if data is not None:
        payload["data"] = data
    if artifacts:
        payload["artifacts"] = artifacts
    if replay:
        payload["replay"] = replay
    if meta:
        payload["meta"] = meta
    return payload


async def log_api_request(
    service_name: str,
    method: str,
    endpoint_url: str,
    request_headers: Optional[Union[Dict[str, Any], str]] = None,
    request_body: Optional[Union[Dict[str, Any], str]] = None,
    response_status: Optional[int] = None,
    response_headers: Optional[Union[Dict[str, Any], str]] = None,
    response_body: Optional[Union[Dict[str, Any], str]] = None,
    cost: float = 0.0,
    discord_user_id: Optional[int] = None,
    discord_channel_id: Optional[int] = None
) -> None:
    """
    Asynchronously log an API request to the central database without blocking main execution.
    Fails safely instead of crashing bots on DB down.
    """
    if get_config().is_testing:
        logger.debug(f"[TESTING] Skipped logging API request to {service_name}")
        return

    # We rely on MariaDB's Virtual Column to generate the curl commands natively!

    query = """
        INSERT INTO api_request_logs (
            service_name, method, endpoint_url,
            request_headers, request_body,
            response_status, response_headers, response_body,
            cost, discord_user_id, discord_channel_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    args = (
        service_name,
        method.upper(),
        endpoint_url,
        _serialize_json(request_headers),
        _serialize_json(request_body) if not isinstance(request_body, str) else request_body,
        response_status,
        _serialize_json(response_headers),
        _serialize_json(response_body) if not isinstance(response_body, str) else response_body,
        cost,
        discord_user_id,
        discord_channel_id
    )

    try:
        # Wrap in a fire-and-forget task so the slow DB inserts don't block the hot path of the bot
        task = asyncio.create_task(execute_query(query, args))
        _pending_log_tasks.add(task)
        task.add_done_callback(_pending_log_tasks.discard)
    except Exception as e:
        logger.error(f"Failed to spawn DB log task for {service_name}: {e}")


async def log_pipeline_step(
    *,
    service_name: str,
    endpoint_url: str,
    title: str,
    step: str,
    input_data: Any = None,
    output_data: Any = None,
    input_summary: Optional[str] = None,
    output_summary: Optional[str] = None,
    request_headers: Optional[Union[Dict[str, Any], str]] = None,
    response_headers: Optional[Union[Dict[str, Any], str]] = None,
    request_artifacts: Optional[list[Dict[str, Any]]] = None,
    response_artifacts: Optional[list[Dict[str, Any]]] = None,
    request_replay: Optional[Dict[str, str]] = None,
    response_replay: Optional[Dict[str, str]] = None,
    meta: Optional[Dict[str, Any]] = None,
    response_status: Optional[int] = 200,
    cost: float = 0.0,
    discord_user_id: Optional[int] = None,
    discord_channel_id: Optional[int] = None,
) -> None:
    """Log a non-HTTP transformation step using the existing request log table."""
    await log_api_request(
        service_name=service_name,
        method="STEP",
        endpoint_url=endpoint_url,
        request_headers=request_headers,
        request_body=build_pipeline_payload(
            title=title,
            step=step,
            summary=input_summary,
            data=input_data,
            artifacts=request_artifacts,
            replay=request_replay,
            meta=meta,
        ),
        response_status=response_status,
        response_headers=response_headers,
        response_body=build_pipeline_payload(
            title=title,
            step=step,
            summary=output_summary,
            data=output_data,
            artifacts=response_artifacts,
            replay=response_replay,
            meta=meta,
        ),
        cost=cost,
        discord_user_id=discord_user_id,
        discord_channel_id=discord_channel_id,
    )
