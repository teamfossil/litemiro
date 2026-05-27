"""`PatternAnalyzer` — 카테고리별 통계 → LLM 인사이트.

Preset 이 호출 수를 결정한다 (PRD §4.2 / §6.3, 1-4 회):

* ``quick`` — 1 회. 4 카테고리를 한 묶음으로 요약.
* ``standard`` — 4 회. 카테고리당 1 회 (단일 lens).
* ``full`` — 4 회. 카테고리당 1 회 (macro + micro 양면 지시를 한 응답에 통합).

모든 호출은 ``asyncio.gather(..., return_exceptions=True)`` 로 동시에 실행되며,
카테고리별로 ``tenacity`` 재시도 1 회 + 실패 시 통계 수치만으로 기본 패턴을
서술하는 폴백 (PRD §4.3) 을 제공해 보고서가 통째로 죽지 않게 한다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from tenacity import AsyncRetrying, stop_after_attempt, wait_none

from litemiro.interfaces import LLMClient
from litemiro.phase1.models import Preset
from litemiro.phase3.models import (
    CATEGORIES,
    CATEGORY_ACTION_DISTRIBUTION,
    CATEGORY_NETWORK_METRICS,
    CATEGORY_TIME_SERIES,
    CATEGORY_TOPIC_FLOW,
    AggregationResult,
    CategoryInsight,
    PartialInsights,
    ReportConfig,
)

_STATS_ONLY_MODEL = "statistics-only"
_OVERVIEW_CATEGORY = "overview"

_SYSTEM_PROMPT = (
    "당신은 소셜 미디어 시뮬레이션 결과를 분석하는 데이터 분석가다. "
    "주어진 통계만을 근거로 한국어로 답하라. 핵심 수치는 모두 짚되, "
    "각 수치가 무엇을 시사하는지 (분포 편향·집중도·시간 추세·이상치) 와 "
    "다른 수치와의 관계를 함께 풀어 분석가다운 해석을 제시하라. "
    "길이 제한은 없으니 통계가 풍부하면 단락을 늘려 디테일을 살리고, "
    "필요하면 마크다운 글머리표로 항목을 정리해도 좋다. "
    "수치는 입력 JSON 의 사전 합산값 (`aggregate`, `total`, `n_*`, `total_posts_created` 등) "
    "을 그대로 인용하라. 라운드를 직접 세거나 series 배열을 합산해서 비율을 추정하지 말라 "
    "— 필요한 합계와 비율은 모두 `aggregate` 블록에 이미 계산돼 있다. "
    "'총 게시물' 같은 표현을 쓸 때는 `total_posts_created` (CREATE+QUOTE+REPOST 합) 를 인용하고, "
    "본문 있는 게시물만 가리킬 때는 `n_content_posts` 를 명시한다. "
    "top_* 리스트가 있다면 상위 3 명까지는 실명·수치를 함께 인용하라. "
    "통계 밖의 사실은 추측하지 않으며, 데이터가 없는 항목은 '관측되지 않음' 으로 명시한다."
)

_STANDARD_INSTRUCTION = (
    "이 카테고리의 결과를 분석가의 시선으로 자세히 풀어라. "
    "분포·집중도·상위 행위자·예외 패턴을 모두 다루고, 가능하면 다른 수치와의 관계도 짚어라."
)
_FULL_INSTRUCTION = (
    "두 시각으로 답하라. 먼저 'macro:' 로 시작하는 단락에서 전체 분포·추세·비율을 "
    "정리하고, 이어 'micro:' 로 시작하는 단락에서 이상치·극단치·튀는 라운드와 "
    "상위 행위자의 행동 패턴을 짚어라. 각 단락 안에서는 길이 제한 없이 충분히 풀어라."
)


class PatternAnalyzer:
    def __init__(self, *, llm: LLMClient, max_attempts: int = 2) -> None:
        # max_attempts=2 → 첫 시도 + 재시도 1 회 (PRD §4.3 "tenacity 재시도 1회").
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
        self._llm = llm
        self._max_attempts = max_attempts

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
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        items: list[CategoryInsight] = []
        for (category, _system, _user), outcome in zip(prompts, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                items.append(_statistics_only_insight(category=category, result=result))
            else:
                items.append(outcome)
        return PartialInsights(items=tuple(items))

    async def _call(self, *, category: str, system: str, user: str, model: str) -> CategoryInsight:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_none(),
            reraise=True,
        ):
            with attempt:
                response = await self._llm.complete(system=system, user=user, model=model)
                return CategoryInsight(
                    category=category,
                    summary=response.content.strip(),
                    model=model,
                    tokens_used=response.prompt_tokens + response.completion_tokens,
                )
        raise RuntimeError("AsyncRetrying terminated without success or reraise")


def _build_prompts(result: AggregationResult, preset: Preset) -> list[tuple[str, str, str]]:
    if preset is Preset.QUICK:
        return [(_OVERVIEW_CATEGORY, _SYSTEM_PROMPT, _quick_prompt(result))]
    instruction = _STANDARD_INSTRUCTION if preset is Preset.STANDARD else _FULL_INSTRUCTION
    if preset in (Preset.STANDARD, Preset.FULL):
        return [
            (cat, _SYSTEM_PROMPT, _category_prompt(result, cat, instruction=instruction))
            for cat in CATEGORIES
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


def _category_prompt(result: AggregationResult, category: str, *, instruction: str) -> str:
    payload: dict[str, Any] = {
        "scope": _scope_block(result),
        "category": category,
        "data": dict(result.categories.get(category, {})),
    }
    return f"{instruction}\n{_dump(payload)}"


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


def _statistics_only_insight(*, category: str, result: AggregationResult) -> CategoryInsight:
    summary = _statistics_only_summary(category=category, result=result)
    return CategoryInsight(
        category=category,
        summary=summary,
        model=_STATS_ONLY_MODEL,
        tokens_used=0,
    )


def _statistics_only_summary(*, category: str, result: AggregationResult) -> str:
    """LLM 호출이 모두 실패했을 때의 통계-only 폴백 텍스트.

    카테고리별 핵심 수치를 결정적인 한국어 한두 문장으로 풀어낸다. 같은 입력은
    같은 출력 — 보고서 재현성을 깨지 않는다.
    """

    data = result.categories.get(category, {})
    scope = (
        f"이벤트 {result.n_events}건 / 에이전트 {result.n_agents}명 / 라운드 {result.n_rounds}회"
    )
    header = f"[LLM 분석 실패 — 통계 수치만 인용] {scope}."
    detail: str
    if category == _OVERVIEW_CATEGORY:
        # quick 프리셋 (1 콜, 합성 키 "overview") 폴백: 4 카테고리 통계를 한 번에.
        detail = _format_overview(result)
    elif category == CATEGORY_ACTION_DISTRIBUTION:
        detail = _format_action_distribution(data)
    elif category == CATEGORY_NETWORK_METRICS:
        detail = _format_network_metrics(data)
    elif category == CATEGORY_TOPIC_FLOW:
        detail = _format_topic_flow(data)
    elif category == CATEGORY_TIME_SERIES:
        detail = _format_time_series(data)
    else:
        detail = _format_generic(data)
    return f"{header} {detail}".strip()


def _format_overview(result: AggregationResult) -> str:
    return " ".join(
        [
            _format_action_distribution(result.categories.get(CATEGORY_ACTION_DISTRIBUTION, {})),
            _format_network_metrics(result.categories.get(CATEGORY_NETWORK_METRICS, {})),
            _format_topic_flow(result.categories.get(CATEGORY_TOPIC_FLOW, {})),
            _format_time_series(result.categories.get(CATEGORY_TIME_SERIES, {})),
        ]
    )


def _format_action_distribution(data: Mapping[str, Any]) -> str:
    total = data.get("total", 0)
    counts = data.get("counts") or {}
    if not isinstance(counts, Mapping) or not counts:
        return f"총 {total}건의 액션이 기록됨."
    top = sorted(counts.items(), key=lambda kv: (-int(kv[1] or 0), kv[0]))[:3]
    parts = [f"{name} {count}건" for name, count in top]
    return f"총 {total}건의 액션 중 상위: {', '.join(parts)}."


def _format_network_metrics(data: Mapping[str, Any]) -> str:
    n_follow = data.get("n_follow_events", 0)
    top_followed = data.get("top_followed") or []
    if isinstance(top_followed, list) and top_followed:
        first = top_followed[0]
        aid = first.get("agent_id", "?")
        received = first.get("follows_received", 0)
        return f"FOLLOW 이벤트 {n_follow}건. 최다 피팔로우: {aid} ({received}건)."
    return f"FOLLOW 이벤트 {n_follow}건. 의미 있는 피팔로우 집중 없음."


def _format_topic_flow(data: Mapping[str, Any]) -> str:
    n_content = int(data.get("n_content_posts", data.get("n_posts", 0)) or 0)
    n_amp = int(data.get("n_amplifications", 0) or 0)
    total = int(data.get("total_posts_created", n_content + n_amp) or 0)
    top = data.get("top_posters") or []
    head = f"본문 있는 포스트 {n_content}건 + 리포스트 {n_amp}건 → 신규 포스트 총 {total}건."
    if isinstance(top, list) and top:
        first = top[0]
        aid = first.get("agent_id", "?")
        posts = first.get("posts", 0)
        return f"{head} 최다 작성자: {aid} ({posts}건)."
    return head


def _format_time_series(data: Mapping[str, Any]) -> str:
    rounds = data.get("rounds") or []
    series = data.get("series") or []
    aggregate = data.get("aggregate") if isinstance(data.get("aggregate"), Mapping) else {}
    if isinstance(series, list) and series:
        total_actions = sum(int(s.get("n_actions", 0) or 0) for s in series)
        # 사전 합산값이 있으면 인용하고, 없으면 series 에서 직접 계산 (옛 fixture 호환).
        if aggregate:
            n_zero = int(aggregate.get("n_rounds_zero_do_nothing", 0) or 0)
            avg_ratio = float(aggregate.get("avg_do_nothing_ratio", 0.0) or 0.0)
        else:
            n_zero = sum(1 for s in series if int(s.get("n_do_nothing", 0) or 0) == 0)
            ratios = [float(s.get("do_nothing_ratio", 0.0) or 0.0) for s in series]
            avg_ratio = sum(ratios) / len(ratios) if ratios else 0.0
        return (
            f"총 {len(rounds)}라운드, 누적 액션 {total_actions}건. "
            f"DO_NOTHING 0%인 라운드 {n_zero}/{len(rounds)}, 평균 비율 {avg_ratio:.2f}."
        )
    return f"라운드 수 {len(rounds)}."


def _format_generic(data: Mapping[str, Any]) -> str:
    return f"카테고리 데이터: {_dump(dict(data))}"


__all__ = ["PatternAnalyzer"]
