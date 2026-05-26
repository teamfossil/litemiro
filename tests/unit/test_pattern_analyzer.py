"""PatternAnalyzer 단위 테스트.

LLM 자체는 로컬 ``_FakeLLM`` 으로 대체한다. 검증 포인트:

* 호출 수가 preset 약속 (quick=1, standard=4, full=4) 과 같다
* 입력 통계가 그대로 user 프롬프트에 직렬화된다 (수치 인용)
* PartialInsights items 순서는 prompt 순서와 같다 (결정성)
* tokens_used = prompt_tokens + completion_tokens 합
* 카테고리별로 tenacity 재시도 1회 + 실패시 통계-only 폴백 (PRD §4.3)
"""

from __future__ import annotations

import asyncio
import json
import re

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
    QaMetrics,
)


def _zero_qa() -> QaMetrics:
    return QaMetrics(
        action_entropy_normalized=0.0,
        follow_clustering_coefficient=0.0,
        content_word_entropy_normalized=0.0,
    )


def _to_item(item: str | LLMResponse | BaseException) -> LLMResponse | BaseException:
    if isinstance(item, LLMResponse | BaseException):
        return item
    return LLMResponse(content=item)


_CATEGORY_PATTERN = re.compile(r'"category":\s*"([^"]+)"')


def _category_of_user(user: str) -> str:
    """user 프롬프트에서 카테고리 식별. quick 프리셋은 'overview' 로 처리."""

    match = _CATEGORY_PATTERN.search(user)
    return match.group(1) if match else "overview"


class _FakeLLM:
    """카테고리별 큐 기반 LLM. 동시 호출에서도 카테고리별로 결정적이다.

    asyncio.gather 가 카테고리 task 를 동시에 schedule 해도, 각 task 는 자기
    카테고리 큐에서 FIFO 로 응답을 가져오므로 ordering 흔들림이 없다.
    """

    def __init__(self) -> None:
        self._per_category: dict[str, list[LLMResponse | BaseException]] = {}
        self._lock = asyncio.Lock()
        self.calls: list[tuple[str, str, str]] = []

    def queue(self, category: str, *items: str | LLMResponse | BaseException) -> None:
        bucket = self._per_category.setdefault(category, [])
        for item in items:
            bucket.append(_to_item(item))

    async def complete(self, *, system: str, user: str, model: str) -> LLMResponse:
        async with self._lock:
            self.calls.append((system, user, model))
            category = _category_of_user(user)
            bucket = self._per_category.get(category)
            if not bucket:
                raise AssertionError(f"FakeLLM exhausted for category={category!r} (queue empty)")
            item = bucket.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def calls_for(self, category: str) -> int:
        return sum(1 for _s, user, _m in self.calls if _category_of_user(user) == category)


def _stub_result() -> AggregationResult:
    return AggregationResult(
        n_events=10,
        n_agents=4,
        n_rounds=2,
        categories={
            CATEGORY_ACTION_DISTRIBUTION: {
                "counts": {"LIKE_POST": 7, "FOLLOW": 2, "CREATE_POST": 1},
                "total": 10,
            },
            CATEGORY_NETWORK_METRICS: {
                "n_follow_events": 2,
                "top_followed": [{"agent_id": "a-002", "follows_received": 2}],
            },
            CATEGORY_TOPIC_FLOW: {
                "n_posts": 1,
                "top_posters": [{"agent_id": "a-003", "posts": 1}],
            },
            CATEGORY_TIME_SERIES: {
                "rounds": [0, 1],
                "series": [
                    {"round_num": 0, "n_actions": 5, "n_do_nothing": 1, "do_nothing_ratio": 0.2},
                    {"round_num": 1, "n_actions": 5, "n_do_nothing": 0, "do_nothing_ratio": 0.0},
                ],
            },
        },
        qa_metrics=_zero_qa(),
    )


class TestQuickPreset:
    async def test_single_call_with_overview_category(self) -> None:
        llm = _FakeLLM()
        llm.queue(
            "overview", LLMResponse(content="요약입니다.", prompt_tokens=50, completion_tokens=12)
        )
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
        llm = _FakeLLM()
        llm.queue("overview", "ok")
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
        llm = _FakeLLM()
        for cat, body in (
            (CATEGORY_ACTION_DISTRIBUTION, "s1"),
            (CATEGORY_NETWORK_METRICS, "s2"),
            (CATEGORY_TOPIC_FLOW, "s3"),
            (CATEGORY_TIME_SERIES, "s4"),
        ):
            llm.queue(cat, body)
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
        llm = _FakeLLM()
        for cat in (
            CATEGORY_ACTION_DISTRIBUTION,
            CATEGORY_NETWORK_METRICS,
            CATEGORY_TOPIC_FLOW,
            CATEGORY_TIME_SERIES,
        ):
            llm.queue(cat, "ok")
        analyzer = PatternAnalyzer(llm=llm)
        await analyzer.analyze(result=_stub_result(), config=ReportConfig(preset=Preset.STANDARD))
        users_by_cat = {_category_of_user(c[1]): c[1] for c in llm.calls}
        assert '"category": "action_distribution"' in users_by_cat[CATEGORY_ACTION_DISTRIBUTION]
        assert '"category": "network_metrics"' in users_by_cat[CATEGORY_NETWORK_METRICS]


