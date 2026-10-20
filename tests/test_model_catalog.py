"""Tests for the /model catalog and API-key filtering."""

import unittest

from src.services.model_catalog import available_models, is_model_unavailable
from tests.config_helpers import cfg


class TestModelCatalog(unittest.TestCase):
    def config(self, openrouter="", gemini="", anthropic=""):
        return cfg(openrouter_api_key=openrouter, gemini_api_key=gemini, anthropic_api_key=anthropic)

    def test_only_models_with_keys_are_offered(self):
        values = [m.value for m in available_models(self.config(gemini="g"))]
        self.assertEqual(values, ["google", "google-high"])

    def test_all_models_offered_when_all_keys_set(self):
        values = [m.value for m in available_models(self.config("o", "g", "a"))]
        self.assertIn("anthropic/claude-sonnet-5.5", values)
        self.assertIn("sonnet-5.5-direct", values)
        self.assertIn("opus-5.5-direct", values)
        self.assertIn("google", values)
        self.assertNotIn("hybrid", values)

    def test_openrouter_placeholder_key_counts_as_missing(self):
        values = [m.value for m in available_models(self.config(openrouter="test-key-for-ci"))]
        self.assertEqual(values, [])

    def test_is_model_unavailable(self):
        config = self.config(openrouter="o")
        self.assertTrue(is_model_unavailable("hybrid", config))
        self.assertTrue(is_model_unavailable("sonnet-5.5-direct", config))
        self.assertFalse(is_model_unavailable("anthropic/claude-sonnet-5.5", config))
        self.assertFalse(is_model_unavailable("some/custom-model", config))


if __name__ == "__main__":
    unittest.main()
