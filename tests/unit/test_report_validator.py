"""ReportValidator 단위 테스트."""

from __future__ import annotations

from litemiro.phase3 import AggregationResult
from litemiro.phase3.models import PhenomenaMetrics, QaMetrics
from litemiro.phase3.report_validator import ReportValidator, ValidationResult

_VALID_MD = """\
# 보고서

## 1. 핵심 여론 예측
이번 토론에서 가상 여론은 AI 규제 강화 방향으로 강하게 수렴했다. 지지 기류가 압도적이며 반대 목소리는 거의 관측되지 않았다.

## 2. 입장 분포
찬성이 다수를 점하며 중립은 소수였다.

## 3. 주요 논점
주요 논점은 접근성과 감시권이었다.

## 4. 여론 주도·확산
agent_0029 가 핵심 담론을 생성했다.

## 5. 신뢰도와 한계
표본이 작아 일반화에 한계가 있다.
"""


def _result(evidence_pack: list | None = None) -> AggregationResult:
    topic_flow: dict = {"n_posts": 1}
    if evidence_pack is not None:
        topic_flow["evidence_pack"] = evidence_pack
    return AggregationResult(
        n_events=4,
        n_agents=2,
        n_rounds=2,
        categories={
            "action_distribution": {"total": 4},
            "network_metrics": {"n_follow_events": 0},
            "topic_flow": topic_flow,
            "time_series": {"rounds": [0, 1]},
        },
        qa_metrics=QaMetrics(
            action_entropy_normalized=0.5,
            follow_clustering_coefficient=0.0,
            content_word_entropy_normalized=0.8,
        ),
        phenomena=PhenomenaMetrics(
            cascade_max_depth=0,
            cascade_max_breadth=0,
            cascade_max_scale=0,
            n_cascades=0,
            popularity_gini=0.0,
        ),
    )


class TestValidMarkdown:
    def test_valid_report_passes(self) -> None:
        vr = ReportValidator().validate(_VALID_MD, _result())
        assert vr.ok
        assert vr.errors == ()

    def test_validation_result_frozen(self) -> None:
        vr = ValidationResult(ok=True, errors=(), warnings=())
        assert vr.ok is True


class TestSectionHeadings:
    def test_missing_section_is_error(self) -> None:
        md = _VALID_MD.replace("## 3. 주요 논점", "## 3. 잘못된 헤딩")
        vr = ReportValidator().validate(md, _result())
        assert not vr.ok
        assert any("주요 논점" in e for e in vr.errors)

    def test_all_five_missing_gives_five_errors(self) -> None:
        vr = ReportValidator().validate("# 제목만 있는 보고서\n내용.", _result())
        assert not vr.ok
        assert len(vr.errors) == 5

    def test_section1_too_short_is_error(self) -> None:
        md = _VALID_MD.replace(
            "이번 토론에서 가상 여론은 AI 규제 강화 방향으로 강하게 수렴했다. 지지 기류가 압도적이며 반대 목소리는 거의 관측되지 않았다.",
            "짧음.",
        )
        vr = ReportValidator().validate(md, _result())
        assert not vr.ok
        assert any("너무 짧다" in e for e in vr.errors)


class TestEvidenceIDValidation:
    def test_valid_evidence_id_passes(self) -> None:
        pack = [{"id": "E001", "agent_id": "agent_0029", "quote": "테스트"}]
        md = _VALID_MD + '\n"테스트" - agent_0029 [E001]\n'
        vr = ReportValidator().validate(md, _result(evidence_pack=pack))
        assert vr.ok

    def test_invalid_evidence_id_is_error(self) -> None:
        pack = [{"id": "E001", "agent_id": "agent_0029", "quote": "테스트"}]
        md = _VALID_MD + '\n"없는 발화" - agent_0029 [E999]\n'
        vr = ReportValidator().validate(md, _result(evidence_pack=pack))
        assert not vr.ok
        assert any("E999" in e for e in vr.errors)

    def test_no_evidence_pack_skips_id_check(self) -> None:
        md = _VALID_MD + "\n[E999] 이건 근거 팩이 없으면 무시된다.\n"
        vr = ReportValidator().validate(md, _result(evidence_pack=None))
        assert vr.ok


class TestRepairPrompt:
    def test_repair_prompt_contains_errors(self) -> None:
        vr = ValidationResult(
            ok=False,
            errors=("섹션 누락",),
            warnings=("경고 사항",),
        )
        prompt = vr.to_repair_prompt()
        assert "섹션 누락" in prompt
        assert "경고 사항" in prompt
        assert "[오류]" in prompt
        assert "[경고]" in prompt