class TestFullPreset:
    """full=4 — 카테고리당 1콜 + macro/micro 양면 지시를 한 응답에 통합 (PRD §4.2 / §6.3)."""

    async def test_four_calls_one_per_category(self) -> None:
        llm = _FakeLLM()
        for cat, body in (
            (CATEGORY_ACTION_DISTRIBUTION, "macro: ... \nmicro: ..."),
            (CATEGORY_NETWORK_METRICS, "macro: ... \nmicro: ..."),
            (CATEGORY_TOPIC_FLOW, "macro: ... \nmicro: ..."),
            (CATEGORY_TIME_SERIES, "macro: ... \nmicro: ..."),
        ):
            llm.queue(cat, body)
        analyzer = PatternAnalyzer(llm=llm)
        config = ReportConfig(preset=Preset.FULL)

        out = await analyzer.analyze(result=_stub_result(), config=config)

        assert len(llm.calls) == 4
        categories = [item.category for item in out.items]
        assert categories == [
            CATEGORY_ACTION_DISTRIBUTION,
            CATEGORY_NETWORK_METRICS,
            CATEGORY_TOPIC_FLOW,
            CATEGORY_TIME_SERIES,
        ]

    async def test_full_prompt_carries_macro_and_micro_instruction(self) -> None:
        llm = _FakeLLM()
        for cat in (
            CATEGORY_ACTION_DISTRIBUTION,
            CATEGORY_NETWORK_METRICS,
            CATEGORY_TOPIC_FLOW,
            CATEGORY_TIME_SERIES,
        ):
            llm.queue(cat, "ok")
        analyzer = PatternAnalyzer(llm=llm)
        await analyzer.analyze(result=_stub_result(), config=ReportConfig(preset=Preset.FULL))
        for _system, user, _model in llm.calls:
            assert "macro:" in user
            assert "micro:" in user


class TestSerialization:
    async def test_summary_is_stripped(self) -> None:
        llm = _FakeLLM()
        llm.queue("overview", "  요약  \n")
        analyzer = PatternAnalyzer(llm=llm)
        out = await analyzer.analyze(
            result=_stub_result(), config=ReportConfig(preset=Preset.QUICK)
        )
        assert out.items[0].summary == "요약"

    async def test_payload_is_valid_json(self) -> None:
        llm = _FakeLLM()
        llm.queue("overview", "ok")
        analyzer = PatternAnalyzer(llm=llm)
        await analyzer.analyze(result=_stub_result(), config=ReportConfig(preset=Preset.QUICK))
        _, user, _ = llm.calls[0]
        last_line = user.strip().splitlines()[-1]
        parsed = json.loads(last_line)
        assert parsed["scope"]["n_events"] == 10


