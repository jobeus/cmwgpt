"""Tests for database connection helpers with mocked async pool primitives."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from tests.config_helpers import cfg

from src.db import connection


class AsyncContextValue:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TestDbConnection(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        connection._pool = None

    async def asyncTearDown(self):
        connection._pool = None

    async def test_get_db_pool_raises_during_testing_mode(self):
        with patch("src.config._cached_config", cfg(is_testing=True)):
            with self.assertRaises(RuntimeError):
                await connection.get_db_pool()

    async def test_get_db_pool_creates_pool_once_and_reuses_it(self):
        fake_pool = MagicMock()

        with patch("src.config._cached_config", cfg(is_testing=False)), patch(
            "src.db.connection.aiomysql.create_pool", new=AsyncMock(return_value=fake_pool)
        ) as mock_create_pool:
            first = await connection.get_db_pool()
            second = await connection.get_db_pool()

        self.assertIs(first, fake_pool)
        self.assertIs(second, fake_pool)
        mock_create_pool.assert_awaited_once()

    async def test_close_db_pool_closes_and_clears_global_pool(self):
        fake_pool = MagicMock()
        fake_pool.wait_closed = AsyncMock()
        connection._pool = fake_pool

        await connection.close_db_pool()

        fake_pool.close.assert_called_once_with()
        fake_pool.wait_closed.assert_awaited_once_with()
        self.assertIsNone(connection._pool)

    async def test_execute_query_returns_lastrowid(self):
        cursor = MagicMock()
        cursor.execute = AsyncMock()
        cursor.lastrowid = 77
        conn = MagicMock()
        conn.cursor.return_value = AsyncContextValue(cursor)
        pool = MagicMock()
        pool.acquire.return_value = AsyncContextValue(conn)

        with patch("src.db.connection.get_db_pool", new=AsyncMock(return_value=pool)):
            result = await connection.execute_query("INSERT INTO x VALUES (%s)", ("a",))

        self.assertEqual(result, 77)
        cursor.execute.assert_awaited_once_with("INSERT INTO x VALUES (%s)", ("a",))

    async def test_execute_query_returns_none_on_database_errors(self):
        cursor = MagicMock()
        cursor.execute = AsyncMock(side_effect=RuntimeError("db boom"))
        conn = MagicMock()
        conn.cursor.return_value = AsyncContextValue(cursor)
        pool = MagicMock()
        pool.acquire.return_value = AsyncContextValue(conn)

        with patch("src.db.connection.get_db_pool", new=AsyncMock(return_value=pool)):
            result = await connection.execute_query("DELETE FROM x", None)

        self.assertIsNone(result)
