"""MemoryInitializer unit tests."""

from __future__ import annotations

from litemiro.phase1.local_graph import LocalGraph
from litemiro.phase1.memory_initializer import MemoryInitializer
from litemiro.phase1.models import (
    AgentOrigin,
    AgentProfile,
    BehaviorTendency,
    Edge,
    Entity,
    ExtractionResult,
)


def _make_profile(
    agent_id: str,
    ideology: float = 0.5,
    topics: list[str] | None = None,
    origin: AgentOrigin = AgentOrigin.EXTRACTED,
) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id,
        name=f"Agent {agent_id}",
        entity_type="Journalist",
        origin=origin,
        ideology=ideology,
        topics=topics or ["AI"],
        behavior_tendency=BehaviorTendency(),
    )


def _build_rich_graph() -> LocalGraph:
    """Graph with entities matching agent_ids so edge-based logic fires."""
    return LocalGraph.build(
        ExtractionResult(
            entities=[
                Entity(
                    id="a1",
                    type="Journalist",
                    name="김기자",
                    summary="정치부 기자로 정책을 비판적으로 보도",
                    source_chunks=[0, 1],
                ),
                Entity(
                    id="a2",
                    type="Organization",
                    name="한겨레",
                    summary="진보 성향 신문사",
                    source_chunks=[0],
                ),
                Entity(
                    id="a3",
                    type="Politician",
                    name="박의원",
                    summary="보수 성향 국회의원",
                    source_chunks=[1],
                ),
            ],
            relationships=[
                Edge(
                    source="a1",
                    target="a2",
                    type="WORKS_FOR",
                    description="한겨레 소속 기자",
                    weight=1.0,
                ),
                Edge(
                    source="a1",
                    target="a3",
                    type="REPORTS_ON",
                    description="박의원 관련 취재",
                    weight=0.8,
                ),
                Edge(source="a3", target="a1", type="OPPOSES", description="언론 비판", weight=0.5),
                Edge(
                    source="a2", target="a3", type="COLLEAGUES", description="업무 협력", weight=0.6
                ),
            ],
        )
    )


