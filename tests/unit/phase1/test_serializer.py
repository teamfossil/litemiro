"""OntologySerializer unit tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

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
from litemiro.phase1.serializer import OntologySerializer


def _make_ontology_a() -> OntologyA:
    return OntologyA(
        seed=42,
        agent_count=1,
        preset=Preset.QUICK,
        source_document="test.pdf",
        simulation_requirement="test sim",
        generated_at=datetime(2026, 4, 1, 10, 0, tzinfo=UTC),
        ontology=Ontology(entity_types=[], edge_types=[]),
        agents={
            "agent_0001": AgentProfile(
                agent_id="agent_0001",
                name="테스트",
                entity_type="Person",
                origin=AgentOrigin.EXTRACTED,
                skeleton={"layer": "test"},
                ideology=0.5,
                topics=["AI"],
                behavior_tendency=BehaviorTendency(),
            ),
        },
    )


def _make_ontology_b() -> OntologyB:
    return OntologyB(
        stores={
            "agent_0001": MemoryStore(
                agent_id="agent_0001",
                semantic=[SemanticMemory(id="seed_0001_1", summary="test memory")],
            ),
        },
    )


class TestOntologySerializer:
    def test_serialize_a_valid_json(self) -> None:
        s = OntologySerializer()
        result = s.serialize_a(_make_ontology_a())
        parsed = json.loads(result)
        assert parsed["version"] == 1
        assert "agent_0001" in parsed["agents"]

    def test_serialize_b_valid_json(self) -> None:
        s = OntologySerializer()
        result = s.serialize_b(_make_ontology_b())
        parsed = json.loads(result)
        assert "agent_0001" in parsed["stores"]

    def test_write_creates_files(self, tmp_path: Path) -> None:
        s = OntologySerializer()
        a_path, b_path = s.write(_make_ontology_a(), _make_ontology_b(), tmp_path)
        assert a_path.exists()
        assert b_path.exists()
        assert a_path.name == "ontology_a_persona.json"
        assert b_path.name == "ontology_b_memory.json"

    def test_schema_validation_passes(self) -> None:
        s = OntologySerializer()
        a = _make_ontology_a()
        data = json.loads(s.serialize_a(a))
        errors = s.validate_against_schema(data, "ontology_a")
        assert errors == []

    def test_schema_validation_b_passes(self) -> None:
        s = OntologySerializer()
        b = _make_ontology_b()
        data = json.loads(s.serialize_b(b))
        errors = s.validate_against_schema(data, "ontology_b")
        assert errors == []
