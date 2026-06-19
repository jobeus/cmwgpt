"""Database connection utilities for MariaDB using ``aiomysql``."""

import logging
from typing import Any, Optional

import aiomysql

from src.config import get_config

logger = logging.getLogger(__name__)

# Global pool instance
_pool: Optional[aiomysql.Pool] = None


async def get_db_pool() -> aiomysql.Pool:
    """
    Get the global MariaDB connection pool, initializing it if necessary.
    """
    global _pool

    cfg = get_config()
    if cfg.is_testing:
        # Avoid creating real pools during tests unless explicitly needed
        raise RuntimeError("Real database pool requested during IS_TESTING=True")

    if _pool is None:
        if not cfg.db_password:
            logger.warning("DB_PASSWORD is not set. Database logging may fail.")

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
            logger.info("MariaDB pool initialized successfully.")
        except Exception:
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
    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, args or ())
                return cur.lastrowid
    except Exception:
        logger.exception("Database query error")
        # Not raising here to prevent DB logging errors from crashing core bot features
        return None
