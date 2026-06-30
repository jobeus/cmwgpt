"""Shared model-routing for chat completions.

The mention handler and the interject service both turn a prepared message
array into a reply by routing across the same three paths:

* ``hybrid``      — phase 1 ``google-high`` (Gemini) summarises + web-searches,
                    phase 2 Sonnet (OpenRouter) writes the final reply;
* a Gemini model  — single Gemini call;
* anything else   — single OpenRouter call.

This module owns that routing (and the identity kwargs threaded into each
service call) so the decision lives in exactly one place. Everything that
legitimately differs between the two callers — the trigger/guard logic, history
fetching, the hybrid prompt wording, and how the reply is delivered — stays with
the caller and is supplied via :class:`HybridConfig` and ``identity``.
"""

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from src.services.gemini_service import is_gemini_model, get_thinking_level

logger = logging.getLogger(__name__)

ReplyContent = Union[str, Dict[str, Any], None]


@dataclass
class HybridConfig:
    """Caller-specific pieces of the two-phase hybrid pipeline."""

    summary_prompt: str
    phase2_system_prompt: str
    build_phase2_text: Callable[[str], str]


async def dispatch_completion(
    *,
    model: str,
    messages: List[Dict[str, Any]],
    system_prompt: Optional[str],
    openai_service: Any,
    gemini_service: Any,
    identity: Dict[str, Any],
    hybrid: Optional[HybridConfig] = None,
    label: str = "",
) -> Tuple[ReplyContent, float]:
    """Route ``messages`` to the right model(s) and return ``(reply, cost)``.

    Args:
        model: The selected model (or ``"hybrid"``).
        messages: The prepared multimodal message array.
        system_prompt: System prompt for the single-call paths.
        openai_service / gemini_service: The completion services.
        identity: Kwargs (``channel_id``/``bot_id``/``discord_user_id``) splatted
            into every service call. Each caller supplies its own set.
        hybrid: Required when ``model == "hybrid"``; ignored otherwise.
        label: Optional tag for the hybrid phase log lines.
    """
    reply_content: ReplyContent = None
    cost = 0.0
    tag = f" ({label})" if label else ""

    if model == "hybrid" and gemini_service and hybrid is not None:
        # Phase 1: Google-High to summarize and search
        logger.info(f"Hybrid phase 1{tag}: Sending to Google-High for summary...")
        summary_content, gemini_cost = await gemini_service.get_chat_completion(
            model="google-high",
            messages=messages,
            system_prompt=hybrid.summary_prompt,
            thinking_level=get_thinking_level("google-high"),
            disable_cache=True,
            **identity,
        )

        if summary_content:
            if isinstance(summary_content, dict) and "text" in summary_content:
                summary_text = summary_content["text"]
            else:
                summary_text = str(summary_content)

            # Phase 2: Sonnet to write the reply from the briefing
            logger.info(f"Hybrid phase 2{tag}: Passing summary to Sonnet...")
            phase2_messages = [{
                "role": "user",
                "content": [{"type": "text", "text": hybrid.build_phase2_text(summary_text)}],
            }]
            reply_content, sonnet_cost = await openai_service.get_chat_completion(
                model="anthropic/claude-sonnet-5",
                messages=phase2_messages,
                system_prompt=hybrid.phase2_system_prompt,
                search=False,
                **identity,
            )
            cost = gemini_cost + sonnet_cost

    elif is_gemini_model(model) and gemini_service:
        reply_content, cost = await gemini_service.get_chat_completion(
            model=model,
            messages=messages,
            system_prompt=system_prompt,
            thinking_level=get_thinking_level(model),
            **identity,
        )
    else:
        reply_content, cost = await openai_service.get_chat_completion(
            model=model,
            messages=messages,
            system_prompt=system_prompt,
            **identity,
        )

    return reply_content, cost
