"""Shared model-routing for chat completions.

The mention handler turns a prepared message array into a reply by routing to:

* a Gemini model  — single Gemini call;
* a direct model  — single Claude API call (``AnthropicService``, no OpenRouter);
* anything else   — single OpenRouter call.

This module owns that routing (and the identity kwargs threaded into each
service call) so the decision lives in exactly one place.
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from src.services.anthropic_service import is_direct_anthropic_model
from src.services.gemini_service import is_gemini_model, get_thinking_level

logger = logging.getLogger(__name__)

ReplyContent = Union[str, Dict[str, Any], None]


async def dispatch_completion(
    *,
    model: str,
    messages: List[Dict[str, Any]],
    system_prompt: Optional[str],
    openai_service: Any,
    gemini_service: Any,
    identity: Dict[str, Any],
    anthropic_service: Any = None,
    search: bool = True,
    on_text: Optional[Callable[[str], None]] = None,
    effort: Optional[str] = None,
) -> Tuple[ReplyContent, float]:
    """Route ``messages`` to the right model(s) and return ``(reply, cost)``.

    Args:
        model: The selected model.
        messages: The prepared multimodal message array.
        system_prompt: System prompt for the single-call paths.
        openai_service / gemini_service / anthropic_service: The completion services.
        identity: Kwargs (``channel_id``/``bot_id``/``discord_user_id``) splatted
            into every service call. Each caller supplies its own set.
        search: Whether the OpenRouter and Claude API routes may use web search.
        on_text: Called with the reply so far while it streams in. The OpenRouter and
            Claude API routes stream; Gemini returns the whole reply at once.
        effort: Overrides the model's default effort level on the Claude API route;
            the other routes ignore it.
    """
    reply_content: ReplyContent = None
    cost = 0.0

    if is_gemini_model(model) and gemini_service:
        reply_content, cost = await gemini_service.get_chat_completion(
            model=model,
            messages=messages,
            system_prompt=system_prompt,
            thinking_level=get_thinking_level(model),
            **identity,
        )
    elif is_direct_anthropic_model(model) and anthropic_service:
        reply_content, cost = await anthropic_service.get_chat_completion(
            model=model,
            messages=messages,
            system_prompt=system_prompt,
            search=search,
            on_text=on_text,
            effort=effort,
            **identity,
        )
    else:
        reply_content, cost = await openai_service.get_chat_completion(
            model=model,
            messages=messages,
            system_prompt=system_prompt,
            search=search,
            on_text=on_text,
            **identity,
        )

    return reply_content, cost
