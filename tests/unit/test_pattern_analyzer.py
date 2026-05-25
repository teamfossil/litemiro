"""PatternAnalyzer 단위 테스트.

LLM 자체는 로컬 ``_FakeLLM`` 으로 대체한다. 검증 포인트:

* 호출 수가 preset 약속 (quick=1, standard=4, full=8) 과 같다
* 입력 통계가 그대로 user 프롬프트에 직렬화된다 (수치 인용)
* PartialInsights items 순서는 prompt 순서와 같다 (결정성)
* tokens_used = prompt_tokens + completion_tokens 합
"""

from __future__ import annotations

import json

from litemiro.models import LLMResponse
from litemiro.phase1.models import Preset
from litemiro.phase3 import (
    AggregationResult,
    PartialInsights,
    PatternAnalyzer,
    ReportConfig,
)
from litemiro.phase3.models import (
    CATEGORY_ACTION_DISTRIBUTION,
    CATEGORY_NETWORK_METRICS,
    CATEGORY_TIME_SERIES,
    CATEGORY_TOPIC_FLOW,
)


class _FakeLLM:
    """결정적 큐 기반 LLM. ``calls`` 에 (system, user, model) 을 기록한다."""

    def __init__(self, *responses: str | LLMResponse) -> None:
        self._queue: list[str | LLMResponse] = list(responses)
        self.calls: list[tuple[str, str, str]] = []

    async def complete(self, *, system: str, user: str, model: str) -> LLMResponse:
        self.calls.append((system, user, model))
        if not self._queue:
            raise AssertionError("FakeLLM exhausted")
        item = self._queue.pop(0)
        return item if isinstance(item, LLMResponse) else LLMResponse(content=item)


def _stub_result() -> AggregationResult:
    return AggregationResult(
        n_events=10,
        n_agents=4,
        n_rounds=2,
        categories={
            CATEGORY_ACTION_DISTRIBUTION: {"counts": {"LIKE_POST": 7}, "total": 10},
            CATEGORY_NETWORK_METRICS: {"n_follow_events": 1},
            CATEGORY_TOPIC_FLOW: {"n_posts": 2},
            CATEGORY_TIME_SERIES: {"rounds": [0, 1]},
        },
    )


class TestQuickPreset:
    async def test_single_call_with_overview_category(self) -> None:
        llm = _FakeLLM(LLMResponse(content="요약입니다.", prompt_tokens=50, completion_tokens=12))
        analyzer = PatternAnalyzer(llm=llm)
        config = ReportConfig(preset=Preset.QUICK, analyzer_model="m-q")

        out = await analyzer.analyze(result=_stub_result(), config=config)

        assert isinstance(out, PartialInsights)
        assert len(out.items) == 1
        assert out.items[0].category == "overview"
        assert out.items[0].summary == "요약입니다."
        assert out.items[0].model == "m-q"
        assert out.items[0].tokens_used == 62
        assert len(llm.calls) == 1
        _, user, model = llm.calls[0]
        assert model == "m-q"
        assert "n_events" in user
        assert "n_agents" in user

    async def test_quick_prompt_includes_all_categories(self) -> None:
        llm = _FakeLLM("ok")
        analyzer = PatternAnalyzer(llm=llm)
        await analyzer.analyze(result=_stub_result(), config=ReportConfig(preset=Preset.QUICK))
        _, user, _ = llm.calls[0]
        for cat in (
            CATEGORY_ACTION_DISTRIBUTION,
            CATEGORY_NETWORK_METRICS,
            CATEGORY_TOPIC_FLOW,
            CATEGORY_TIME_SERIES,
        ):
            assert cat in user


class TestStandardPreset:
    async def test_four_calls_one_per_category(self) -> None:
        llm = _FakeLLM("s1", "s2", "s3", "s4")
        analyzer = PatternAnalyzer(llm=llm)
        config = ReportConfig(preset=Preset.STANDARD)

        out = await analyzer.analyze(result=_stub_result(), config=config)

        assert len(llm.calls) == 4
        categories = [item.category for item in out.items]
        assert categories == [
            CATEGORY_ACTION_DISTRIBUTION,
            CATEGORY_NETWORK_METRICS,
            CATEGORY_TOPIC_FLOW,
            CATEGORY_TIME_SERIES,
        ]

    async def test_each_call_carries_its_own_category_payload(self) -> None:
        llm = _FakeLLM("a", "b", "c", "d")
        analyzer = PatternAnalyzer(llm=llm)
        await analyzer.analyze(result=_stub_result(), config=ReportConfig(preset=Preset.STANDARD))
        _, user_act, _ = llm.calls[0]
        assert '"category": "action_distribution"' in user_act
        _, user_net, _ = llm.calls[1]
        assert '"category": "network_metrics"' in user_net


class TestFullPreset:
    async def test_eight_calls_two_lenses_per_category(self) -> None:
        llm = _FakeLLM(*[f"r{i}" for i in range(8)])
        analyzer = PatternAnalyzer(llm=llm)
        config = ReportConfig(preset=Preset.FULL)

        out = await analyzer.analyze(result=_stub_result(), config=config)

        assert len(llm.calls) == 8
        categories = [item.category for item in out.items]
        assert categories == [
            f"{cat}:{lens}"
            for cat in (
                CATEGORY_ACTION_DISTRIBUTION,
                CATEGORY_NETWORK_METRICS,
                CATEGORY_TOPIC_FLOW,
                CATEGORY_TIME_SERIES,
            )
            for lens in ("macro", "micro")
        ]


class TestSerialization:
    async def test_summary_is_stripped(self) -> None:
        llm = _FakeLLM("  요약  \n")
        analyzer = PatternAnalyzer(llm=llm)
        out = await analyzer.analyze(
            result=_stub_result(), config=ReportConfig(preset=Preset.QUICK)
        )
        assert out.items[0].summary == "요약"

    async def test_payload_is_valid_json(self) -> None:
        llm = _FakeLLM("ok")
        analyzer = PatternAnalyzer(llm=llm)
        await analyzer.analyze(result=_stub_result(), config=ReportConfig(preset=Preset.QUICK))
        _, user, _ = llm.calls[0]
        last_line = user.strip().splitlines()[-1]
        parsed = json.loads(last_line)
        assert parsed["scope"]["n_events"] == 10
