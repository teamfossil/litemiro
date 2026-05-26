"""Phase 1 → Phase 2 단일 경계.

`docs/integration/phase1-2-contract.md` Section 4-6 의 매핑/검증 규칙을
구현한다. 호출자는 Section 5 후반의 wiring 예시처럼 세 staticmethod 를
조합해 `StateStore` 를 만든다.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from jsonschema import Draft7Validator, FormatChecker
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from litemiro.models import Agent
from litemiro.phase1.models import (
    AgentOrigin,
    AgentProfile,
    MemoryStore,
    OntologyA,
    OntologyB,
    SemanticMemory,
)
from litemiro.schemas import ontology_a_schema, ontology_b_schema
from litemiro.social.graph import SocialGraph

if TYPE_CHECKING:
    from litemiro.interfaces import EmbedderLike

log = structlog.get_logger(__name__)

_MEMORY_TOP_N = 3
# §6.5 — 옵션 B (#58) 의 디폴트 cosine 임계값. 실측 calibration 후 hard-error
# 승격과 함께 재조정 (§8.4). 0.4 는 sentence-transformers all-MiniLM-L6-v2
# 의 한국어 페르소나-NER 엔티티 짝에서 "의미 매칭 약함" 가드로 시작값.
_DEFAULT_SIMILARITY_THRESHOLD = 0.4


class ConsistencyWarning(BaseModel):
    """`OntologyLoader.validate_consistency` 결과 단위.

    Contract Section 6.5 에서 hard-error 승격 판단 데이터로 쓰인다 — 이슈 #21
    task 2 의 누적 비율 측정에 ``origin`` 분류가 필요해 함께 보존한다.

    ``max_similarity`` 는 옵션 B (`#58`) 의 임베딩 cosine 경로에서만 채워진다 —
    legacy set intersection 경로에서는 ``None``. threshold calibration 시 분포를
    보기 위해 함께 노출.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str
    origin: AgentOrigin
    persona_topics: tuple[str, ...]
    memory_topics: tuple[str, ...]
    max_similarity: float | None = Field(default=None, ge=-1.0, le=1.0)


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
    def validate_consistency(
        *,
        ontology_a: OntologyA,
        ontology_b: OntologyB,
        embedder: EmbedderLike | None = None,
        similarity_threshold: float = _DEFAULT_SIMILARITY_THRESHOLD,
    ) -> tuple[ConsistencyWarning, ...]:
        """Section 6.5 페르소나-메모리 어휘 정합성 검출.

        각 에이전트의 ``AgentProfile.topics`` 와 ``SemanticMemory.topics`` 합집합을
        비교한다. 빈 ``semantic`` 리스트는 cold start 로 면제. 반환은 결정적
        튜플 (agent_id 사전순).

        비교 방식은 ``embedder`` 유무로 분기 (`#58`):

        * ``embedder`` 가 주어지면 두 토픽 묶음을 임베딩 후 max pairwise cosine 이
          ``similarity_threshold`` 미만이면 warning. 페르소나 토픽 (LLM 추상 개념)
          과 메모리 토픽 (NER 엔티티) 이 어휘공간을 달리해도 의미 매칭이 잡힌다.
        * ``embedder`` 가 없으면 (legacy) set intersection — 어휘 매칭이 안 되는
          쌍은 의미가 가까워도 모두 warning. 단위 테스트가 임베딩 모델 로딩 없이
          돌게 두는 백워드 호환 경로.

        MVP 는 warning 만 — hard-error 승격은 옵션 B threshold calibration 측정
        뒤 결정 (이슈 #21 / contract §8.4).
        """
        warnings: list[ConsistencyWarning] = []
        embed_cache: dict[str, tuple[float, ...]] = {}

        for aid in sorted(ontology_a.agents):
            profile = ontology_a.agents[aid]
            store = ontology_b.stores.get(aid)
            memories = store.semantic if store else []
            if not memories:
                continue
            memory_topics: set[str] = set().union(*(set(m.topics) for m in memories))
            persona_topics = set(profile.topics)

            max_similarity: float | None
            if embedder is None:
                if persona_topics & memory_topics:
                    continue
                max_similarity = None
            else:
                max_similarity = _max_pairwise_cosine(
                    persona_topics, memory_topics, embedder=embedder, cache=embed_cache
                )
                if max_similarity >= similarity_threshold:
                    continue

            warning = ConsistencyWarning(
                agent_id=aid,
                origin=profile.origin,
                persona_topics=tuple(sorted(persona_topics)),
                memory_topics=tuple(sorted(memory_topics)),
                max_similarity=max_similarity,
            )
            log.warning(
                "ontology_loader.persona_memory_mismatch",
                agent_id=warning.agent_id,
                origin=warning.origin.value,
                persona_topics=warning.persona_topics,
                memory_topics=warning.memory_topics,
                max_similarity=warning.max_similarity,
            )
            warnings.append(warning)
        return tuple(warnings)

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


def _max_pairwise_cosine(
    persona_topics: set[str],
    memory_topics: set[str],
    *,
    embedder: EmbedderLike,
    cache: dict[str, tuple[float, ...]],
) -> float:
    """페르소나-메모리 토픽 쌍 중 가장 높은 cosine 유사도.

    한 쪽이 비면 매칭 자체가 불가능하니 0.0 (= "전혀 안 닮음") 으로 떨어진다 —
    threshold 가 양수면 자동으로 warning 후보가 된다. 임베딩은 ``cache`` 로
    재사용해 같은 토픽 문자열을 두 번 임베딩하지 않는다.
    """
    if not persona_topics or not memory_topics:
        return 0.0

    def _embed(text: str) -> tuple[float, ...]:
        if text not in cache:
            cache[text] = embedder.embed(text)
        return cache[text]

    persona_vecs = [_embed(t) for t in persona_topics]
    memory_vecs = [_embed(t) for t in memory_topics]
    return max(_cosine(pv, mv) for pv in persona_vecs for mv in memory_vecs)


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """일반 cosine. STEmbedder 는 이미 L2 정규화돼 dot product 와 동치지만
    fake / 미정규화 embedder 도 같은 함수로 닫기 위해 정의대로 계산한다."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _build_agent(profile: AgentProfile, store: MemoryStore | None) -> Agent:
    return Agent(
        agent_id=profile.agent_id,
        interests=tuple(profile.topics),
        persona_traits=profile.model_dump(mode="json"),
        memory_summary=_memory_summary(store.semantic if store else []),
        activation_rate=profile.behavior_tendency.post_rate,
    )


__all__ = ["ConsistencyWarning", "OntologyLoader"]
