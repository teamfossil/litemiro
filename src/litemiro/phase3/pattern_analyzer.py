"""`PatternAnalyzer` — 카테고리별 통계 → LLM 인사이트.

Preset 이 호출 수를 결정한다 (Section 3 Phase 3 노트):

* ``quick`` — 1 회. 4 카테고리를 한 묶음으로 요약.
* ``standard`` — 4 회. 카테고리당 1 회.
* ``full`` — 8 회. 카테고리당 macro / micro 2 회.

모든 호출은 ``asyncio.gather`` 로 동시에 실행된다. LLM 의 호출 순서는
결과 ``items`` 순서를 결정하지 않는다 — 입력 순서를 보존해 결정성을 유지한다.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from litemiro.interfaces import LLMClient
from litemiro.phase1.models import Preset
from litemiro.phase3.models import (
    CATEGORIES,
    AggregationResult,
    CategoryInsight,
    PartialInsights,
    ReportConfig,
)

_FULL_LENSES: tuple[str, ...] = ("macro", "micro")

_SYSTEM_PROMPT = (
    "당신은 소셜 미디어 시뮬레이션 결과를 분석하는 데이터 분석가다. "
    "주어진 통계만을 근거로 한국어로 2-4 문장 안에 요약하라. "
    "수치를 그대로 인용하되 통계 밖의 사실을 추측하지 않는다."
)

_LENS_INSTRUCTION: dict[str | None, str] = {
    "macro": "전체 분포·추세·비율 중심으로 요약하라.",
    "micro": "이상치·극단치·특정 라운드의 튀는 지점을 짚어라.",
    None: "이 카테고리의 결과를 요약하라.",
}


class PatternAnalyzer:
    def __init__(self, *, llm: LLMClient) -> None:
        self._llm = llm

    async def analyze(
        self,
        *,
        result: AggregationResult,
        config: ReportConfig,
    ) -> PartialInsights:
        prompts = _build_prompts(result, config.preset)
        tasks = [
            self._call(category=cat, system=system, user=user, model=config.analyzer_model)
            for cat, system, user in prompts
        ]
        items = await asyncio.gather(*tasks)
        return PartialInsights(items=tuple(items))

    async def _call(self, *, category: str, system: str, user: str, model: str) -> CategoryInsight:
        response = await self._llm.complete(system=system, user=user, model=model)
        return CategoryInsight(
            category=category,
            summary=response.content.strip(),
            model=model,
            tokens_used=response.prompt_tokens + response.completion_tokens,
        )


def _build_prompts(result: AggregationResult, preset: Preset) -> list[tuple[str, str, str]]:
    if preset is Preset.QUICK:
        return [("overview", _SYSTEM_PROMPT, _quick_prompt(result))]
    if preset is Preset.STANDARD:
        return [
            (cat, _SYSTEM_PROMPT, _category_prompt(result, cat, lens=None)) for cat in CATEGORIES
        ]
    if preset is Preset.FULL:
        return [
            (
                f"{cat}:{lens}",
                _SYSTEM_PROMPT,
                _category_prompt(result, cat, lens=lens),
            )
            for cat in CATEGORIES
            for lens in _FULL_LENSES
        ]
    raise ValueError(f"unsupported preset: {preset}")


def _quick_prompt(result: AggregationResult) -> str:
    payload = {
        "scope": _scope_block(result),
        "categories": {cat: dict(result.categories.get(cat, {})) for cat in CATEGORIES},
    }
    return (
        "다음 시뮬레이션 통계 전체를 요약하라. 각 카테고리의 핵심을 1 문장씩 다뤄라.\n"
        f"{_dump(payload)}"
    )


def _category_prompt(result: AggregationResult, category: str, *, lens: str | None) -> str:
    payload: dict[str, Any] = {
        "scope": _scope_block(result),
        "category": category,
        "data": dict(result.categories.get(category, {})),
    }
    return f"{_LENS_INSTRUCTION[lens]}\n{_dump(payload)}"


def _scope_block(result: AggregationResult) -> dict[str, int]:
    return {
        "n_events": result.n_events,
        "n_agents": result.n_agents,
        "n_rounds": result.n_rounds,
    }


def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_safe)


def _safe(obj: Any) -> Any:
    if isinstance(obj, set | frozenset):
        return sorted(obj)
    return str(obj)


__all__ = ["PatternAnalyzer"]
