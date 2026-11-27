"""Tests for stateless git utility helpers."""

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.utils import git_utils


class TestGitUtils(unittest.TestCase):
    def test_is_git_repository_true_and_exception_false(self):
        with patch("src.utils.git_utils.subprocess.run", return_value=SimpleNamespace(returncode=0)):
            self.assertTrue(git_utils.is_git_repository())

        with patch("src.utils.git_utils.subprocess.run", side_effect=FileNotFoundError()):
            self.assertFalse(git_utils.is_git_repository())

    def test_get_current_commit_hash(self):
        with patch(
            "src.utils.git_utils.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="abc123\n", stderr=""),
        ):
            self.assertEqual(git_utils.get_current_commit_hash(), "abc123")

    def test_get_current_commit_hash_failures_return_none(self):
        with patch(
            "src.utils.git_utils.subprocess.run",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr="fatal"),
        ):
            self.assertIsNone(git_utils.get_current_commit_hash())

        with patch("src.utils.git_utils.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git rev-parse", timeout=10)):
            self.assertIsNone(git_utils.get_current_commit_hash())
