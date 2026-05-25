"""``LiteLLMClient`` — production ``LLMClient`` adapter (W3).

Bridges ``litellm.acompletion`` to B's tiny ``LLMClient`` Protocol.
Retry, JSON repair, and DO_NOTHING fallback all live inside
``ActionSelector`` already, so this adapter is intentionally thin:
format the two-turn messages, await the response, and surface the
content alongside whatever token usage the backend reported.

API key and base URL fall back to ``OPENROUTER_API_KEY`` and
``OPENROUTER_BASE_URL``; the latter defaults to OpenRouter's public
endpoint so a freshly-cloned repo with only ``OPENROUTER_API_KEY``
set just works. Models follow OpenRouter naming
(``openrouter/anthropic/claude-3.5-sonnet``).

``max_output_tokens`` is plumbed straight to ``litellm.acompletion``'s
``max_tokens`` so OpenRouter doesn't pre-bill the model's full context
window. Set ``LITEMIRO_MAX_OUTPUT_TOKENS`` for an env-level cap; when
both call site and env are silent we leave the kwarg off and let the
backend pick its own ceiling.

Usage extraction is best-effort: litellm normalises the OpenAI-style
``usage`` block onto the response, but local fakes and self-hosted
endpoints sometimes omit it. Missing or malformed counts collapse to
zero so ``LLMMeta.tokens_used`` stays a non-negative int rather than
crashing the round.
"""

from __future__ import annotations

import os
from typing import Any

import litellm

from litemiro.models import LLMResponse

_OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_MAX_OUTPUT_TOKENS_ENV = "LITEMIRO_MAX_OUTPUT_TOKENS"


def _resolve_max_output_tokens(explicit: int | None) -> int | None:
    """Explicit arg wins; otherwise pull a positive int from the env var."""
    if explicit is not None:
        return explicit if explicit > 0 else None
    raw = os.environ.get(_MAX_OUTPUT_TOKENS_ENV)
    if raw is None or not raw.strip():
        return None
    try:
        parsed = int(raw)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _coerce_token_count(value: Any) -> int:
    """Map a usage field to a non-negative ``int``.

    litellm normally reports usage as ``int`` but some providers send
    it as ``str`` or omit it entirely. We never want a malformed usage
    block to crash the round, so anything that doesn't cleanly become
    a non-negative integer collapses to zero.
    """
    if value is None:
        return 0
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return 0
    return coerced if coerced >= 0 else 0


class LiteLLMClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = 30.0,
        max_output_tokens: int | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY")
        self._base_url = (
            base_url
            if base_url is not None
            else os.environ.get("OPENROUTER_BASE_URL", _OPENROUTER_DEFAULT_BASE_URL)
        )
        self._timeout_seconds = timeout_seconds
        self._max_output_tokens = _resolve_max_output_tokens(max_output_tokens)

    async def complete(self, *, system: str, user: str, model: str) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": self._api_key,
            "base_url": self._base_url,
            "timeout": self._timeout_seconds,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self._max_output_tokens is not None:
            kwargs["max_tokens"] = self._max_output_tokens
        response: Any = await litellm.acompletion(**kwargs)
        content = response.choices[0].message.content
        text = "" if content is None else str(content)
        prompt_tokens, completion_tokens = _extract_usage(response)
        return LLMResponse(
            content=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )


def _extract_usage(response: Any) -> tuple[int, int]:
    """Pull prompt/completion token counts off a litellm response.

    litellm exposes usage either as an attribute or a dict-like field.
    Both paths are tolerated; anything we can't parse is reported as
    zero rather than raising.
    """
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return 0, 0
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    if prompt is None and isinstance(usage, dict):
        prompt = usage.get("prompt_tokens")
    if completion is None and isinstance(usage, dict):
        completion = usage.get("completion_tokens")
    return _coerce_token_count(prompt), _coerce_token_count(completion)


__all__ = ["LiteLLMClient"]
