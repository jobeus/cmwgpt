"""Database connection utilities for MariaDB using ``aiomysql``."""

import asyncio
import logging
import time
from typing import Any, Optional

import aiomysql

from src.config import get_config

logger = logging.getLogger(__name__)

# Global pool instance
_pool: Optional[aiomysql.Pool] = None
# Concurrent first callers must share one pool rather than each creating one.
_pool_lock = asyncio.Lock()
# After a failed connect, don't retry (and log a traceback) on every call.
POOL_RETRY_BACKOFF_SECONDS = 30.0
_pool_failed_at: Optional[float] = None


async def get_db_pool() -> aiomysql.Pool:
    """
    Get the global MariaDB connection pool, initializing it if necessary.
    """
    global _pool, _pool_failed_at

    cfg = get_config()
    if cfg.is_testing:
        # Avoid creating real pools during tests unless explicitly needed
        raise RuntimeError("Real database pool requested during IS_TESTING=True")

    if _pool is not None:
        return _pool

    async with _pool_lock:
        if _pool is not None:
            return _pool
        if _pool_failed_at is not None and time.monotonic() - _pool_failed_at < POOL_RETRY_BACKOFF_SECONDS:
            raise RuntimeError("MariaDB is unavailable (recent connection failure); retrying shortly")
        if not cfg.db_password:
            logger.warning("DB_PASSWORD is not set. Database access may fail.")

        try:
            logger.info(f"Initializing MariaDB pool to {cfg.db_user}@{cfg.db_host}:{cfg.db_port}/{cfg.db_name}")
            _pool = await aiomysql.create_pool(
                host=cfg.db_host,
                port=cfg.db_port,
                user=cfg.db_user,
                password=cfg.db_password,
                db=cfg.db_name,
                autocommit=True,
                charset='utf8mb4',
                minsize=1,
                maxsize=10,
            )
            _pool_failed_at = None
            logger.info("MariaDB pool initialized successfully.")
        except Exception:
            _pool_failed_at = time.monotonic()
            logger.exception("Failed to initialize MariaDB pool")
            raise

    return _pool


async def close_db_pool() -> None:
    """
    Close the global database pool. Should be called on application shutdown.
    """
    global _pool
    if _pool is not None:
        logger.info("Closing MariaDB pool...")
        _pool.close()
        await _pool.wait_closed()
        _pool = None
        logger.info("MariaDB pool closed.")


async def execute_query(query: str, args: tuple = None) -> Any:
    """
    Helper to execute a query (INSERT, UPDATE, DELETE) acquiring and releasing a connection automatically.

    Args:
        query: SQL query string
        args: Tuple of parameters

    Returns:
        The last row ID (if applicable)
    """
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, args or ())
                return cur.lastrowid
    except Exception:
        logger.exception("Database query error")
        # Not raising: a DB outage shouldn't crash the caller
        return None
