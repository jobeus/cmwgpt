"""Shared model-routing for chat completions.

Callers (the mention handler, the memory job, deathwatch) turn a prepared message array
into a reply by routing to:

* a Gemini model  — single Gemini call;
* anything else   — single Claude API call (``AnthropicService``).

This module owns that routing (and the identity kwargs threaded into each
service call) so the decision lives in exactly one place.
"""

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from src.services.gemini_service import is_gemini_model, get_thinking_level

ReplyContent = Union[str, Dict[str, Any], None]


async def dispatch_completion(
    *,
    model: str,
    messages: List[Dict[str, Any]],
    system_prompt: Optional[str],
    gemini_service: Any,
    anthropic_service: Any,
    identity: Dict[str, Any],
    search: bool = True,
    on_text: Optional[Callable[[str], None]] = None,
    effort: Optional[str] = None,
    tools: Optional[Sequence[Any]] = None,
    on_status: Optional[Callable[[str], None]] = None,
    max_tokens: Optional[int] = None,
) -> Tuple[ReplyContent, float]:
    """Route ``messages`` to the right model and return ``(reply, cost)``.

    Args:
        model: The selected model.
        messages: The prepared multimodal message array.
        system_prompt: System prompt for the call.
        gemini_service / anthropic_service: The completion services.
        identity: Kwargs (``channel_id``/``discord_user_id``) splatted
            into every service call. Each caller supplies its own set.
        search: Whether the Claude API route may use web search.
        on_text: Called with the reply so far while it streams in. The Claude API route
            streams; Gemini returns the whole reply at once.
        effort: Overrides the model's default effort level on the Claude API route;
            Gemini ignores it.
        tools: Client tools (``ChatTool``) the model may call. Only the Claude API route
            offers them.
        on_status: Called with what the model is doing (thinking, searching, a tool call)
            while no new text is coming. Only the Claude API route reports it.
        max_tokens: Caps each Claude API request's output, thinking included; unset, the
            service default. Gemini ignores it.
    """
    if is_gemini_model(model):
        return await gemini_service.get_chat_completion(
            model=model,
            messages=messages,
            system_prompt=system_prompt,
            thinking_level=get_thinking_level(model),
            **identity,
        )
    return await anthropic_service.get_chat_completion(
        model=model,
        messages=messages,
        system_prompt=system_prompt,
        search=search,
        on_text=on_text,
        effort=effort,
        tools=tools,
        on_status=on_status,
        **({"max_tokens": max_tokens} if max_tokens else {}),
        **identity,
    )
