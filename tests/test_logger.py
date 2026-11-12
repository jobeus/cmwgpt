"""Tests for the log file handler set up by setup_logger."""

import logging
import logging.handlers
import os
import tempfile
import unittest

from src.utils.logger import setup_logger


class TestLogRotation(unittest.TestCase):
    def test_old_dated_logs_are_found_for_deletion(self):
        root = logging.getLogger()
        before_handlers, before_level = list(root.handlers), root.level
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            os.chdir(temp_dir)
            try:
                os.mkdir("logs")
                for day in range(1, 5):
                    with open(os.path.join("logs", f"2026-01-0{day}.log"), "w") as f:
                        f.write("x")
                setup_logger()
                handler = next(
                    h for h in root.handlers if isinstance(h, logging.handlers.TimedRotatingFileHandler))
                handler.backupCount = 2
                doomed = sorted(os.path.basename(p) for p in handler.getFilesToDelete())
            finally:
                for h in list(root.handlers):
                    if h not in before_handlers:
                        root.removeHandler(h)
                        h.close()
                root.handlers[:] = before_handlers
                root.setLevel(before_level)
                os.chdir(cwd)

        self.assertEqual(doomed, ["2026-01-01.log", "2026-01-02.log"])


if __name__ == "__main__":
    unittest.main()
