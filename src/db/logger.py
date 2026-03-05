"""
Database Logger Utility
Provides a safe, non-blocking interface to log API requests to the database.
"""

import logging
import json
import asyncio
from typing import Optional, Any, Dict, Union

from src.db.connection import execute_query
from src.config import IS_TESTING

logger = logging.getLogger(__name__)

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
    if IS_TESTING:
        logger.debug(f"[TESTING] Skipped logging API request to {service_name}")
        return

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
        asyncio.create_task(execute_query(query, args))
    except Exception as e:
        logger.error(f"Failed to spawn DB log task for {service_name}: {e}")