class TestResilience:
    """카테고리별 내성 — PRD §4.3 'tenacity 재시도 1회 → 실패 시 통계 수치만으로 기본 패턴 서술'."""

    async def test_single_category_failure_yields_statistics_only_fallback(self) -> None:
        """한 카테고리 503 → 다른 3개는 LLM 인사이트, 실패 1개는 통계-only 폴백."""

        llm = _FakeLLM()
        # action_distribution 만 두 번 다 503. 나머지 3 카테고리는 정상 응답.
        llm.queue(
            CATEGORY_ACTION_DISTRIBUTION,
            RuntimeError("Qwen 503 #1"),
            RuntimeError("Qwen 503 #2"),
        )
        llm.queue(CATEGORY_NETWORK_METRICS, "네트워크 인사이트")
        llm.queue(CATEGORY_TOPIC_FLOW, "토픽 인사이트")
        llm.queue(CATEGORY_TIME_SERIES, "시계열 인사이트")
        analyzer = PatternAnalyzer(llm=llm)

        out = await analyzer.analyze(
            result=_stub_result(), config=ReportConfig(preset=Preset.STANDARD)
        )

        assert len(out.items) == 4
        by_cat = out.by_category()
        # 실패 카테고리는 statistics-only 모델 + 통계 텍스트.
        failed = by_cat[CATEGORY_ACTION_DISTRIBUTION]
        assert failed.model == "statistics-only"
        assert failed.tokens_used == 0
        assert "LLM 분석 실패" in failed.summary
        assert "이벤트 10건" in failed.summary
        # 나머지 3 카테고리는 LLM 응답이 그대로.
        assert by_cat[CATEGORY_NETWORK_METRICS].summary == "네트워크 인사이트"
        assert by_cat[CATEGORY_TOPIC_FLOW].summary == "토픽 인사이트"
        assert by_cat[CATEGORY_TIME_SERIES].summary == "시계열 인사이트"
        # 실패 카테고리는 재시도 1회 = 2 호출, 나머지는 1 호출.
        assert llm.calls_for(CATEGORY_ACTION_DISTRIBUTION) == 2
        assert llm.calls_for(CATEGORY_NETWORK_METRICS) == 1

    async def test_tenacity_retries_once_then_succeeds(self) -> None:
        """첫 시도 503, 두 번째 시도 성공 → 폴백 안 타고 LLM 응답 그대로."""

        llm = _FakeLLM()
        llm.queue(CATEGORY_ACTION_DISTRIBUTION, RuntimeError("transient"), "복구된 인사이트")
        for cat in (
            CATEGORY_NETWORK_METRICS,
            CATEGORY_TOPIC_FLOW,
            CATEGORY_TIME_SERIES,
        ):
            llm.queue(cat, "ok")
        analyzer = PatternAnalyzer(llm=llm)

        out = await analyzer.analyze(
            result=_stub_result(), config=ReportConfig(preset=Preset.STANDARD)
        )

        recovered = out.by_category()[CATEGORY_ACTION_DISTRIBUTION]
        assert recovered.summary == "복구된 인사이트"
        assert recovered.model != "statistics-only"
        assert llm.calls_for(CATEGORY_ACTION_DISTRIBUTION) == 2

    async def test_all_categories_failing_returns_only_statistics_fallback(self) -> None:
        llm = _FakeLLM()
        for cat in (
            CATEGORY_ACTION_DISTRIBUTION,
            CATEGORY_NETWORK_METRICS,
            CATEGORY_TOPIC_FLOW,
            CATEGORY_TIME_SERIES,
        ):
            llm.queue(cat, RuntimeError("down 1"), RuntimeError("down 2"))
        analyzer = PatternAnalyzer(llm=llm)

        out = await analyzer.analyze(
            result=_stub_result(), config=ReportConfig(preset=Preset.STANDARD)
        )

        assert len(out.items) == 4
        assert all(item.model == "statistics-only" for item in out.items)
        # 카테고리별 통계 키워드가 텍스트에 들어가 있는지.
        by_cat = out.by_category()
        assert "총 10건의 액션" in by_cat[CATEGORY_ACTION_DISTRIBUTION].summary
        assert "FOLLOW 이벤트 2건" in by_cat[CATEGORY_NETWORK_METRICS].summary
        assert "포스트 1건" in by_cat[CATEGORY_TOPIC_FLOW].summary
        assert "2라운드" in by_cat[CATEGORY_TIME_SERIES].summary

    async def test_quick_preset_failure_yields_aggregated_statistics_fallback(self) -> None:
        """quick 프리셋 (1 콜, category='overview') 폴백은 4 카테고리 통계를 한 줄로 합성한다.

        회귀 가드: `_statistics_only_summary` 가 `_format_generic({})` 분기로 빠져
        `카테고리 데이터: {}` 같은 빈 텍스트만 남기던 회귀 (#49) 가 다시 들어오지
        않도록 4 카테고리 핵심 수치가 모두 포함되는지 검증한다.
        """

        llm = _FakeLLM()
        llm.queue("overview", RuntimeError("down 1"), RuntimeError("down 2"))
        analyzer = PatternAnalyzer(llm=llm)

        out = await analyzer.analyze(
            result=_stub_result(), config=ReportConfig(preset=Preset.QUICK)
        )

        assert len(out.items) == 1
        item = out.items[0]
        assert item.category == "overview"
        assert item.model == "statistics-only"
        assert item.tokens_used == 0
        assert "LLM 분석 실패" in item.summary
        # 4 카테고리 핵심 수치가 모두 한 줄에 들어가야 한다.
        assert "총 10건의 액션" in item.summary
        assert "FOLLOW 이벤트 2건" in item.summary
        assert "포스트 1건" in item.summary
        assert "2라운드" in item.summary
        # 빈 dict 회귀 가드.
        assert "카테고리 데이터: {}" not in item.summary
        assert llm.calls_for("overview") == 2
