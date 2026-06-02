"""ReportValidator — 보고서 구조·근거 계약 검증.

Composer 가 출력한 Markdown 이 아래 세 계약을 만족하는지 검사한다:
1. 구조 계약: 5개 필수 섹션 헤딩 + 1섹션 실질 내용
2. 근거 계약: evidence_pack 이 있으면 인용된 [Exxx] ID 가 팩 안에 실재
3. 인용 무결성: 인용 패턴 ``"..." - agent_XXXX [Exxx]`` 에서 agent_id·quote 본문이
   evidence pack 과 일치하는지 근사 검증 (경고 수준)

검증 실패 시 ``ValidationResult.to_repair_prompt()`` 로 Composer 재작성을 요청한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

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

# "발화 본문" - agent_XXXX [E001] 형식 인용 파싱 (6자 이상 인용문만 대상)
# 하이픈(-) + EN DASH (U+2013) 모두 허용 - Composer 가 en dash 를 쓰는 경우 대비
_CITATION_RE = re.compile(r'"([^"]{6,})"\s*[-' + "\u2013" + r"]\s*([\w_]+)\s*\[E(\d{3})\]")

# quote 근사 매칭에 사용할 앞부분 길이
_QUOTE_SNIPPET_LEN = 20


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

            # 4. 인용 무결성 — agent_id · quote 근사 매칭 (경고 수준)
            warnings.extend(_check_citation_integrity(markdown, evidence_pack, valid_ids))

        _logger.debug(
            "report_validation",
            ok=len(errors) == 0,
            n_errors=len(errors),
            n_warnings=len(warnings),
        )
        if warnings:
            _logger.info("report_citation_warnings", n=len(warnings), details=warnings)
        return ValidationResult(
            ok=len(errors) == 0,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )


def _check_citation_integrity(
    markdown: str,
    evidence_pack: list[Any],
    valid_ids: set[str],
) -> list[str]:
    """인용 패턴 ``"..." - agent_XXXX [Exxx]`` 에서 agent_id·quote 근사 검증.

    agent_id 불일치는 다른 에이전트 발화를 잘못 귀속한 할루시네이션 신호.
    quote 불일치는 증거 은행에 없는 본문을 지어낸 신호.
    두 검증 모두 warning 수준 — 정당한 요약 인용·번역·축약 가능성을 남겨둔다.
    """
    id_to_item: dict[str, dict[str, Any]] = {
        item["id"]: item for item in evidence_pack if isinstance(item, dict) and "id" in item
    }
    warnings: list[str] = []
    for m in _CITATION_RE.finditer(markdown):
        quoted_text = m.group(1)
        cited_agent = m.group(2)
        eid = f"E{m.group(3)}"
        if eid not in valid_ids:
            continue  # 존재하지 않는 ID 는 이미 error 로 처리됨
        item = id_to_item.get(eid)
        if not item:
            continue
        # agent_id 일치 확인
        pack_agent = item.get("agent_id", "")
        if pack_agent and cited_agent != pack_agent:
            warnings.append(
                f"[{eid}] agent_id 불일치: 보고서는 '{cited_agent}' 이나 "
                f"evidence pack 은 '{pack_agent}' — 할루시네이션 의심."
            )
        # quote 근사 매칭 — 인용문 앞부분이 pack quote 에 포함되는지
        # 공백 정규화(내부 이중 공백 등) 후 비교 — LLM 이 공백을 흔히 정규화해 false positive 방지
        # pack quote 는 content[:200] 로 잘리므로 원문 200자 이후 인용은 false positive 가능
        pack_quote = item.get("quote", "")
        snippet = re.sub(r"\s+", " ", quoted_text[:_QUOTE_SNIPPET_LEN]).strip()
        pack_quote_norm = re.sub(r"\s+", " ", pack_quote)
        if snippet and pack_quote_norm and snippet not in pack_quote_norm:
            warnings.append(
                f"[{eid}] 인용 본문이 evidence pack 에 없음: '{snippet}…' — 변형·날조 인용 의심."
            )
    return warnings


__all__ = ["ReportValidator", "ValidationResult"]
