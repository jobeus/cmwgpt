"""The Redis connection: one async client shared by the services and the content caches.

It is created lazily from ``REDIS_URL`` and refuses to start under tests.
Every key the bot writes starts with ``KEY_PREFIX``.
"""

import logging
from typing import Optional

import redis.asyncio as aioredis

from src.config import get_config

logger = logging.getLogger(__name__)

KEY_PREFIX = "cmwgpt:"

# Redis runs on the same host; anything slower than this means it's down.
SOCKET_TIMEOUT_SECONDS = 2.0

_async_client: Optional[aioredis.Redis] = None


def _check_not_testing() -> None:
    if get_config().is_testing:
        raise RuntimeError("Real Redis client requested during IS_TESTING=True")


def get_async_redis() -> aioredis.Redis:
    """The shared async client (connects on first command, not here)."""
    global _async_client
    _check_not_testing()
    if _async_client is None:
        _async_client = aioredis.Redis.from_url(
            get_config().redis_url,
            decode_responses=True,
            socket_timeout=SOCKET_TIMEOUT_SECONDS,
            socket_connect_timeout=SOCKET_TIMEOUT_SECONDS,
            health_check_interval=30,
        )
    return _async_client


async def close_redis() -> None:
    """Close the client. Called on shutdown."""
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None
    logger.info("Redis client closed.")
