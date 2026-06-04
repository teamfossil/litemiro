"""Phase 1 model validation tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from litemiro.phase1.models import (
    ActorKind,
    AgentOrigin,
    AgentProfile,
    BehaviorTendency,
    Entity,
    EntityTypeDef,
    MemoryConfig,
    MemoryStore,
    Ontology,
    OntologyA,
    OntologyB,
    PersonaMode,
    Preset,
    SemanticMemory,
    StanceBucket,
    actor_kind_from_persona_mode,
    stance_bucket,
)


class TestBehaviorTendency:
    def test_defaults(self) -> None:
        bt = BehaviorTendency()
        assert bt.post_rate == 0.5
        assert bt.reply_rate == 0.3
        assert bt.repost_rate == 0.35

    def test_rejects_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            BehaviorTendency(post_rate=1.5)
        with pytest.raises(ValidationError):
            BehaviorTendency(reply_rate=-0.1)

    def test_frozen(self) -> None:
        bt = BehaviorTendency()
        with pytest.raises(ValidationError):
            bt.post_rate = 0.9


class TestAgentProfile:
    def test_valid_profile(self, sample_agent_profile: AgentProfile) -> None:
        assert sample_agent_profile.agent_id == "agent_0001"
        assert sample_agent_profile.ideology == 0.3
        assert sample_agent_profile.stance == 0.3

    def test_ideology_range(self) -> None:
        with pytest.raises(ValidationError):
            AgentProfile(
                agent_id="a",
                name="x",
                entity_type="T",
                origin=AgentOrigin.EXTRACTED,
                ideology=1.5,
            )

    def test_stance_range(self) -> None:
        with pytest.raises(ValidationError):
            AgentProfile(
                agent_id="a",
                name="x",
                entity_type="T",
                origin=AgentOrigin.EXTRACTED,
                stance=-0.1,
            )

    def test_legacy_profile_defaults_stance_from_ideology(self) -> None:
        profile = AgentProfile(
            agent_id="a",
            name="x",
            entity_type="T",
            origin=AgentOrigin.EXTRACTED,
            ideology=0.8,
        )
        assert profile.stance == 0.8

    def test_actor_metadata_defaults_to_direct_person(self) -> None:
        profile = AgentProfile(
            agent_id="a",
            name="x",
            entity_type="Person",
            origin=AgentOrigin.EXTRACTED,
        )
        assert profile.actor_kind is ActorKind.DIRECT_PERSON
        assert profile.represented_entity_id is None

    def test_representative_actor_metadata(self) -> None:
        profile = AgentProfile(
            agent_id="org",
            name="Org spokesperson",
            entity_type="Organization",
            origin=AgentOrigin.EXTRACTED,
            actor_kind=ActorKind.REPRESENTATIVE,
            represented_entity_id="org",
        )
        assert profile.actor_kind is ActorKind.REPRESENTATIVE
        assert profile.represented_entity_id == "org"

    def test_self_follow_removed(self) -> None:
        p = AgentProfile(
            agent_id="agent_0001",
            name="x",
            entity_type="T",
            origin=AgentOrigin.EXTRACTED,
            initial_following=["agent_0001", "agent_0002"],
        )
        assert "agent_0001" not in p.initial_following
        assert "agent_0002" in p.initial_following

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentProfile.model_validate(
                {
                    "agent_id": "a",
                    "name": "x",
                    "entity_type": "T",
                    "origin": "extracted",
                    "extra_field": True,
                }
            )


class TestStanceBucket:
    def test_bucket_boundaries(self) -> None:
        assert stance_bucket(0.39) is StanceBucket.CRITICAL
        assert stance_bucket(0.4) is StanceBucket.NEUTRAL
        assert stance_bucket(0.6) is StanceBucket.NEUTRAL
        assert stance_bucket(0.61) is StanceBucket.SUPPORTIVE


class TestActorKindMapping:
    def test_actor_kind_from_persona_mode(self) -> None:
        assert actor_kind_from_persona_mode(PersonaMode.DIRECT_PERSON) is ActorKind.DIRECT_PERSON
        assert actor_kind_from_persona_mode(PersonaMode.REPRESENTATIVE) is ActorKind.REPRESENTATIVE
        assert actor_kind_from_persona_mode(PersonaMode.CONTEXT_ONLY) is None


class TestEntity:
    def test_basic(self) -> None:
        e = Entity(id="e1", type="Person", name="김철수")
        assert e.summary == ""
        assert e.source_chunks == []

    def test_with_attributes(self) -> None:
        e = Entity(id="e1", type="Person", name="김철수", attributes={"age": 30})
        assert e.attributes["age"] == 30


class TestEntityTypeDef:
    def test_persona_mode_is_optional_for_legacy_ontologies(self) -> None:
        type_def = EntityTypeDef(name="PolicyDocument", description="policy")
        assert type_def.persona_mode is None

    def test_persona_mode_enum(self) -> None:
        type_def = EntityTypeDef(
            name="PolicyDocument",
            description="policy",
            persona_mode=PersonaMode.CONTEXT_ONLY,
        )
        assert type_def.persona_mode is PersonaMode.CONTEXT_ONLY


class TestOntologyA:
    def test_naive_timestamp_rejected(self, sample_agent_profile: AgentProfile) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            OntologyA(
                seed=42,
                agent_count=1,
                preset=Preset.QUICK,
                source_document="test.pdf",
                simulation_requirement="test",
                generated_at=datetime(2026, 4, 1, 10, 0),  # naive
                ontology=Ontology(entity_types=[], edge_types=[]),
                agents={"agent_0001": sample_agent_profile},
            )

    def test_valid_construction(self, sample_agent_profile: AgentProfile) -> None:
        a = OntologyA(
            seed=42,
            agent_count=1,
            preset=Preset.QUICK,
            source_document="test.pdf",
            simulation_requirement="AI 규제 정책 72시간 여론 시뮬레이션",
            generated_at=datetime(2026, 4, 1, 10, 0, tzinfo=UTC),
            ontology=Ontology(entity_types=[], edge_types=[]),
            agents={"agent_0001": sample_agent_profile},
        )
        assert a.version == 1
        assert a.preset is Preset.QUICK


class TestOntologyB:
    def test_default_config(self) -> None:
        b = OntologyB(stores={})
        assert b.config.episodic_max == 10
        assert b.config.semantic_decay_rate == 0.05

    def test_with_store(self) -> None:
        store = MemoryStore(
            agent_id="agent_0001",
            semantic=[
                SemanticMemory(id="seed_0001_1", summary="test memory", topics=["AI"]),
            ],
        )
        b = OntologyB(stores={"agent_0001": store})
        assert len(b.stores["agent_0001"].semantic) == 1


class TestMemoryConfig:
    def test_frozen(self) -> None:
        mc = MemoryConfig()
        with pytest.raises(ValidationError):
            mc.episodic_max = 20

    def test_rejects_invalid_rates(self) -> None:
        with pytest.raises(ValidationError):
            MemoryConfig(episodic_decay_rate=2.0)


class TestPreset:
    def test_enum_values(self) -> None:
        assert Preset.QUICK.value == "quick"
        assert Preset.STANDARD.value == "standard"
        assert Preset.FULL.value == "full"
