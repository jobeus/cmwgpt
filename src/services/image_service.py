"""
Image Service - Gemini's image models and the options each one takes.

Shared by the ``/draw`` and ``/edit`` commands and the chat model's ``draw`` and
``edit_image`` tools. Editing is the same call as drawing, with the image to edit
first among the reference images.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

STANDARD_ASPECT_RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
# Only banana-2.1 takes these.
EXTREME_ASPECT_RATIOS = ("1:4", "4:1", "1:8", "8:1")
ASPECT_RATIOS = STANDARD_ASPECT_RATIOS + EXTREME_ASPECT_RATIOS
IMAGE_SIZES = ("1K", "2K", "4K")
THINKING_LEVELS = ("minimal", "medium", "high")
# "web" grounds the image in Google web results; "web+image" adds Google image search.
SEARCH_MODES = ("web", "web+image")

# Draws are widescreen unless asked otherwise; edits keep the shape of the image being edited.
DRAW_ASPECT_RATIO = "16:9"
# The most images one request sends the model (it takes up to 14).
MAX_REFERENCE_IMAGES = 14


@dataclass(frozen=True)
class ImageModel:
    id: str
    name: str
    aspect_ratios: Tuple[str, ...]
    sizes: Tuple[str, ...]
    # Empty when the level can't be set.
    thinking_levels: Tuple[str, ...]
    searches: Tuple[str, ...]
    # USD per 1M tokens.
    price_input: float
    price_text_output: float
    price_image_output: float


IMAGE_MODELS: Dict[str, ImageModel] = {
    m.id: m for m in (
        ImageModel("gemini-nano-banana-2.1", "banana-2.1", ASPECT_RATIOS, IMAGE_SIZES,
                   THINKING_LEVELS, SEARCH_MODES, 1.50, 7.50, 30.0),
        # Always thinks, at a level that can't be set.
        ImageModel("gemini-3-pro-image", "banana-pro", STANDARD_ASPECT_RATIOS, IMAGE_SIZES,
                   (), ("web",), 2.00, 12.00, 120.0),
        ImageModel("gemini-3.1-flash-lite-image", "banana-2-lite", STANDARD_ASPECT_RATIOS, ("1K",),
                   ("minimal", "high"), (), 0.25, 1.50, 30.0),
    )
}
DEFAULT_IMAGE_MODEL = "gemini-nano-banana-2.1"


@dataclass(frozen=True)
class ImageOptions:
    aspect_ratio: Optional[str] = None
    size: Optional[str] = None
    thinking: Optional[str] = None
    search: Optional[str] = None

    def describe(self) -> str:
        """The options that were set, like ``9:16, 2K, search web``."""
        parts = [p for p in (self.aspect_ratio, self.size) if p]
        if self.thinking:
            parts.append(f"thinking {self.thinking}")
        if self.search:
            parts.append(f"search {self.search}")
        return ", ".join(parts)


@dataclass(frozen=True)
class ImageResult:
    image: bytes
    # Whatever the model said alongside the image (often empty).
    text: str
    cost: float


class ImageOptionError(ValueError):
    """An option the chosen model doesn't support."""


def check_options(model: ImageModel, options: ImageOptions) -> None:
    """Raise ``ImageOptionError`` naming the first option ``model`` can't do."""
    def check(name: str, value: Optional[str], allowed: Tuple[str, ...]) -> None:
        if value is None or value in allowed:
            return
        if not allowed:
            raise ImageOptionError(f"{model.name} doesn't take a {name}.")
        raise ImageOptionError(f"{model.name} doesn't support {name} {value}; it takes {', '.join(allowed)}.")

    check("aspect ratio", options.aspect_ratio, model.aspect_ratios)
    check("size", options.size, model.sizes)
    check("thinking level", options.thinking, model.thinking_levels)
    check("search", options.search, model.searches)


def find_image_model(value: Optional[str]) -> Optional[ImageModel]:
    """The model with this ID or name, if there is one."""
    if not value:
        return None
    return IMAGE_MODELS.get(value) or next((m for m in IMAGE_MODELS.values() if m.name == value), None)


def resolve_image_model(state_service: Any, channel_id: int, default_model: Optional[str],
                        model: Optional[str] = None) -> ImageModel:
    """The given model, else the channel's image model, else the configured default.

    Unknown values (such as a retired Runpod model still saved for a channel) are skipped.
    """
    for candidate in (model, state_service.get_image_model(channel_id), default_model):
        found = find_image_model(candidate)
        if found:
            return found
    return IMAGE_MODELS[DEFAULT_IMAGE_MODEL]


async def generate_image(
    *,
    prompt: str,
    model: ImageModel,
    gemini_service: Any,
    images_b64: Optional[List[str]] = None,
    options: Optional[ImageOptions] = None,
) -> ImageResult:
    """Make an image with ``model``. ``images_b64`` are reference images; to edit one, put it first.

    Raises ``ImageOptionError`` if ``model`` doesn't support one of ``options``.
    """
    options = options or ImageOptions()
    check_options(model, options)
    return await gemini_service.generate_image(
        prompt=prompt,
        model=model,
        images_b64=(images_b64 or [])[:MAX_REFERENCE_IMAGES],
        options=options,
    )
