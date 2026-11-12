"""Tests for the package update watcher."""

import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import fakeredis

from src.db.state_store import StateStore
from src.services.package_update_service import PackageUpdateService, pending_upgrades


def report(*packages):
    return json.dumps({"install": [{"metadata": {"name": n, "version": v}} for n, v in packages]})


class TestPendingUpgrades(unittest.TestCase):
    def test_lists_what_a_full_upgrade_would_install(self):
        with patch("src.services.package_update_service.subprocess.run", return_value=SimpleNamespace(
                returncode=0, stdout=report(("yt-dlp", "2026.10.26"), ("anyio", "4.16.0")), stderr="")) as run:
            self.assertEqual(pending_upgrades(), ["anyio==4.16.0", "yt-dlp==2026.10.26"])
        command = run.call_args.args[0]
        self.assertIn("--dry-run", command)
        self.assertEqual(command[command.index("--upgrade-strategy") + 1], "eager")

    def test_nothing_to_install_is_an_empty_list(self):
        with patch("src.services.package_update_service.subprocess.run", return_value=SimpleNamespace(
                returncode=0, stdout=report(), stderr="")):
            self.assertEqual(pending_upgrades(), [])

    def test_pip_failures_are_none_not_an_empty_list(self):
        failures = [
            SimpleNamespace(returncode=1, stdout="", stderr="no network"),
            SimpleNamespace(returncode=0, stdout="not json", stderr=""),
        ]
        for failure in failures:
            with patch("src.services.package_update_service.subprocess.run", return_value=failure):
                self.assertIsNone(pending_upgrades())
        with patch("src.services.package_update_service.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("pip", 300)):
            self.assertIsNone(pending_upgrades())


class TestPackageUpdateService(unittest.IsolatedAsyncioTestCase):
    def make_service(self, pending, **kwargs):
        self.restart = AsyncMock(return_value=True)
        redis = fakeredis.FakeRedis(decode_responses=True)
        self.store = StateStore(lambda: redis)
        return PackageUpdateService(store=self.store, restart=self.restart, check=lambda: pending, **kwargs)

    async def test_restarts_when_updates_are_out(self):
        service = self.make_service(["yt-dlp==2026.10.26"])
        self.assertTrue(await service.check_once())
        self.restart.assert_awaited_once_with()

    async def test_does_nothing_when_up_to_date_or_pip_failed(self):
        for pending in ([], None):
            service = self.make_service(pending)
            self.assertFalse(await service.check_once())
            self.restart.assert_not_awaited()

    async def test_does_not_restart_twice_for_the_same_updates(self):
        service = self.make_service(["yt-dlp==2026.10.26"])
        await service.check_once()
        # After the restart the same upgrade is still pending: it didn't install.
        again = PackageUpdateService(store=self.store, restart=self.restart, check=lambda: ["yt-dlp==2026.10.26"])
        self.assertFalse(await again.check_once())
        self.restart.assert_awaited_once_with()
        # A different release does restart.
        newer = PackageUpdateService(store=self.store, restart=self.restart, check=lambda: ["yt-dlp==2026.11.1"])
        self.assertTrue(await newer.check_once())

    async def test_does_not_restart_when_the_trigger_cannot_be_saved(self):
        service = self.make_service(["yt-dlp==2026.10.26"])
        service._store = StateStore(None)
        self.assertFalse(await service.check_once())
        self.restart.assert_not_awaited()

    async def test_off_when_testing_or_interval_is_zero(self):
        self.assertFalse(self.make_service([], is_testing=True).enabled)
        self.assertFalse(self.make_service([], interval_hours=0).enabled)
        service = self.make_service([], interval_hours=0)
        service.start()
        self.assertIsNone(service._task)


if __name__ == "__main__":
    unittest.main()
