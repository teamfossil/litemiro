"""Unit tests for `litemiro.integration.OntologyLoader`.

Coverage targets: contract Section 4 매핑, Section 6 검증 (실패 경로 포함),
재현성, 빈 semantic → ``None``. E2E 스모크 (`tests/e2e/test_phase1_to_phase2_*`)
는 별도로 owner=A 가 helper → Loader 로 치환하는 별도 PR (contract Section 8.1)
에서 다룬다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from litemiro.integration import OntologyLoader
from litemiro.phase1.models import (
    AgentOrigin,
    AgentProfile,
    BehaviorTendency,
    MemoryStore,
    Ontology,
    OntologyA,
    OntologyB,
    Preset,
    SemanticMemory,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SAMPLE_A = _REPO_ROOT / "tests" / "data" / "sample_ontology_a.json"
_SAMPLE_B = _REPO_ROOT / "tests" / "data" / "sample_ontology_b.json"


# ── fixtures ─────────────────────────────────────────────────────────


def _profile(
    aid: str,
    *,
    topics: list[str],
    post_rate: float,
    following: Iterable[str] = (),
    origin: AgentOrigin = AgentOrigin.EXTRACTED,
) -> AgentProfile:
    return AgentProfile(
        agent_id=aid,
        name=f"agent-{aid}",
        entity_type="Journalist",
        origin=origin,
        topics=topics,
        personality="중립적이고 분석적",
        speech_style="~다 체",
        background="단위 테스트용 픽스처",
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
        simulation_count=sim_count,
        last_relevant_sim=last_sim,
    )


@pytest.fixture
def ontology_a() -> OntologyA:
    return OntologyA(
        seed=42,
        agent_count=3,
        preset=Preset.QUICK,
        source_document="unit-test",
        simulation_requirement="OntologyLoader 단위 테스트",
        generated_at=datetime(2026, 5, 25, 12, 0, tzinfo=UTC),
        ontology=Ontology(entity_types=[], edge_types=[]),
        agents={
            "agent_001": _profile(
                "agent_001",
                topics=["정치", "경제"],
                post_rate=0.7,
                following=["agent_002", "agent_001", "agent_999"],
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


# ── build_agents — Section 4.1 매핑 ──────────────────────────────────


def test_build_agents_orders_by_agent_id(ontology_a: OntologyA, ontology_b: OntologyB) -> None:
    """Section 5: 결정적 순서 (agent_id 사전순)."""
    ids = tuple(
        a.agent_id
        for a in OntologyLoader.build_agents(ontology_a=ontology_a, ontology_b=ontology_b)
    )
    assert ids == ("agent_001", "agent_002", "agent_003")


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


def test_build_agents_preserves_unused_persona_fields(
    ontology_a: OntologyA, ontology_b: OntologyB
) -> None:
    """Section 4.1: AgentProfile.model_dump 전체 보존."""
    agents = {
        a.agent_id: a
        for a in OntologyLoader.build_agents(ontology_a=ontology_a, ontology_b=ontology_b)
    }
    traits = agents["agent_001"].persona_traits
    assert traits["behavior_tendency"]["reply_rate"] == pytest.approx(0.3)
    assert traits["entity_type"] == "Journalist"
    assert traits["origin"] == "extracted"


# ── memory_summary — Section 4.2 ─────────────────────────────────────


def test_memory_summary_orders_by_sim_count_then_recency(
    ontology_a: OntologyA, ontology_b: OntologyB
) -> None:
    """top-3 by (simulation_count desc, last_relevant_sim desc)."""
    agents = {
        a.agent_id: a
        for a in OntologyLoader.build_agents(ontology_a=ontology_a, ontology_b=ontology_b)
    }
    # m4(9) → m2(5,10) → m3(5,2); m1(1) cutoff
    assert agents["agent_001"].memory_summary == "최다 회상 기억; 중간 회상 최신; 중간 회상 과거"


def test_memory_summary_is_none_for_empty_semantic(
    ontology_a: OntologyA, ontology_b: OntologyB
) -> None:
    agents = {
        a.agent_id: a
        for a in OntologyLoader.build_agents(ontology_a=ontology_a, ontology_b=ontology_b)
    }
    assert agents["agent_003"].memory_summary is None


def test_memory_summary_handles_fewer_than_n_entries(
    ontology_a: OntologyA, ontology_b: OntologyB
) -> None:
    agents = {
        a.agent_id: a
        for a in OntologyLoader.build_agents(ontology_a=ontology_a, ontology_b=ontology_b)
    }
    assert agents["agent_002"].memory_summary == "유일한 기억"


def test_memory_summary_breaks_full_tie_by_id() -> None:
    """두 정렬 키가 동률이면 id 사전순 — 재현성 (Section 6.4) 보장."""
    a = OntologyA(
        seed=1,
        agent_count=1,
        preset=Preset.QUICK,
        source_document="x",
        simulation_requirement="x",
        generated_at=datetime(2026, 5, 25, tzinfo=UTC),
        ontology=Ontology(entity_types=[], edge_types=[]),
        agents={"a1": _profile("a1", topics=[], post_rate=0.5)},
    )
    b = OntologyB(
        stores={
            "a1": MemoryStore(
                agent_id="a1",
                semantic=[
                    _sem("m_b", "b", sim_count=5, last_sim=3),
                    _sem("m_a", "a", sim_count=5, last_sim=3),
                ],
            )
        }
    )
    agents = OntologyLoader.build_agents(ontology_a=a, ontology_b=b)
    assert agents[0].memory_summary == "a; b"


def test_memory_summary_caps_at_top_three() -> None:
    """N=3 cutoff — 네 번째 이상은 버린다 (Section 4.2)."""
    a = OntologyA(
        seed=1,
        agent_count=1,
        preset=Preset.QUICK,
        source_document="x",
        simulation_requirement="x",
        generated_at=datetime(2026, 5, 25, tzinfo=UTC),
        ontology=Ontology(entity_types=[], edge_types=[]),
        agents={"a1": _profile("a1", topics=[], post_rate=0.5)},
    )
    b = OntologyB(
        stores={
            "a1": MemoryStore(
                agent_id="a1",
                semantic=[
                    _sem("m1", "first", sim_count=10, last_sim=10),
                    _sem("m2", "second", sim_count=9, last_sim=9),
                    _sem("m3", "third", sim_count=8, last_sim=8),
                    _sem("m4", "fourth", sim_count=7, last_sim=7),
                ],
            )
        }
    )
    summary = OntologyLoader.build_agents(ontology_a=a, ontology_b=b)[0].memory_summary
    assert summary == "first; second; third"


# ── build_social_graph — Section 4.3 ─────────────────────────────────


def test_social_graph_drops_self_and_unknown(ontology_a: OntologyA) -> None:
    graph = OntologyLoader.build_social_graph(ontology_a=ontology_a)
    # agent_001 had [agent_002, agent_001, agent_999]
    #   agent_001 self → AgentProfile._no_self_follow 가 모델 생성 시 제거
    #   agent_999 unknown → Loader 가 drop
    assert graph.following("agent_001") == frozenset({"agent_002"})
    assert graph.following("agent_002") == frozenset({"agent_003"})
    assert graph.following("agent_003") == frozenset()


def test_social_graph_omits_followers_with_no_edges() -> None:
    """follow 가 모두 필터되어 비면 to_dict 결과에서도 제외."""
    a = OntologyA(
        seed=1,
        agent_count=2,
        preset=Preset.QUICK,
        source_document="x",
        simulation_requirement="x",
        generated_at=datetime(2026, 5, 25, tzinfo=UTC),
        ontology=Ontology(entity_types=[], edge_types=[]),
        agents={
            "a1": _profile("a1", topics=[], post_rate=0.5, following=["a_unknown"]),
            "a2": _profile("a2", topics=[], post_rate=0.5, following=["a1"]),
        },
    )
    graph = OntologyLoader.build_social_graph(ontology_a=a)
    assert graph.to_dict() == {"a2": ["a1"]}


# ── 재현성 (Section 6.4) ─────────────────────────────────────────────


def test_build_agents_is_deterministic(ontology_a: OntologyA, ontology_b: OntologyB) -> None:
    first = OntologyLoader.build_agents(ontology_a=ontology_a, ontology_b=ontology_b)
    second = OntologyLoader.build_agents(ontology_a=ontology_a, ontology_b=ontology_b)
    assert first == second


def test_build_social_graph_is_deterministic(ontology_a: OntologyA) -> None:
    first = OntologyLoader.build_social_graph(ontology_a=ontology_a).to_dict()
    second = OntologyLoader.build_social_graph(ontology_a=ontology_a).to_dict()
    assert first == second


# ── load — sample fixture round-trip ─────────────────────────────────


def test_load_sample_fixture_round_trip() -> None:
    a, b = OntologyLoader.load(ontology_a_path=_SAMPLE_A, ontology_b_path=_SAMPLE_B)
    assert set(a.agents) == set(b.stores)
    # 매핑까지 통과해야 진짜 round-trip
    agents = OntologyLoader.build_agents(ontology_a=a, ontology_b=b)
    assert tuple(ag.agent_id for ag in agents) == ("agent_001", "agent_002", "agent_003")
    graph = OntologyLoader.build_social_graph(ontology_a=a)
    assert graph.following("agent_002") == frozenset({"agent_001"})


# ── load — 검증 실패 경로 (Section 6) ────────────────────────────────


def _write_pair(
    tmp_path: Path, a_payload: dict[str, object], b_payload: dict[str, object]
) -> tuple[Path, Path]:
    path_a = tmp_path / "ontology_a.json"
    path_b = tmp_path / "ontology_b.json"
    path_a.write_text(json.dumps(a_payload, ensure_ascii=False), encoding="utf-8")
    path_b.write_text(json.dumps(b_payload, ensure_ascii=False), encoding="utf-8")
    return path_a, path_b


def _sample_payloads() -> tuple[dict[str, object], dict[str, object]]:
    return (
        json.loads(_SAMPLE_A.read_text(encoding="utf-8")),
        json.loads(_SAMPLE_B.read_text(encoding="utf-8")),
    )


def test_load_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="읽기 실패"):
        OntologyLoader.load(
            ontology_a_path=tmp_path / "missing_a.json",
            ontology_b_path=tmp_path / "missing_b.json",
        )


def test_load_rejects_invalid_json(tmp_path: Path) -> None:
    path_a = tmp_path / "ontology_a.json"
    path_b = tmp_path / "ontology_b.json"
    path_a.write_text("not-json{", encoding="utf-8")
    path_b.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON 파싱 실패"):
        OntologyLoader.load(ontology_a_path=path_a, ontology_b_path=path_b)


def test_load_rejects_non_object_root(tmp_path: Path) -> None:
    path_a = tmp_path / "ontology_a.json"
    path_b = tmp_path / "ontology_b.json"
    path_a.write_text("[]", encoding="utf-8")
    path_b.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="object"):
        OntologyLoader.load(ontology_a_path=path_a, ontology_b_path=path_b)


def test_load_rejects_schema_violation(tmp_path: Path) -> None:
    a, b = _sample_payloads()
    a.pop("seed")  # required by ontology_a.schema.json
    path_a, path_b = _write_pair(tmp_path, a, b)
    with pytest.raises(ValueError, match="jsonschema 검증 실패"):
        OntologyLoader.load(ontology_a_path=path_a, ontology_b_path=path_b)


def test_load_rejects_agent_count_mismatch(tmp_path: Path) -> None:
    a, b = _sample_payloads()
    a["agent_count"] = 99  # len(agents) == 3
    path_a, path_b = _write_pair(tmp_path, a, b)
    with pytest.raises(ValueError, match="agent_count 불일치"):
        OntologyLoader.load(ontology_a_path=path_a, ontology_b_path=path_b)


def test_load_rejects_reference_mismatch(tmp_path: Path) -> None:
    a, b = _sample_payloads()
    # B 에서 한 agent 의 store 만 잘라낸다 → set(A) - set(B) = {missing}
    stores = b["stores"]
    assert isinstance(stores, dict)
    stores.pop("agent_003")
    path_a, path_b = _write_pair(tmp_path, a, b)
    with pytest.raises(ValueError, match="agent_id 참조 불일치"):
        OntologyLoader.load(ontology_a_path=path_a, ontology_b_path=path_b)


# ── validate_consistency — Section 6.5 ───────────────────────────────


def test_validate_consistency_flags_disjoint_topics(
    ontology_a: OntologyA, ontology_b: OntologyB
) -> None:
    """페르소나 ↔ 기억 토픽 합집합 교집합이 ∅ 이면 warning."""
    warnings = OntologyLoader.validate_consistency(ontology_a=ontology_a, ontology_b=ontology_b)
    # agent_001 (정치, 경제) ∩ {정치} = {정치} → pass
    # agent_002 (기술) ∩ {정치} = ∅ → warning
    # agent_003 → empty semantic → exempt
    assert len(warnings) == 1
    only = warnings[0]
    assert only.agent_id == "agent_002"
    assert only.origin == AgentOrigin.EXTRACTED
    assert only.persona_topics == ("기술",)
    assert only.memory_topics == ("정치",)


def test_validate_consistency_passes_when_topics_overlap() -> None:
    """페르소나-기억 토픽 교집합이 비지 않으면 warning 없음."""
    a = OntologyA(
        seed=1,
        agent_count=1,
        preset=Preset.QUICK,
        source_document="x",
        simulation_requirement="x",
        generated_at=datetime(2026, 5, 25, tzinfo=UTC),
        ontology=Ontology(entity_types=[], edge_types=[]),
        agents={"a1": _profile("a1", topics=["정치", "기술"], post_rate=0.5)},
    )
    b = OntologyB(
        stores={
            "a1": MemoryStore(
                agent_id="a1",
                semantic=[_sem("m1", "x", sim_count=1, last_sim=1)],  # topics=["정치"]
            )
        }
    )
    assert OntologyLoader.validate_consistency(ontology_a=a, ontology_b=b) == ()


def test_validate_consistency_exempts_cold_start() -> None:
    """semantic 리스트가 비면 cold start — 토픽 불일치여도 warning 안 함."""
    a = OntologyA(
        seed=1,
        agent_count=1,
        preset=Preset.QUICK,
        source_document="x",
        simulation_requirement="x",
        generated_at=datetime(2026, 5, 25, tzinfo=UTC),
        ontology=Ontology(entity_types=[], edge_types=[]),
        agents={"a1": _profile("a1", topics=["전혀_다른_주제"], post_rate=0.5)},
    )
    b = OntologyB(stores={"a1": MemoryStore(agent_id="a1", semantic=[])})
    assert OntologyLoader.validate_consistency(ontology_a=a, ontology_b=b) == ()


def test_validate_consistency_orders_warnings_by_agent_id() -> None:
    """결정성 — warning 순서는 agent_id 사전순."""
    a = OntologyA(
        seed=1,
        agent_count=3,
        preset=Preset.QUICK,
        source_document="x",
        simulation_requirement="x",
        generated_at=datetime(2026, 5, 25, tzinfo=UTC),
        ontology=Ontology(entity_types=[], edge_types=[]),
        agents={
            "z_last": _profile("z_last", topics=["X"], post_rate=0.5),
            "a_first": _profile("a_first", topics=["Y"], post_rate=0.5),
            "m_mid": _profile("m_mid", topics=["Z"], post_rate=0.5),
        },
    )
    b = OntologyB(
        stores={
            aid: MemoryStore(
                agent_id=aid,
                semantic=[_sem("m1", "x", sim_count=1, last_sim=1)],  # 모두 ["정치"]
            )
            for aid in ("z_last", "a_first", "m_mid")
        }
    )
    warnings = OntologyLoader.validate_consistency(ontology_a=a, ontology_b=b)
    assert tuple(w.agent_id for w in warnings) == ("a_first", "m_mid", "z_last")


def test_validate_consistency_preserves_derived_origin() -> None:
    """이슈 #21 task 2 가 origin 별 비율을 집계할 수 있게 보존."""
    a = OntologyA(
        seed=1,
        agent_count=1,
        preset=Preset.QUICK,
        source_document="x",
        simulation_requirement="x",
        generated_at=datetime(2026, 5, 25, tzinfo=UTC),
        ontology=Ontology(entity_types=[], edge_types=[]),
        agents={
            "d1": _profile("d1", topics=["X"], post_rate=0.5, origin=AgentOrigin.DERIVED),
        },
    )
    b = OntologyB(
        stores={
            "d1": MemoryStore(agent_id="d1", semantic=[_sem("m1", "x", sim_count=1, last_sim=1)])
        }
    )
    warnings = OntologyLoader.validate_consistency(ontology_a=a, ontology_b=b)
    assert len(warnings) == 1
    assert warnings[0].origin == AgentOrigin.DERIVED
