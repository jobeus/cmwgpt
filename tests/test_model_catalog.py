"""Tests for the /model catalog and API-key filtering."""

import unittest

from src.services.model_catalog import available_models, is_model_unavailable
from tests.config_helpers import cfg


class TestModelCatalog(unittest.TestCase):
    def config(self, gemini="", anthropic=""):
        return cfg(gemini_api_key=gemini, anthropic_api_key=anthropic)

    def test_only_models_with_keys_are_offered(self):
        values = [m.value for m in available_models(self.config(gemini="g"))]
        self.assertEqual(values, ["google", "google-high"])

    def test_all_models_offered_when_all_keys_set(self):
        values = [m.value for m in available_models(self.config("g", "a"))]
        self.assertEqual(values, ["sonnet-5.5-direct", "opus-5.5-direct", "haiku-5.5-direct", "google", "google-high"])

    def test_no_models_without_keys(self):
        self.assertEqual(available_models(self.config()), [])

    def test_is_model_unavailable(self):
        config = self.config(anthropic="a")
        self.assertFalse(is_model_unavailable("sonnet-5.5-direct", config))
        self.assertTrue(is_model_unavailable("google", config))
        # Retired models (OpenRouter's, the old hybrid) fall back to the default.
        self.assertTrue(is_model_unavailable("hybrid", config))
        self.assertTrue(is_model_unavailable("anthropic/claude-sonnet-5.5", config))


if __name__ == "__main__":
    unittest.main()
