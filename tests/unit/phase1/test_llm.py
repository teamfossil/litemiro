"""Phase 1 LLM boundary tests."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from litemiro.phase1.llm import response_text


@dataclass(frozen=True)
class _LLMResponseShape:
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


def test_response_text_accepts_str() -> None:
    assert response_text("raw json") == "raw json"


def test_response_text_accepts_content_response() -> None:
    assert response_text(_LLMResponseShape(content='{"ok": true}')) == '{"ok": true}'


def test_response_text_rejects_unknown_shape() -> None:
    with pytest.raises(TypeError, match="LLM response"):
        response_text(object())
