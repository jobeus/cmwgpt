"""Text models offered by ``/model``, and which API key each one needs.

A model is only offered (and only used for a channel) when its key is configured.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from src.config import AppConfig


@dataclass(frozen=True)
class ModelOption:
    value: str
    label: str
    provider: str  # "gemini" | "anthropic"


MODEL_OPTIONS: List[ModelOption] = [
    ModelOption("sonnet", "sonnet (claude-sonnet-5.5)", "anthropic"),
    ModelOption("opus", "opus (claude-opus-5.5)", "anthropic"),
    ModelOption("haiku", "haiku (claude-haiku-5.5)", "anthropic"),
    ModelOption("hydrogen", "hydrogen (gemini-4-hydrogen)", "gemini"),
    ModelOption("argon", "argon (gemini-4-argon)", "gemini"),
]

# Names a channel or an env var may still hold from before the models were renamed.
LEGACY_MODEL_NAMES = {
    "sonnet-5.5-direct": "sonnet",
    "opus-5.5-direct": "opus",
    "haiku-5.5-direct": "haiku",
    "google": "hydrogen",
    "google-high": "hydrogen",
}


def canonical_model(model: str) -> str:
    """``model`` under its current name (an old name maps to the model that replaced it)."""
    return LEGACY_MODEL_NAMES.get(model, model)


def model_provider(model: str) -> Optional[str]:
    """Which API answers for ``model``: "anthropic", "gemini", or None for a model outside the catalog."""
    for option in MODEL_OPTIONS:
        if option.value == model:
            return option.provider
    return None


def provider_available(provider: str, config: "AppConfig") -> bool:
    """Return True if the API key for ``provider`` is configured."""
    if provider == "gemini":
        return bool(config.gemini_api_key)
    if provider == "anthropic":
        return bool(config.anthropic_api_key)
    return False


def available_models(config: "AppConfig") -> List[ModelOption]:
    """The catalog models whose API key is configured."""
    return [m for m in MODEL_OPTIONS if provider_available(m.provider, config)]


def is_model_unavailable(model: str, config: "AppConfig") -> bool:
    """True for a model outside the catalog (such as a retired OpenRouter one), or one whose API key is missing."""
    provider = model_provider(model)
    return provider is None or not provider_available(provider, config)
