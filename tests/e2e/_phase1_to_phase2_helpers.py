"""임시 helper: Phase 1 산출 → Phase 2 입력 변환 (Loader 미구현 동안).

`docs/integration/phase1-2-contract.md` §4 매핑 규칙 그대로. Issue #13
(`OntologyLoader` 구현) 머지 시 본 모듈은 삭제되고 호출부는 Loader 메서드로 교체된다.
"""

from __future__ import annotations

from litemiro.models import Agent
from litemiro.phase1.models import (
    AgentProfile,
    MemoryStore,
    OntologyA,
    OntologyB,
    SemanticMemory,
)
from litemiro.social.graph import SocialGraph


def memory_summary_top_n(semantic: list[SemanticMemory], *, n: int = 3) -> str | None:
    """Contract §4.2: top-N concat by (simulation_count desc, last_relevant_sim desc, id asc)."""
    if not semantic:
        return None
    ordered = sorted(semantic, key=lambda m: (-m.simulation_count, -m.last_relevant_sim, m.id))
    return "; ".join(m.summary for m in ordered[:n])


def build_agent(profile: AgentProfile, store: MemoryStore | None) -> Agent:
    """Contract §4.1."""
    return Agent(
        agent_id=profile.agent_id,
        interests=tuple(profile.topics),
        persona_traits=profile.model_dump(mode="json"),
        memory_summary=memory_summary_top_n(store.semantic if store else []),
        activation_rate=profile.behavior_tendency.post_rate,
    )


def build_agents(ontology_a: OntologyA, ontology_b: OntologyB) -> tuple[Agent, ...]:
    """결정적 순서 (agent_id 사전순)."""
    return tuple(
        build_agent(ontology_a.agents[aid], ontology_b.stores.get(aid))
        for aid in sorted(ontology_a.agents)
    )


def build_social_graph(ontology_a: OntologyA) -> SocialGraph:
    """Contract §4.3: self-follow / 미지 agent 사전 필터링."""
    known = set(ontology_a.agents)
    edges: dict[str, list[str]] = {}
    for aid, profile in ontology_a.agents.items():
        followees = [f for f in profile.initial_following if f != aid and f in known]
        if followees:
            edges[aid] = followees
    return SocialGraph.from_dict(edges)
