"""``LiteLLMClient`` — production ``LLMClient`` adapter (W3).

Bridges ``litellm.acompletion`` to B's tiny ``LLMClient`` Protocol.
Retry, JSON repair, and DO_NOTHING fallback all live inside
``ActionSelector`` already, so this adapter is intentionally thin:
format the two-turn messages, await the response, return the string
content.

API key and base URL fall back to ``OPENROUTER_API_KEY`` and
``OPENROUTER_BASE_URL``; the latter defaults to OpenRouter's public
endpoint so a freshly-cloned repo with only ``OPENROUTER_API_KEY``
set just works. Models follow OpenRouter naming
(``openrouter/anthropic/claude-3.5-sonnet``).
"""

from __future__ import annotations

import os
from typing import Any

import litellm

_OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class LiteLLMClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = 30.0,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY")
        self._base_url = (
            base_url
            if base_url is not None
            else os.environ.get("OPENROUTER_BASE_URL", _OPENROUTER_DEFAULT_BASE_URL)
        )
        self._timeout_seconds = timeout_seconds

    async def complete(self, *, system: str, user: str, model: str) -> str:
        response: Any = await litellm.acompletion(
            model=model,
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout_seconds,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = response.choices[0].message.content
        return "" if content is None else str(content)


__all__ = ["LiteLLMClient"]
