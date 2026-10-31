"""Test helpers for overriding the lazily-cached AppConfig.

Consumers read configuration via ``src.config.get_config()``, which returns the
process-wide cached ``AppConfig`` (``src.config._cached_config``). Tests can
therefore inject specific values by patching that cached object, e.g.::

    with patch("src.config._cached_config", cfg(groq_api_key="groq-key")):
        ...

``cfg()`` builds an AppConfig from deterministic defaults with the given
overrides applied.
"""

from dataclasses import replace

from src.config import AppConfig, load_config

# Deterministic base config (no .env, empty environment).
_BASE: AppConfig = load_config(env={}, load_env_file=False)


def cfg(**overrides) -> AppConfig:
    """Return the base test config with ``overrides`` applied."""
    return replace(_BASE, **overrides)


class AsyncDictCache(dict):
    """A dict with :class:`~src.utils.cache_utils.PersistentCache`'s async methods.

    Patch it in for a module's cache; tests can still check it like a dict.
    """

    async def get(self, key, default=None):  # type: ignore[override]
        return dict.get(self, key, default)

    async def set(self, key, value):
        self[key] = value

    async def delete(self, key):
        self.pop(key, None)