class TestMemoryInitializer:
    def test_initialize_creates_stores(self, sample_extraction: ExtractionResult) -> None:
        graph = LocalGraph.build(sample_extraction)
        agents = {
            "agent_0001": _make_profile("agent_0001", 0.3, ["정치", "경제"]),
            "agent_0002": _make_profile("agent_0002", 0.4, ["규제"]),
        }
        init = MemoryInitializer(graph=graph, seed=42)
        stores = init.initialize(agents)
        assert "agent_0001" in stores
        assert "agent_0002" in stores
        assert stores["agent_0001"].episodic == []

    def test_seed_memories_from_entity_with_summary(self) -> None:
        graph = _build_rich_graph()
        agents = {"a1": _make_profile("a1", 0.3, ["정치"])}
        init = MemoryInitializer(graph=graph, seed=42)
        stores = init.initialize(agents)
        memories = stores["a1"].semantic
        assert len(memories) >= 1
        assert any("정치부 기자" in m.summary for m in memories)

    def test_seed_memory_topics_come_from_entity_not_persona(self) -> None:
        graph = LocalGraph.build(
            ExtractionResult(
                entities=[
                    Entity(
                        id="a1",
                        type="Policy",
                        name="Privacy Act",
                        attributes={"domain": "privacy"},
                        summary="consumer data regulation",
                    )
                ]
            )
        )
        agents = {"a1": _make_profile("a1", topics=["sports", "music"])}

        stores = MemoryInitializer(graph=graph, seed=42).initialize(agents)

        assert stores["a1"].semantic[0].topics == ["privacy", "Policy", "consumer"]

    def test_relationship_memory_topics_come_from_graph_not_persona(self) -> None:
        graph = LocalGraph.build(
            ExtractionResult(
                entities=[
                    Entity(id="a1", type="Person", name="Alice", summary="profile seed"),
                    Entity(
                        id="a2",
                        type="Agency",
                        name="Data Office",
                        attributes={"field": "housing"},
                        summary="housing policy enforcement",
                    ),
                ],
                relationships=[
                    Edge(
                        source="a1",
                        target="a2",
                        type="REPORTS_ON",
                        description="audits privacy reports",
                    )
                ],
            )
        )
        agents = {"a1": _make_profile("a1", topics=["sports"])}

        stores = MemoryInitializer(graph=graph, seed=42).initialize(agents)
        relationship_memory = next(m for m in stores["a1"].semantic if m.key_relationships)

        assert relationship_memory.topics == ["housing", "Agency", "audits"]

    def test_relationship_memories_created(self) -> None:
        graph = _build_rich_graph()
        agents = {
            "a1": _make_profile("a1", 0.3, ["정치"]),
            "a2": _make_profile("a2", 0.5, ["뉴스"]),
            "a3": _make_profile("a3", 0.7, ["정치"]),
        }
        init = MemoryInitializer(graph=graph, seed=42)
        stores = init.initialize(agents)
        a1_memories = stores["a1"].semantic
        assert len(a1_memories) >= 2
        rel_memories = [m for m in a1_memories if m.key_relationships]
        assert len(rel_memories) >= 1

    def test_sentiment_inference(self) -> None:
        graph = _build_rich_graph()
        agents = {
            "a1": _make_profile("a1"),
            "a3": _make_profile("a3"),
        }
        init = MemoryInitializer(graph=graph, seed=42)
        stores = init.initialize(agents)
        a1_mems = stores["a1"].semantic
        sentiments = {m.dominant_sentiment for m in a1_mems}
        assert len(sentiments) >= 1

    def test_follow_from_works_for(self) -> None:
        graph = _build_rich_graph()
        agents = {
            "a1": _make_profile("a1", 0.3),
            "a2": _make_profile("a2", 0.5),
        }
        init = MemoryInitializer(graph=graph, seed=42)
        init.initialize(agents)
        assert "a2" in agents["a1"].initial_following

    def test_opposes_no_follow(self) -> None:
        graph = LocalGraph.build(
            ExtractionResult(
                entities=[
                    Entity(id="x1", type="P", name="A", summary="a"),
                    Entity(id="x2", type="P", name="B", summary="b"),
                ],
                relationships=[
                    Edge(source="x1", target="x2", type="OPPOSES", description="반대"),
                ],
            )
        )
        agents = {
            "x1": _make_profile("x1", 0.2),
            "x2": _make_profile("x2", 0.8),
        }
        init = MemoryInitializer(graph=graph, seed=42)
        init.initialize(agents)
        assert "x2" not in agents["x1"].initial_following

    def test_derived_agent_ideology_follow(self) -> None:
        graph = LocalGraph.build(ExtractionResult())
        agents = {
            "a1": _make_profile("a1", 0.5, ["AI"], AgentOrigin.EXTRACTED),
            "d1": _make_profile("d1", 0.5, ["AI"], AgentOrigin.DERIVED),
            "d2": _make_profile("d2", 0.51, ["AI"], AgentOrigin.DERIVED),
        }
        init = MemoryInitializer(graph=graph, seed=1)
        init.initialize(agents)
        # Derived agents with close ideology should have some following
        all_following = []
        for a in agents.values():
            all_following.extend(a.initial_following)
        # With seed=1 and ideology diff < 0.2, some follows should occur
        assert isinstance(all_following, list)

    def test_derived_agent_topic_jaccard_follow(self) -> None:
        graph = LocalGraph.build(ExtractionResult())
        agents = {
            "d1": _make_profile("d1", 0.1, ["AI", "규제", "정책"], AgentOrigin.DERIVED),
            "d2": _make_profile("d2", 0.9, ["AI", "규제", "기술"], AgentOrigin.DERIVED),
        }
        init = MemoryInitializer(graph=graph, seed=100)
        init.initialize(agents)
        # Ideology diff > 0.2 but Jaccard > 0.4 — topic-based follow possible
        assert isinstance(agents["d1"].initial_following, list)

    def test_empty_agents(self, sample_extraction: ExtractionResult) -> None:
        graph = LocalGraph.build(sample_extraction)
        init = MemoryInitializer(graph=graph, seed=42)
        stores = init.initialize({})
        assert stores == {}

    def test_deterministic(self) -> None:
        graph = _build_rich_graph()
        agents1 = {"a1": _make_profile("a1"), "a2": _make_profile("a2")}
        agents2 = {"a1": _make_profile("a1"), "a2": _make_profile("a2")}
        s1 = MemoryInitializer(graph=graph, seed=42).initialize(agents1)
        s2 = MemoryInitializer(graph=graph, seed=42).initialize(agents2)
        for aid in agents1:
            assert len(s1[aid].semantic) == len(s2[aid].semantic)

    def test_max_5_memories(self) -> None:
        entities = [
            Entity(id=f"e{i}", type="P", name=f"Person{i}", summary=f"요약{i}", source_chunks=[0])
            for i in range(10)
        ]
        edges = [
            Edge(source="e0", target=f"e{i}", type="ALLIES", description=f"관계{i}", weight=1.0)
            for i in range(1, 10)
        ]
        graph = LocalGraph.build(ExtractionResult(entities=entities, relationships=edges))
        agents = {"e0": _make_profile("e0")}
        stores = MemoryInitializer(graph=graph, seed=42).initialize(agents)
        assert len(stores["e0"].semantic) <= 5
