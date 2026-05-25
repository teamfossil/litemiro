"""Validate committed fixture files against Pydantic models and JSON Schema.

These tests load ``tests/data/sample_quick_preset_ontology_{a,b}.json`` —
the reference output of a quick-preset pipeline run — and verify every
contract that the Phase 2 OntologyLoader depends on.
"""

from __future__ import annotations

import json
from pathlib import Path

from litemiro.phase1.models import OntologyA, OntologyB
from litemiro.phase1.serializer import OntologySerializer
from litemiro.phase1.validator import OntologyValidator

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
FIXTURE_A = DATA_DIR / "sample_quick_preset_ontology_a.json"
FIXTURE_B = DATA_DIR / "sample_quick_preset_ontology_b.json"


class TestFixturePydanticValidation:
    def test_ontology_a_loads(self) -> None:
        a = OntologyA.model_validate_json(FIXTURE_A.read_text(encoding="utf-8"))
        assert a.version == 1
        assert a.agent_count >= 1
        assert a.agent_count == len(a.agents)

    def test_ontology_b_loads(self) -> None:
        b = OntologyB.model_validate_json(FIXTURE_B.read_text(encoding="utf-8"))
        assert b.version == 1
        assert len(b.stores) >= 1

    def test_agent_count_matches_stores(self) -> None:
        a = OntologyA.model_validate_json(FIXTURE_A.read_text(encoding="utf-8"))
        b = OntologyB.model_validate_json(FIXTURE_B.read_text(encoding="utf-8"))
        assert set(a.agents) == set(b.stores)

    def test_required_agent_fields(self) -> None:
        a = OntologyA.model_validate_json(FIXTURE_A.read_text(encoding="utf-8"))
        for agent_id, profile in a.agents.items():
            assert profile.skeleton, f"{agent_id}: empty skeleton"
            assert 0.0 <= profile.ideology <= 1.0, f"{agent_id}: ideology"
            assert profile.topics, f"{agent_id}: empty topics"
            assert profile.behavior_tendency is not None


class TestFixtureSchemaValidation:
    def test_ontology_a_schema(self) -> None:
        data = json.loads(FIXTURE_A.read_text(encoding="utf-8"))
        errors = OntologySerializer().validate_against_schema(data, "ontology_a")
        assert errors == [], errors

    def test_ontology_b_schema(self) -> None:
        data = json.loads(FIXTURE_B.read_text(encoding="utf-8"))
        errors = OntologySerializer().validate_against_schema(data, "ontology_b")
        assert errors == [], errors


class TestFixtureCrossConsistency:
    def test_validator_passes(self) -> None:
        a = OntologyA.model_validate_json(FIXTURE_A.read_text(encoding="utf-8"))
        b = OntologyB.model_validate_json(FIXTURE_B.read_text(encoding="utf-8"))
        result = OntologyValidator().validate(a, b)
        assert result.valid, f"errors: {result.errors}"

    def test_no_self_follow(self) -> None:
        a = OntologyA.model_validate_json(FIXTURE_A.read_text(encoding="utf-8"))
        for agent_id, profile in a.agents.items():
            assert agent_id not in profile.initial_following, f"{agent_id} self-follows"

    def test_initial_following_references_valid(self) -> None:
        a = OntologyA.model_validate_json(FIXTURE_A.read_text(encoding="utf-8"))
        valid_ids = set(a.agents)
        for agent_id, profile in a.agents.items():
            invalid = [fid for fid in profile.initial_following if fid not in valid_ids]
            assert invalid == [], f"{agent_id} follows unknown: {invalid}"
