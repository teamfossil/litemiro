"""End-to-end smoke (JSON 경로): 디스크 JSON → Pydantic → Phase 2 매핑.

`test_phase1_to_phase2_smoke.py` 는 in-memory 픽스처로 매핑 규칙 자체를 lock-in
하고, 본 모듈은 실제 Phase 1 산출물 모양 (`tests/data/sample_ontology_*.json`)
이 JSON → `OntologyA/B` → `Agent`/`SocialGraph`/`StateStore` 까지 통과하는지
검증한다. PR #12 contract §6 의 sample 경로 그대로.

Loader (Issue #13) 머지 후에는 helper 호출부를 ``OntologyLoader`` 로 치환한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from litemiro.core.agent_scheduler import AgentScheduler
from litemiro.core.state_store import StateStore
from litemiro.phase1.models import OntologyA, OntologyB
from litemiro.social.graph import SocialGraph
from tests.e2e._phase1_to_phase2_helpers import build_agents, build_social_graph

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_SAMPLE_A = _DATA_DIR / "sample_ontology_a.json"
_SAMPLE_B = _DATA_DIR / "sample_ontology_b.json"


@pytest.fixture(scope="module")
def ontology_a() -> OntologyA:
    return OntologyA.model_validate_json(_SAMPLE_A.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ontology_b() -> OntologyB:
    return OntologyB.model_validate_json(_SAMPLE_B.read_text(encoding="utf-8"))


def test_sample_files_exist() -> None:
    assert _SAMPLE_A.is_file(), f"missing fixture: {_SAMPLE_A}"
    assert _SAMPLE_B.is_file(), f"missing fixture: {_SAMPLE_B}"


def test_json_roundtrip_preserves_fields(ontology_a: OntologyA, ontology_b: OntologyB) -> None:
    """JSON → 모델 → JSON 재직렬화가 동일한 구조를 유지하는지 (extra=forbid 보장)."""
    a_round = OntologyA.model_validate_json(ontology_a.model_dump_json())
    b_round = OntologyB.model_validate_json(ontology_b.model_dump_json())
    assert a_round == ontology_a
    assert b_round == ontology_b


def test_build_agents_from_json(ontology_a: OntologyA, ontology_b: OntologyB) -> None:
    agents = {a.agent_id: a for a in build_agents(ontology_a, ontology_b)}

    assert set(agents) == {"agent_001", "agent_002", "agent_003"}
    assert agents["agent_001"].activation_rate == pytest.approx(0.7)
    assert agents["agent_001"].interests == ("정치", "경제")
    # sample_ontology_b: m4(9) > m2(5,10) > m3(5,2) > m1(1) — top-3
    assert agents["agent_001"].memory_summary == "최다 회상 기억; 중간 회상 최신; 중간 회상 과거"
    assert agents["agent_002"].memory_summary == "유일한 기억"
    assert agents["agent_003"].memory_summary is None  # cold start


def test_social_graph_from_json_filters_self_and_unknown(ontology_a: OntologyA) -> None:
    graph = build_social_graph(ontology_a)

    # sample: agent_001 → [agent_002, agent_001, agent_999] → only agent_002 survives
    assert graph.following("agent_001") == frozenset({"agent_002"})
    assert graph.following("agent_002") == frozenset({"agent_001"})
    assert graph.following("agent_003") == frozenset()


def test_state_store_constructs_from_json(
    ontology_a: OntologyA, ontology_b: OntologyB, tmp_path: Path
) -> None:
    agents = build_agents(ontology_a, ontology_b)
    graph = build_social_graph(ontology_a)

    store = StateStore(
        agents=agents,
        social=graph,
        social_factory=SocialGraph.from_dict,
        checkpoint_dir=tmp_path,
        global_seed=ontology_a.seed,
    )

    assert store.list_agent_ids() == ("agent_001", "agent_002", "agent_003")
    assert store.get_agent("agent_002").interests == ("기술", "경제")


def test_scheduler_deterministic_from_json(ontology_a: OntologyA, ontology_b: OntologyB) -> None:
    """동일 seed → 동일 활성 셋. JSON 경로에서도 재현성 보장."""

    def _run() -> tuple[str, ...]:
        agents = build_agents(ontology_a, ontology_b)
        scheduler = AgentScheduler(global_seed=ontology_a.seed)
        return scheduler.select_active(agents, round_num=0)

    assert _run() == _run()
