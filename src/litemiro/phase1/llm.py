"""Phase 1 LLM boundary helpers."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Phase1LLMClient(Protocol):
    async def complete(self, *, system: str, user: str, model: str) -> object: ...


def response_text(response: object) -> str:
    if isinstance(response, str):
        return response

    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content

    raise TypeError("LLM response must be a str or expose a str content attribute")


__all__ = ["Phase1LLMClient", "response_text"]
