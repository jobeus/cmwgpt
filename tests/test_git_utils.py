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

    def test_get_current_commit_hash_and_branch(self):
        with patch(
            "src.utils.git_utils.subprocess.run",
            side_effect=[
                SimpleNamespace(returncode=0, stdout="abc123\n", stderr=""),
                SimpleNamespace(returncode=0, stdout="main\n", stderr=""),
            ],
        ):
            self.assertEqual(git_utils.get_current_commit_hash(), "abc123")
            self.assertEqual(git_utils.get_current_branch(), "main")

    def test_get_current_commit_hash_and_branch_failures_return_none(self):
        with patch(
            "src.utils.git_utils.subprocess.run",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr="fatal"),
        ):
            self.assertIsNone(git_utils.get_current_commit_hash())

        with patch("src.utils.git_utils.subprocess.run", side_effect=OSError("missing git")):
            self.assertIsNone(git_utils.get_current_branch())

        with patch("src.utils.git_utils.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git rev-parse", timeout=10)):
            self.assertIsNone(git_utils.get_current_commit_hash())

        with patch(
            "src.utils.git_utils.subprocess.run",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr="fatal"),
        ):
            self.assertIsNone(git_utils.get_current_branch())

    def test_fetch_updates_success_and_failure(self):
        with patch(
            "src.utils.git_utils.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
        ):
            self.assertTrue(git_utils.fetch_updates())

        with patch(
            "src.utils.git_utils.subprocess.run",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr="fatal"),
        ):
            self.assertFalse(git_utils.fetch_updates())

        with patch("src.utils.git_utils.subprocess.run", side_effect=FileNotFoundError()):
            self.assertFalse(git_utils.fetch_updates())

    def test_check_for_new_commits_handles_counts_and_bad_values(self):
        with patch("src.utils.git_utils.get_current_branch", return_value="main"), patch(
            "src.utils.git_utils.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="2\n", stderr=""),
        ):
            self.assertTrue(git_utils.check_for_new_commits())

        with patch("src.utils.git_utils.get_current_branch", return_value="main"), patch(
            "src.utils.git_utils.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="not-an-int", stderr=""),
        ):
            self.assertFalse(git_utils.check_for_new_commits())

        with patch("src.utils.git_utils.get_current_branch", return_value=None):
            self.assertFalse(git_utils.check_for_new_commits())

        with patch("src.utils.git_utils.get_current_branch", return_value="main"), patch(
            "src.utils.git_utils.subprocess.run",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr="fatal"),
        ):
            self.assertFalse(git_utils.check_for_new_commits())

        with patch("src.utils.git_utils.get_current_branch", return_value="main"), patch(
            "src.utils.git_utils.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="0\n", stderr=""),
        ):
            self.assertFalse(git_utils.check_for_new_commits())

        with patch("src.utils.git_utils.get_current_branch", return_value="main"), patch(
            "src.utils.git_utils.subprocess.run",
            side_effect=OSError("git missing"),
        ):
            self.assertFalse(git_utils.check_for_new_commits())

    def test_perform_git_pull_handles_not_repo_success_and_failure(self):
        with patch("src.utils.git_utils.is_git_repository", return_value=False):
            self.assertFalse(git_utils.perform_git_pull())

        with patch("src.utils.git_utils.is_git_repository", return_value=True), patch(
            "src.utils.git_utils.subprocess.run",
            side_effect=[
                SimpleNamespace(returncode=0, stdout="M foo.py\n", stderr=""),
                SimpleNamespace(returncode=0, stdout="Already up to date.\n", stderr=""),
            ],
        ):
            self.assertTrue(git_utils.perform_git_pull())

        with patch("src.utils.git_utils.is_git_repository", return_value=True), patch(
            "src.utils.git_utils.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git pull", timeout=60),
        ):
            self.assertFalse(git_utils.perform_git_pull())

        with patch("src.utils.git_utils.is_git_repository", return_value=True), patch(
            "src.utils.git_utils.subprocess.run",
            side_effect=[
                SimpleNamespace(returncode=0, stdout="", stderr=""),
                SimpleNamespace(returncode=1, stdout="", stderr="merge conflict"),
            ],
        ):
            self.assertFalse(git_utils.perform_git_pull())

        with patch("src.utils.git_utils.is_git_repository", return_value=True), patch(
            "src.utils.git_utils.subprocess.run",
            side_effect=[
                SimpleNamespace(returncode=0, stdout="", stderr=""),
                SimpleNamespace(returncode=0, stdout="Updating abc..def\n", stderr=""),
            ],
        ):
            self.assertTrue(git_utils.perform_git_pull())

        with patch("src.utils.git_utils.is_git_repository", return_value=True), patch(
            "src.utils.git_utils.subprocess.run",
            side_effect=OSError("git missing"),
        ):
            self.assertFalse(git_utils.perform_git_pull())

