# Test package for Discord Bot

import os
import tempfile

# Keep tests away from the real .cache/cmwgpt_settings.json: any StateService a test
# builds reads and writes a throwaway file instead. The directory is removed at exit.
_settings_dir = tempfile.TemporaryDirectory(prefix="cmwgpt-test-")
os.environ["CMWGPT_SETTINGS_FILE"] = os.path.join(_settings_dir.name, "cmwgpt_settings.json")
