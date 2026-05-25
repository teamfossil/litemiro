"""End-to-end smoke (JSON 경로): 디스크 JSON → `OntologyLoader` → Phase 2 입력.

`test_phase1_to_phase2_smoke.py` 는 in-memory 픽스처로 매핑 규칙 자체를 lock-in
하고, 본 모듈은 실제 Phase 1 산출물 모양 (`tests/data/sample_ontology_*.json`)
이 JSON → `OntologyLoader.load/build_agents/build_social_graph/validate_consistency`
→ `StateStore`/`AgentScheduler` 까지 결정적으로 통과하는지 contract Section 8.1
시나리오 5종을 모두 검증한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from litemiro.core.agent_scheduler import AgentScheduler
from litemiro.core.state_store import StateStore
from litemiro.integration.ontology_loader import OntologyLoader
from litemiro.phase1.models import OntologyA, OntologyB
from litemiro.social.graph import SocialGraph

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


def test_loader_load_returns_validated_models() -> None:
    """contract Section 8.1 ①: 디스크 sample → 4단 검증 통과 + 모델 튜플 반환.

    `OntologyLoader.load` 가 (1) jsonschema, (2) Pydantic, (3) 참조 일관성,
    (4) agent_count 일관성을 모두 통과시키는지 lock-in.
    """
    ontology_a, ontology_b = OntologyLoader.load(
        ontology_a_path=_SAMPLE_A,
        ontology_b_path=_SAMPLE_B,
    )
    assert isinstance(ontology_a, OntologyA)
    assert isinstance(ontology_b, OntologyB)
    assert ontology_a.agent_count == len(ontology_a.agents)
    assert set(ontology_b.stores) == set(ontology_a.agents)


def test_json_roundtrip_preserves_fields(ontology_a: OntologyA, ontology_b: OntologyB) -> None:
    """JSON → 모델 → JSON 재직렬화 충실도 (round-trip 동일성)."""
    a_round = OntologyA.model_validate_json(ontology_a.model_dump_json())
    b_round = OntologyB.model_validate_json(ontology_b.model_dump_json())
    assert a_round == ontology_a
    assert b_round == ontology_b


def test_json_extra_fields_are_rejected() -> None:
    """contract Section 6.1: 알 수 없는 키는 model_validate 단계에서 거부 (extra=forbid)."""
    payload_a = json.loads(_SAMPLE_A.read_text(encoding="utf-8"))
    payload_a["bogus_top_level"] = "x"
    with pytest.raises(ValidationError):
        OntologyA.model_validate(payload_a)

    payload_b = json.loads(_SAMPLE_B.read_text(encoding="utf-8"))
    payload_b["bogus_top_level"] = "x"
    with pytest.raises(ValidationError):
        OntologyB.model_validate(payload_b)


def test_build_agents_from_json(ontology_a: OntologyA, ontology_b: OntologyB) -> None:
    agents = {
        a.agent_id: a
        for a in OntologyLoader.build_agents(ontology_a=ontology_a, ontology_b=ontology_b)
    }

    assert set(agents) == {"agent_001", "agent_002", "agent_003"}
    assert agents["agent_001"].activation_rate == pytest.approx(0.7)
    assert agents["agent_001"].interests == ("정치", "경제")
    # sample_ontology_b: m4(9) > m2(5,10) > m3(5,2) > m1(1) — top-3
    assert agents["agent_001"].memory_summary == "최다 회상 기억; 중간 회상 최신; 중간 회상 과거"
    assert agents["agent_002"].memory_summary == "유일한 기억"
    assert agents["agent_003"].memory_summary is None  # cold start


def test_social_graph_from_json_filters_self_and_unknown(ontology_a: OntologyA) -> None:
    """unknown agent_id drop 을 실효 검증 (JSON 경로).

    self-follow 는 `AgentProfile._no_self_follow` 가 모델 생성 시점에
    이미 제거하므로 Loader 의 `f != aid` 가드는 belt-and-suspenders 다.
    본 테스트는 unknown follow drop 만 실효 검증한다.
    """
    graph = OntologyLoader.build_social_graph(ontology_a=ontology_a)

    # sample agent_001 → [agent_002, agent_001, agent_999]:
    #   - agent_001 (self) 은 AgentProfile 단계에서 이미 제거됨 (가드 도달 전)
    #   - agent_999 (unknown) 는 Loader 가 제거 — 본 테스트의 실효 케이스
    assert graph.following("agent_001") == frozenset({"agent_002"})
    assert graph.following("agent_002") == frozenset({"agent_001"})
    assert graph.following("agent_003") == frozenset()


def test_state_store_constructs_from_json(
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
    assert store.get_agent("agent_002").interests == ("기술", "경제")


def test_scheduler_deterministic_from_json(ontology_a: OntologyA, ontology_b: OntologyB) -> None:
    """contract Section 8.1 ④: 동일 seed → round 0, 1 양쪽에서 동일 활성 셋.

    AgentScheduler 가 라운드별 RNG 파생을 결정적으로 수행하는지 두 라운드에서
    각각 두 번 실행해 비교한다.
    """

    def _run(round_num: int) -> tuple[str, ...]:
        agents = OntologyLoader.build_agents(ontology_a=ontology_a, ontology_b=ontology_b)
        scheduler = AgentScheduler(global_seed=ontology_a.seed)
        return scheduler.select_active(agents, round_num=round_num)

    assert _run(0) == _run(0)
    assert _run(1) == _run(1)


def test_validate_consistency_zero_warnings_on_sample_fixture(
    ontology_a: OntologyA, ontology_b: OntologyB
) -> None:
    """contract Section 8.1 ⑤: sample fixture 는 페르소나/메모리 토픽 교집합이
    모두 비공집합이거나 cold start (빈 semantic) 라서 warning 이 0 이어야 한다.

    이 가드가 깨지면 sample fixture 자체가 의도와 어긋난 것이거나, Section 6.5
    검증 로직이 회귀한 것이다 — 둘 다 명시적으로 추적되어야 하므로 hard assert.
    """
    warnings = OntologyLoader.validate_consistency(ontology_a=ontology_a, ontology_b=ontology_b)
    assert warnings == ()
