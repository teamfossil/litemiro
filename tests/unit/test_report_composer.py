"""ReportComposer 단위 테스트.

핵심 계약은 두 가지:
1. 정상 경로 — primary 모델만 호출, Markdown 그대로 반환, ``fallback_used=False``.
2. 폴백 경로 — primary 가 예외를 던지면 fallback 모델로 한 번 더 호출,
   ``fallback_used=True``. 폴백도 실패하면 그 예외는 그대로 전파한다.
"""

from __future__ import annotations

import pytest

from litemiro.models import LLMResponse
from litemiro.phase3 import (
    AggregationResult,
    CategoryInsight,
    PartialInsights,
    ReportComposer,
    ReportConfig,
)


class _FakeLLM:
    """모델 ID 별로 응답 또는 예외를 큐잉한다."""

    def __init__(self) -> None:
        self._per_model: dict[str, list[LLMResponse | BaseException]] = {}
        self.calls: list[tuple[str, str, str]] = []

    def queue(self, model: str, *items: LLMResponse | str | BaseException) -> None:
        bucket = self._per_model.setdefault(model, [])
        for item in items:
            bucket.append(LLMResponse(content=item) if isinstance(item, str) else item)

    async def complete(self, *, system: str, user: str, model: str) -> LLMResponse:
        self.calls.append((system, user, model))
        bucket = self._per_model.get(model)
        if not bucket:
            raise AssertionError(f"FakeLLM: no queued response for model={model}")
        item = bucket.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _result() -> AggregationResult:
    return AggregationResult(
        n_events=4,
        n_agents=2,
        n_rounds=2,
        categories={
            "action_distribution": {"total": 4},
            "network_metrics": {"n_follow_events": 0},
            "topic_flow": {"n_posts": 1},
            "time_series": {"rounds": [0, 1]},
        },
    )


def _insights() -> PartialInsights:
    return PartialInsights(
        items=(
            CategoryInsight(category="overview", summary="전반 요약.", model="m", tokens_used=10),
        )
    )


class TestPrimaryPath:
    async def test_returns_markdown_from_primary(self) -> None:
        llm = _FakeLLM()
        llm.queue(
            "primary-m",
            LLMResponse(content="# 보고서\n\n본문", prompt_tokens=30, completion_tokens=120),
        )
        composer = ReportComposer(llm=llm)
        config = ReportConfig(
            composer_primary_model="primary-m", composer_fallback_model="fallback-m"
        )

        out = await composer.compose(result=_result(), insights=_insights(), config=config)

        assert out.markdown == "# 보고서\n\n본문"
        assert out.model == "primary-m"
        assert out.fallback_used is False
        assert out.tokens_used == 150
        assert [c[2] for c in llm.calls] == ["primary-m"]

    async def test_user_prompt_includes_scope_and_insights(self) -> None:
        llm = _FakeLLM()
        llm.queue("primary-m", "ok")
        composer = ReportComposer(llm=llm)
        await composer.compose(
            result=_result(),
            insights=_insights(),
            config=ReportConfig(composer_primary_model="primary-m"),
        )
        _, user, _ = llm.calls[0]
        assert "이벤트 수: 4" in user
        assert "에이전트 수: 2" in user
        assert "라운드 수: 2" in user
        assert "overview" in user
        assert "전반 요약." in user


class TestFallback:
    async def test_falls_back_when_primary_raises(self) -> None:
        llm = _FakeLLM()
        llm.queue("primary-m", RuntimeError("primary down"))
        llm.queue(
            "fallback-m",
            LLMResponse(content="폴백 본문", prompt_tokens=20, completion_tokens=40),
        )
        composer = ReportComposer(llm=llm)
        config = ReportConfig(
            composer_primary_model="primary-m", composer_fallback_model="fallback-m"
        )

        out = await composer.compose(result=_result(), insights=_insights(), config=config)

        assert out.markdown == "폴백 본문"
        assert out.model == "fallback-m"
        assert out.fallback_used is True
        assert out.tokens_used == 60
        assert [c[2] for c in llm.calls] == ["primary-m", "fallback-m"]

    async def test_fallback_failure_propagates(self) -> None:
        llm = _FakeLLM()
        llm.queue("primary-m", RuntimeError("primary down"))
        llm.queue("fallback-m", RuntimeError("fallback also down"))
        composer = ReportComposer(llm=llm)
        config = ReportConfig(
            composer_primary_model="primary-m", composer_fallback_model="fallback-m"
        )

        with pytest.raises(RuntimeError, match="fallback also down"):
            await composer.compose(result=_result(), insights=_insights(), config=config)
        assert len(llm.calls) == 2
