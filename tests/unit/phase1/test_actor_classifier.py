from __future__ import annotations

from litemiro.phase1.actor_classifier import ActorClassifier, infer_persona_mode
from litemiro.phase1.models import Entity, EntityTypeDef, Ontology, PersonaMode


def test_explicit_persona_mode_wins_over_heuristic() -> None:
    ontology = Ontology(
        entity_types=[
            EntityTypeDef(
                name="PolicyDocument",
                description="policy document",
                persona_mode=PersonaMode.REPRESENTATIVE,
            )
        ],
        edge_types=[],
    )
    entity = Entity(id="ai_basic_act", type="PolicyDocument", name="AI 기본법")

    assert ActorClassifier(ontology).persona_mode_for_entity(entity) is PersonaMode.REPRESENTATIVE


def test_person_like_types_are_direct_person() -> None:
    assert infer_persona_mode(type_name="Journalist", name="김기자") is PersonaMode.DIRECT_PERSON
    assert (
        infer_persona_mode(type_name="GovernmentOfficial", name="정책 담당자")
        is PersonaMode.DIRECT_PERSON
    )


def test_institution_like_types_are_representative() -> None:
    assert (
        infer_persona_mode(type_name="GovernmentAgency", name="과학기술정보통신부")
        is PersonaMode.REPRESENTATIVE
    )
    assert (
        infer_persona_mode(type_name="MediaOrganization", name="한겨레신문")
        is PersonaMode.REPRESENTATIVE
    )


def test_policy_and_regulatory_objects_are_context_only() -> None:
    assert (
        infer_persona_mode(type_name="PolicyDocument", name="AI 기본법") is PersonaMode.CONTEXT_ONLY
    )
    assert (
        infer_persona_mode(type_name="RegulatoryIssue", name="데이터 주권")
        is PersonaMode.CONTEXT_ONLY
    )
    assert (
        infer_persona_mode(type_name="InternationalRegulatoryFramework", name="EU AI Act")
        is PersonaMode.CONTEXT_ONLY
    )


def test_unknown_types_default_to_context_only() -> None:
    assert (
        infer_persona_mode(type_name="EmergentTopic", name="AI 안전성") is PersonaMode.CONTEXT_ONLY
    )
