"""End-to-end smoke: Phase 1 출력 → Phase 2 입력 변환 계약 검증.

본 모듈은 :doc:`/integration/phase1-2-contract` (PR #12) 의 매핑 규칙을
``OntologyLoader`` 호출로 lock-in 한다 (Section 8.1, owner=A).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from litemiro.core.agent_scheduler import AgentScheduler
from litemiro.core.state_store import StateStore
from litemiro.integration.ontology_loader import OntologyLoader
from litemiro.phase1.models import (
    AgentOrigin,
    AgentProfile,
    BehaviorTendency,
    KeyRelationship,
    MemoryStore,
    Ontology,
    OntologyA,
    OntologyB,
    Preset,
    SemanticMemory,
)
from litemiro.social.graph import SocialGraph

# ── fixtures ─────────────────────────────────────────────────────────


def _profile(
    aid: str,
    *,
    topics: list[str],
    post_rate: float,
    following: Iterable[str] = (),
) -> AgentProfile:
    return AgentProfile(
        agent_id=aid,
        name=f"agent-{aid}",
        entity_type="Journalist",
        origin=AgentOrigin.EXTRACTED,
        topics=topics,
        personality="중립적이고 분석적",
        speech_style="~다 체",
        background="테스트용 가상 프로필",
        behavior_tendency=BehaviorTendency(
            post_rate=post_rate, reply_rate=0.3, repost_rate=0.2, controversy_affinity=0.5
        ),
        initial_following=list(following),
    )


def _sem(mid: str, summary: str, *, sim_count: int, last_sim: int) -> SemanticMemory:
    return SemanticMemory(
        id=mid,
        summary=summary,
        topics=["정치"],
        key_relationships=[KeyRelationship(agent_id="agent_002", nature="neutral")],
        simulation_count=sim_count,
        last_relevant_sim=last_sim,
    )


@pytest.fixture
def ontology_a() -> OntologyA:
    return OntologyA(
        seed=42,
        agent_count=3,
        preset=Preset.QUICK,
        source_document="테스트 문서",
        simulation_requirement="3-agent 스모크",
        generated_at=datetime(2026, 5, 24, 12, 0, tzinfo=UTC),
        ontology=Ontology(entity_types=[], edge_types=[]),
        agents={
            "agent_001": _profile(
                "agent_001",
                topics=["정치", "경제"],
                post_rate=0.7,
                following=["agent_002", "agent_001", "agent_999"],  # self + unknown
            ),
            "agent_002": _profile(
                "agent_002",
                topics=["기술"],
                post_rate=0.4,
                following=["agent_003"],
            ),
            "agent_003": _profile(
                "agent_003",
                topics=["문화"],
                post_rate=0.1,
                following=[],
            ),
        },
    )


@pytest.fixture
def ontology_b() -> OntologyB:
    return OntologyB(
        stores={
            "agent_001": MemoryStore(
                agent_id="agent_001",
                semantic=[
                    _sem("m1", "낮은 회상 기억", sim_count=1, last_sim=0),
                    _sem("m2", "중간 회상 최신", sim_count=5, last_sim=10),
                    _sem("m3", "중간 회상 과거", sim_count=5, last_sim=2),
                    _sem("m4", "최다 회상 기억", sim_count=9, last_sim=7),
                ],
            ),
            "agent_002": MemoryStore(
                agent_id="agent_002",
                semantic=[_sem("m1", "유일한 기억", sim_count=2, last_sim=1)],
            ),
            "agent_003": MemoryStore(agent_id="agent_003", semantic=[]),
        }
    )


# ── tests ────────────────────────────────────────────────────────────


def test_build_agents_maps_post_rate_to_activation_rate(
    ontology_a: OntologyA, ontology_b: OntologyB
) -> None:
    agents = {
        a.agent_id: a
        for a in OntologyLoader.build_agents(ontology_a=ontology_a, ontology_b=ontology_b)
    }

    assert agents["agent_001"].activation_rate == pytest.approx(0.7)
    assert agents["agent_002"].activation_rate == pytest.approx(0.4)
    assert agents["agent_003"].activation_rate == pytest.approx(0.1)


def test_build_agents_maps_topics_to_interests(
    ontology_a: OntologyA, ontology_b: OntologyB
) -> None:
    agents = {
        a.agent_id: a
        for a in OntologyLoader.build_agents(ontology_a=ontology_a, ontology_b=ontology_b)
    }

    assert agents["agent_001"].interests == ("정치", "경제")
    assert agents["agent_002"].interests == ("기술",)
    assert agents["agent_003"].interests == ("문화",)


def test_persona_traits_preserve_unused_fields(
    ontology_a: OntologyA, ontology_b: OntologyB
) -> None:
    """Section 4.1: model_dump 전체 보존 — 후속 단계가 참조할 미사용 필드 유지."""
    agents = {
        a.agent_id: a
        for a in OntologyLoader.build_agents(ontology_a=ontology_a, ontology_b=ontology_b)
    }
    traits = agents["agent_001"].persona_traits

    assert traits["behavior_tendency"]["reply_rate"] == pytest.approx(0.3)
    assert traits["entity_type"] == "Journalist"


def test_memory_summary_orders_by_sim_count_then_recency(
    ontology_a: OntologyA, ontology_b: OntologyB
) -> None:
    agents = {
        a.agent_id: a
        for a in OntologyLoader.build_agents(ontology_a=ontology_a, ontology_b=ontology_b)
    }

    # contract Section 4.2: top-3 by (simulation_count desc, last_relevant_sim desc)
    # m4(9), m2(5,10), m3(5,2) — m1(1) dropped
    assert agents["agent_001"].memory_summary == "최다 회상 기억; 중간 회상 최신; 중간 회상 과거"


def test_memory_summary_is_none_for_empty_semantic(
    ontology_a: OntologyA, ontology_b: OntologyB
) -> None:
    agents = {
        a.agent_id: a
        for a in OntologyLoader.build_agents(ontology_a=ontology_a, ontology_b=ontology_b)
    }
    assert agents["agent_003"].memory_summary is None


def test_memory_summary_handles_under_n_entries(
    ontology_a: OntologyA, ontology_b: OntologyB
) -> None:
    agents = {
        a.agent_id: a
        for a in OntologyLoader.build_agents(ontology_a=ontology_a, ontology_b=ontology_b)
    }
    assert agents["agent_002"].memory_summary == "유일한 기억"


def test_memory_summary_breaks_full_ties_by_id() -> None:
    """두 정렬 키가 모두 동률이면 id 사전순으로 결정 (재현성, Section 6.4).

    `Loader.build_agents` 경로를 통해 Section 4.2 정렬 결정성을 lock-in.
    """
    profile = _profile("agent_a", topics=["정치"], post_rate=0.5)
    a = OntologyA(
        seed=1,
        agent_count=1,
        preset=Preset.QUICK,
        source_document="d",
        simulation_requirement="r",
        generated_at=datetime(2026, 5, 25, tzinfo=UTC),
        ontology=Ontology(entity_types=[], edge_types=[]),
        agents={"agent_a": profile},
    )
    b = OntologyB(
        stores={
            "agent_a": MemoryStore(
                agent_id="agent_a",
                semantic=[
                    _sem("m_b", "b", sim_count=5, last_sim=3),
                    _sem("m_a", "a", sim_count=5, last_sim=3),
                ],
            ),
        }
    )
    agents = OntologyLoader.build_agents(ontology_a=a, ontology_b=b)
    assert agents[0].memory_summary == "a; b"


def test_social_graph_drops_self_follow_and_unknown(ontology_a: OntologyA) -> None:
    """unknown agent_id drop 을 실효 검증.

    self-follow 는 `AgentProfile._no_self_follow` (`phase1/models.py`) 가
    모델 생성 시점에 이미 제거하므로 Loader 의 `f != aid` 가드는
    belt-and-suspenders 다. 본 테스트는 unknown follow drop 만 실효 검증한다.
    """
    graph = OntologyLoader.build_social_graph(ontology_a=ontology_a)

    # agent_001 had [agent_002, agent_001, agent_999]:
    #   - agent_001 (self) 은 AgentProfile 단계에서 이미 제거됨 (가드 도달 전)
    #   - agent_999 (unknown) 는 Loader 가 제거 — 본 테스트의 실효 케이스
    assert graph.following("agent_001") == frozenset({"agent_002"})
    assert graph.following("agent_002") == frozenset({"agent_003"})
    assert graph.following("agent_003") == frozenset()


def test_state_store_constructible_from_ontology(
    ontology_a: OntologyA, ontology_b: OntologyB, tmp_path: Path
) -> None:
    agents = OntologyLoader.build_agents(ontology_a=ontology_a, ontology_b=ontology_b)
    graph = OntologyLoader.build_social_graph(ontology_a=ontology_a)

    store = StateStore(
        agents=agents,
        social=graph,
        social_factory=SocialGraph.from_dict,
        checkpoint_dir=tmp_path,
        global_seed=ontology_a.seed,
    )

    assert store.list_agent_ids() == ("agent_001", "agent_002", "agent_003")
    assert store.get_agent("agent_001").activation_rate == pytest.approx(0.7)


def test_scheduler_is_deterministic_across_runs(
    ontology_a: OntologyA, ontology_b: OntologyB
) -> None:
    """동일 입력 + 동일 seed → 동일 활성 에이전트 집합."""

    def _run() -> tuple[str, ...]:
        agents = OntologyLoader.build_agents(ontology_a=ontology_a, ontology_b=ontology_b)
        scheduler = AgentScheduler(global_seed=ontology_a.seed)
        return scheduler.select_active(agents, round_num=0)

    assert _run() == _run()


def test_build_agents_order_is_stable(ontology_a: OntologyA, ontology_b: OntologyB) -> None:
    """contract Section 5: agent_id 사전순 보장."""
    ids = tuple(
        a.agent_id
        for a in OntologyLoader.build_agents(ontology_a=ontology_a, ontology_b=ontology_b)
    )
    assert ids == ("agent_001", "agent_002", "agent_003")
