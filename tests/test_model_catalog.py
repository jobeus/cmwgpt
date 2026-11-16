"""Tests for the /model catalog and API-key filtering."""

import unittest

from src.services.model_catalog import available_models, is_model_unavailable
from tests.config_helpers import cfg


class TestModelCatalog(unittest.TestCase):
    def config(self, gemini="", anthropic=""):
        return cfg(gemini_api_key=gemini, anthropic_api_key=anthropic)

    def test_only_models_with_keys_are_offered(self):
        values = [m.value for m in available_models(self.config(gemini="g"))]
        self.assertEqual(values, ["hydrogen", "argon"])

    def test_all_models_offered_when_all_keys_set(self):
        values = [m.value for m in available_models(self.config("g", "a"))]
        self.assertEqual(values, ["sonnet", "opus", "haiku", "hydrogen", "argon"])

    def test_no_models_without_keys(self):
        self.assertEqual(available_models(self.config()), [])

    def test_is_model_unavailable(self):
        config = self.config(anthropic="a")
        self.assertFalse(is_model_unavailable("sonnet", config))
        self.assertTrue(is_model_unavailable("hydrogen", config))
        # Retired models (OpenRouter's, the old hybrid) fall back to the default.
        self.assertTrue(is_model_unavailable("hybrid", config))
        self.assertTrue(is_model_unavailable("anthropic/claude-sonnet-5.5", config))


if __name__ == "__main__":
    unittest.main()


class TestLegacyModelNames(unittest.TestCase):
    def test_old_names_map_to_the_models_that_replaced_them(self):
        from src.services.model_catalog import canonical_model, model_provider
        self.assertEqual(canonical_model("sonnet-5.5-direct"), "sonnet")
        self.assertEqual(canonical_model("google-high"), "hydrogen")
        self.assertEqual(canonical_model("argon"), "argon")
        self.assertEqual((model_provider("opus"), model_provider("argon"), model_provider("google")),
                         ("anthropic", "gemini", None))

    def test_saved_channel_models_and_env_defaults_are_read_under_their_new_names(self):
        from src.config import load_config
        from src.services.state_service import StateService
        service = StateService.__new__(StateService)
        service._get_channel_setting = lambda channel_id, key: "google"
        self.assertEqual(service.get_model(1), "hydrogen")
        config = load_config({"DEFAULT_MODEL": "opus-5.5-direct", "MEMORY_MODEL": "sonnet-5.5-direct"})
        self.assertEqual((config.default_model, config.memory_model), ("opus", "sonnet"))
