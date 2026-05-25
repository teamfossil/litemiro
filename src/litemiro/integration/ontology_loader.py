"""Phase 1 → Phase 2 단일 경계.

`docs/integration/phase1-2-contract.md` Section 4-6 의 매핑/검증 규칙을
구현한다. 호출자는 Section 5 후반의 wiring 예시처럼 세 staticmethod 를
조합해 `StateStore` 를 만든다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from jsonschema import Draft7Validator, FormatChecker
from pydantic import ValidationError

from litemiro.models import Agent
from litemiro.phase1.models import (
    AgentProfile,
    MemoryStore,
    OntologyA,
    OntologyB,
    SemanticMemory,
)
from litemiro.schemas import ontology_a_schema, ontology_b_schema
from litemiro.social.graph import SocialGraph

log = structlog.get_logger(__name__)

_MEMORY_TOP_N = 3


class OntologyLoader:
    @staticmethod
    def load(
        *,
        ontology_a_path: Path,
        ontology_b_path: Path,
    ) -> tuple[OntologyA, OntologyB]:
        """JSON 파일 → 검증된 Pydantic 객체.

        Section 6 의 4 가지 검증 순서로 실행한다:
        (1) jsonschema (Draft 7) — wire 포맷 게이트
        (2) Pydantic — Python 타입 게이트
        (3) 참조 일관성 — `set(B.stores) == set(A.agents)`
        (4) agent_count 일관성 — `len(A.agents) == A.agent_count`

        실패는 모두 ``ValueError`` 로 통일 (Pydantic 의 ``ValidationError``
        는 호출자가 분기하기 어려우므로 메시지를 보존하여 wrap).
        """
        payload_a = _read_json(ontology_a_path)
        payload_b = _read_json(ontology_b_path)

        _validate_schema(payload_a, ontology_a_schema(), label="ontology_a")
        _validate_schema(payload_b, ontology_b_schema(), label="ontology_b")

        try:
            ontology_a = OntologyA.model_validate(payload_a)
        except ValidationError as exc:
            raise ValueError(f"ontology_a Pydantic 검증 실패: {exc}") from exc
        try:
            ontology_b = OntologyB.model_validate(payload_b)
        except ValidationError as exc:
            raise ValueError(f"ontology_b Pydantic 검증 실패: {exc}") from exc

        _check_reference_consistency(ontology_a, ontology_b)
        return ontology_a, ontology_b

    @staticmethod
    def build_agents(
        *,
        ontology_a: OntologyA,
        ontology_b: OntologyB,
    ) -> tuple[Agent, ...]:
        """Section 4.1 매핑 + 4.2 ``memory_summary`` 알고리즘.

        ``agent_id`` 사전순으로 정렬한 튜플을 돌려준다 (Section 5 결정성).
        ``OntologyB`` 에 store 가 없는 에이전트는 cold start 로 간주하여
        ``memory_summary=None`` (Section 6 검증이 store 누락을 이미 거르므로
        이 분기는 방어적 폴백).
        """
        return tuple(
            _build_agent(ontology_a.agents[aid], ontology_b.stores.get(aid))
            for aid in sorted(ontology_a.agents)
        )

    @staticmethod
    def build_social_graph(*, ontology_a: OntologyA) -> SocialGraph:
        """Section 4.3 매핑.

        self-follow 는 `AgentProfile._no_self_follow` 가 모델 생성 단계에서
        이미 제거하지만 belt-and-suspenders 로 한 번 더 거른다. 미지의
        `agent_id` 를 가리키는 엣지는 무시하고 경고 로그를 남긴다.
        """
        known = set(ontology_a.agents)
        edges: dict[str, list[str]] = {}
        for aid, profile in ontology_a.agents.items():
            kept: list[str] = []
            dropped_unknown: list[str] = []
            for followee in profile.initial_following:
                if followee == aid:
                    continue
                if followee not in known:
                    dropped_unknown.append(followee)
                    continue
                kept.append(followee)
            if dropped_unknown:
                log.warning(
                    "ontology_loader.unknown_follow_dropped",
                    follower=aid,
                    dropped=tuple(dropped_unknown),
                )
            if kept:
                edges[aid] = kept
        return SocialGraph.from_dict(edges)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"ontology JSON 읽기 실패: {path} ({exc})") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"ontology JSON 파싱 실패: {path} ({exc.msg})") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"ontology JSON 루트는 object 여야 함: {path}")
    return payload


def _validate_schema(payload: dict[str, Any], schema: dict[str, Any], *, label: str) -> None:
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path))
    if not errors:
        return
    messages = [
        f"{('/'.join(map(str, err.absolute_path)) or '<root>')}: {err.message}" for err in errors
    ]
    raise ValueError(f"{label} jsonschema 검증 실패: " + "; ".join(messages))


def _check_reference_consistency(ontology_a: OntologyA, ontology_b: OntologyB) -> None:
    if len(ontology_a.agents) != ontology_a.agent_count:
        raise ValueError(
            "agent_count 불일치: "
            f"len(agents)={len(ontology_a.agents)} != agent_count={ontology_a.agent_count}"
        )
    a_ids = set(ontology_a.agents)
    b_ids = set(ontology_b.stores)
    if a_ids != b_ids:
        only_a = sorted(a_ids - b_ids)
        only_b = sorted(b_ids - a_ids)
        raise ValueError(
            f"agent_id 참조 불일치: OntologyA-only={only_a or '∅'}, OntologyB-only={only_b or '∅'}"
        )


def _memory_summary(semantic: list[SemanticMemory]) -> str | None:
    """Section 4.2 top-N concat.

    정렬 키: (`simulation_count desc`, `last_relevant_sim desc`, `id asc`).
    빈 리스트는 ``None`` — `Agent.memory_summary: str | None` 계약과 정렬.
    """
    if not semantic:
        return None
    ordered = sorted(semantic, key=lambda m: (-m.simulation_count, -m.last_relevant_sim, m.id))
    return "; ".join(m.summary for m in ordered[:_MEMORY_TOP_N])


def _build_agent(profile: AgentProfile, store: MemoryStore | None) -> Agent:
    return Agent(
        agent_id=profile.agent_id,
        interests=tuple(profile.topics),
        persona_traits=profile.model_dump(mode="json"),
        memory_summary=_memory_summary(store.semantic if store else []),
        activation_rate=profile.behavior_tendency.post_rate,
    )


__all__ = ["OntologyLoader"]
