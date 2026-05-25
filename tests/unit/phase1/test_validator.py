"""OntologyValidator unit tests."""

from __future__ import annotations

from datetime import UTC, datetime

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
from litemiro.phase1.validator import OntologyValidator


def _make_a(agents: dict[str, AgentProfile]) -> OntologyA:
    return OntologyA(
        seed=42,
        agent_count=len(agents),
        preset=Preset.QUICK,
        source_document="test.pdf",
        simulation_requirement="test",
        generated_at=datetime(2026, 4, 1, 10, 0, tzinfo=UTC),
        ontology=Ontology(entity_types=[], edge_types=[]),
        agents=agents,
    )


def _make_b(agent_ids: list[str]) -> OntologyB:
    return OntologyB(
        stores={
            aid: MemoryStore(
                agent_id=aid,
                semantic=[
                    SemanticMemory(id=f"seed_{aid}_1", summary="test", topics=["AI"]),
                ],
            )
            for aid in agent_ids
        },
    )


def _make_profile(
    agent_id: str, ideology: float = 0.5, following: list[str] | None = None
) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id,
        name=f"Agent {agent_id}",
        entity_type="Person",
        origin=AgentOrigin.EXTRACTED,
        skeleton={"layer": "test"},
        ideology=ideology,
        topics=["AI"],
        behavior_tendency=BehaviorTendency(),
        initial_following=following or [],
    )


class TestOntologyValidator:
    def test_valid_pair(self) -> None:
        agents = {"a1": _make_profile("a1"), "a2": _make_profile("a2", following=["a1"])}
        result = OntologyValidator().validate(_make_a(agents), _make_b(["a1", "a2"]))
        assert result.valid
        assert result.errors == []

    def test_agent_id_mismatch(self) -> None:
        agents = {"a1": _make_profile("a1")}
        result = OntologyValidator().validate(_make_a(agents), _make_b(["a1", "a2"]))
        assert not result.valid
        assert any("agent_id" in e.lower() or "mismatch" in e.lower() for e in result.errors)

    def test_invalid_following_reference(self) -> None:
        agents = {"a1": _make_profile("a1", following=["nonexistent"])}
        result = OntologyValidator().validate(_make_a(agents), _make_b(["a1"]))
        assert any("nonexistent" in e or "following" in e.lower() for e in result.errors)

    def test_invalid_key_relationship_reference(self) -> None:
        agents = {"a1": _make_profile("a1")}
        b = OntologyB(
            stores={
                "a1": MemoryStore(
                    agent_id="a1",
                    semantic=[
                        SemanticMemory(
                            id="seed_a1_1",
                            summary="test",
                            key_relationships=[
                                KeyRelationship(agent_id="nonexistent", nature="neutral")
                            ],
                        )
                    ],
                )
            }
        )
        result = OntologyValidator().validate(_make_a(agents), b)
        assert any("key_relationships" in e and "nonexistent" in e for e in result.errors)

    def test_ideology_distribution_warning(self) -> None:
        agents = {
            "a1": _make_profile("a1", ideology=0.1),
            "a2": _make_profile("a2", ideology=0.15),
            "a3": _make_profile("a3", ideology=0.2),
        }
        result = OntologyValidator().validate(_make_a(agents), _make_b(["a1", "a2", "a3"]))
        assert len(result.warnings) >= 1

    def test_persona_memory_topic_mismatch_warning(self) -> None:
        agents = {"a1": _make_profile("a1")}
        b = OntologyB(
            stores={
                "a1": MemoryStore(
                    agent_id="a1",
                    semantic=[
                        SemanticMemory(
                            id="seed_a1_1",
                            summary="sports update",
                            topics=["sports"],
                        )
                    ],
                )
            }
        )

        result = OntologyValidator().validate(_make_a(agents), b)

        assert result.valid
        assert any("persona topics" in warning for warning in result.warnings)

    def test_empty_semantic_memory_skips_topic_mismatch_warning(self) -> None:
        agents = {"a1": _make_profile("a1")}
        b = OntologyB(stores={"a1": MemoryStore(agent_id="a1", semantic=[])})

        result = OntologyValidator().validate(_make_a(agents), b)

        assert result.valid
        assert not any("persona topics" in warning for warning in result.warnings)
