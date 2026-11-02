"""Text models offered by ``/model``, and which API key each one needs.

A model is only offered (and only used for a channel) when its key is configured.
"""

from dataclasses import dataclass
from typing import List

from src.config import AppConfig


@dataclass(frozen=True)
class ModelOption:
    value: str
    label: str
    provider: str  # "gemini" | "anthropic"


MODEL_OPTIONS: List[ModelOption] = [
    ModelOption("sonnet-5.5-direct", "sonnet-5.5-direct (Claude API, search, web aware)", "anthropic"),
    ModelOption("opus-5.5-direct", "opus-5.5-direct (Claude API, search, web aware)", "anthropic"),
    ModelOption("google", "google (gemini-3.8-flash, search)", "gemini"),
    ModelOption("google-high", "google-high (gemini-3.8-flash, search, thinking)", "gemini"),
]


def provider_available(provider: str, config: AppConfig) -> bool:
    """Return True if the API key for ``provider`` is configured."""
    if provider == "gemini":
        return bool(config.gemini_api_key)
    if provider == "anthropic":
        return bool(config.anthropic_api_key)
    return False


def available_models(config: AppConfig) -> List[ModelOption]:
    """The catalog models whose API key is configured."""
    return [m for m in MODEL_OPTIONS if provider_available(m.provider, config)]


def is_model_unavailable(model: str, config: AppConfig) -> bool:
    """True for a model outside the catalog (such as a retired OpenRouter one), or one whose API key is missing."""
    for option in MODEL_OPTIONS:
        if option.value == model:
            return not provider_available(option.provider, config)
    return True
