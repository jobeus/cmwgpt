"""Text models offered by ``/model``, and which API key each one needs.

A model is only offered (and only used for a channel) when its key is configured.
"""

from dataclasses import dataclass
from typing import List

from src.config import AppConfig

# Placeholder ``load_config`` uses when OPENROUTER_API_KEY is unset (for CI).
_OPENROUTER_PLACEHOLDER_KEY = "test-key-for-ci"

# Models that used to exist; a channel still set to one falls back to the default.
RETIRED_MODELS = {"hybrid"}


@dataclass(frozen=True)
class ModelOption:
    value: str
    label: str
    provider: str  # "openrouter" | "gemini" | "anthropic"


MODEL_OPTIONS: List[ModelOption] = [
    ModelOption("google/gemini-2.5-flash", "google/gemini-2.5-flash", "openrouter"),
    ModelOption("bytedance-seed/seed-2.0-mini", "bytedance-seed/seed-2.0-mini", "openrouter"),
    ModelOption("bytedance-seed/seed-1.6-flash", "bytedance-seed/seed-1.6-flash", "openrouter"),
    ModelOption("qwen/qwen3.5-flash-02-23", "qwen/qwen3.5-flash-02-23", "openrouter"),
    ModelOption("anthropic/claude-sonnet-5.5", "anthropic/claude-sonnet-5.5 (search, web aware)", "openrouter"),
    ModelOption("sonnet-5.5-direct", "sonnet-5.5-direct (Claude API, search, web aware)", "anthropic"),
    ModelOption("opus-5.5-direct", "opus-5.5-direct (Claude API, search, web aware)", "anthropic"),
    ModelOption("google", "google (gemini-3.8-flash, search)", "gemini"),
    ModelOption("google-high", "google-high (gemini-3.8-flash, search, thinking)", "gemini"),
]


def provider_available(provider: str, config: AppConfig) -> bool:
    """Return True if the API key for ``provider`` is configured."""
    if provider == "openrouter":
        key = config.openrouter_api_key
        return bool(key) and key != _OPENROUTER_PLACEHOLDER_KEY
    if provider == "gemini":
        return bool(config.gemini_api_key)
    if provider == "anthropic":
        return bool(config.anthropic_api_key)
    return False


def available_models(config: AppConfig) -> List[ModelOption]:
    """The catalog models whose API key is configured."""
    return [m for m in MODEL_OPTIONS if provider_available(m.provider, config)]


def is_model_unavailable(model: str, config: AppConfig) -> bool:
    """True for a retired model, or a catalog model whose API key is missing.

    Models outside the catalog are left alone (they may be set via DEFAULT_MODEL).
    """
    if model in RETIRED_MODELS:
        return True
    for option in MODEL_OPTIONS:
        if option.value == model:
            return not provider_available(option.provider, config)
    return False
