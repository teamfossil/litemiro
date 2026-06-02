"""ReportValidator — 보고서 구조·근거 계약 검증.

Composer 가 출력한 Markdown 이 아래 두 계약을 만족하는지 검사한다:
1. 구조 계약: 5개 필수 섹션 헤딩 + 1섹션 실질 내용
2. 근거 계약: evidence_pack 이 있으면 인용된 [Exxx] ID 가 팩 안에 실재

검증 실패 시 ``ValidationResult.to_repair_prompt()`` 로 Composer 재작성을 요청한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

from litemiro.phase3.models import CATEGORY_TOPIC_FLOW, AggregationResult

_logger = structlog.get_logger(__name__)

REQUIRED_HEADINGS: tuple[str, ...] = (
    "## 1. 핵심 여론 예측",
    "## 2. 입장 분포",
    "## 3. 주요 논점",
    "## 4. 여론 주도·확산",
    "## 5. 신뢰도와 한계",
)

_EVIDENCE_ID_RE = re.compile(r"\[E(\d{3})\]")
_SEC1_RE = re.compile(r"## 1\. 핵심 여론 예측(.*?)(?=## 2\.|\Z)", re.DOTALL)


@dataclass(frozen=True)
class ValidationResult:
    """검증 결과 — errors 가 비면 ok=True."""

    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_repair_prompt(self) -> str:
        """오류 목록을 Composer 재작성 지시문으로 변환."""
        lines = [
            "이전 보고서에서 다음 오류가 발견됐다. 모두 수정해서 다시 작성하라:",
        ]
        for e in self.errors:
            lines.append(f"- [오류] {e}")
        for w in self.warnings:
            lines.append(f"- [경고] {w}")
        return "\n".join(lines)


class ReportValidator:
    """보고서 Markdown 검증기.

    ``validate(markdown, result)`` 를 호출하면 ``ValidationResult`` 를 반환한다.
    ReportComposer 에 선택적으로 주입 (``validator=ReportValidator()``) 하면
    compose 시 repair loop 가 활성화된다.
    """

    def validate(self, markdown: str, result: AggregationResult) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        # 1. 5개 섹션 헤딩 존재
        for heading in REQUIRED_HEADINGS:
            if heading not in markdown:
                errors.append(f"필수 섹션 누락: '{heading}'")

        # 2. 1섹션 실질 내용 (60자 이상)
        m = _SEC1_RE.search(markdown)
        if m:
            body = m.group(1).strip()
            if len(body) < 60:
                errors.append(
                    "1섹션(핵심 여론 예측)이 너무 짧다 — "
                    "이슈에 대한 여론 결론을 구체적으로 서술하라."
                )

        # 3. evidence ID 유효성 (evidence_pack 이 있을 때만)
        topic_flow = result.categories.get(CATEGORY_TOPIC_FLOW)
        evidence_pack = topic_flow.get("evidence_pack") if isinstance(topic_flow, dict) else None
        if evidence_pack:
            valid_ids = {
                item["id"] for item in evidence_pack if isinstance(item, dict) and "id" in item
            }
            cited_ids = {f"E{n}" for n in _EVIDENCE_ID_RE.findall(markdown)}
            if not cited_ids:
                errors.append(
                    "evidence pack 이 있는데 인용이 0건이다 — "
                    "증거 은행에서 최소 1건 이상 [Exxx] 형식으로 인용하라."
                )
            else:
                invalid = cited_ids - valid_ids
                if invalid:
                    errors.append(
                        f"존재하지 않는 evidence ID 인용: {sorted(invalid)} — "
                        "증거 은행에 있는 ID 만 사용하라."
                    )

        _logger.debug(
            "report_validation",
            ok=len(errors) == 0,
            n_errors=len(errors),
            n_warnings=len(warnings),
        )
        return ValidationResult(
            ok=len(errors) == 0,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )


__all__ = ["ReportValidator", "ValidationResult"]
