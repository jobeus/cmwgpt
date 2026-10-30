"""
Image Service - Routes an image generation request to Gemini or Runpod.

Shared by the ``/draw`` command and the chat model's ``draw`` tool.
"""

from typing import Any, List, Optional, Tuple

GEMINI_IMAGE_MODELS = ("gemini-3-pro-image", "gemini-nano-banana-2.1")
# Old model IDs that channels may still have saved as their default.
LEGACY_MODEL_IDS = {"gemini-3.1-flash-image": "gemini-nano-banana-2.1"}


def resolve_draw_model(state_service: Any, channel_id: int, default_draw_model: str,
                       model: Optional[str] = None) -> str:
    """The given model, else the channel's draw model, else the global default."""
    active_model = model or state_service.get_draw_model(channel_id) or default_draw_model
    return LEGACY_MODEL_IDS.get(active_model, active_model)


async def generate_image(
    *,
    prompt: str,
    model: str,
    gemini_service: Any,
    runpod_service: Any,
    images_b64: Optional[List[str]] = None,
    discord_user_id: Optional[int] = None,
    discord_channel_id: Optional[int] = None,
) -> Tuple[bytes, Optional[float]]:
    """Generate an image with ``model``. Returns ``(image bytes, cost or None)``.

    ``images_b64`` are reference images; only the Gemini models use them.
    """
    if model in GEMINI_IMAGE_MODELS:
        return await gemini_service.generate_image(
            prompt=prompt,
            model=model,
            images_b64=images_b64 or [],
            discord_user_id=discord_user_id,
            discord_channel_id=discord_channel_id,
        )
    if runpod_service.has_model(model):
        return await runpod_service.generate_image(
            prompt=prompt,
            model=model,
            discord_user_id=discord_user_id,
            discord_channel_id=discord_channel_id,
        )
    raise Exception(f"Model {model} is not supported or not configured via Runpod.")
