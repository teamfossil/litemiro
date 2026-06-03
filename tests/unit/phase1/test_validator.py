"""OntologyValidator unit tests."""

from __future__ import annotations

from datetime import UTC, datetime

from litemiro.phase1.models import (
    ActorKind,
    AgentOrigin,
    AgentProfile,
    BehaviorTendency,
    EntityTypeDef,
    KeyRelationship,
    MemoryStore,
    Ontology,
    OntologyA,
    OntologyB,
    PersonaMode,
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
    agent_id: str,
    ideology: float = 0.5,
    stance: float = 0.5,
    origin: AgentOrigin = AgentOrigin.EXTRACTED,
    following: list[str] | None = None,
    topics: list[str] | None = None,
) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id,
        name=f"Agent {agent_id}",
        entity_type="Person",
        origin=origin,
        skeleton={"layer": "test"},
        ideology=ideology,
        stance=stance,
        topics=topics or ["AI"],
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

    def test_context_only_entity_type_cannot_be_direct_agent(self) -> None:
        profile = _make_profile("policy_ai_basic_act", topics=["AI 기본법"])
        profile = profile.model_copy(update={"entity_type": "PolicyDocument", "name": "AI 기본법"})
        a = _make_a({"policy_ai_basic_act": profile})
        a = a.model_copy(
            update={
                "ontology": Ontology(
                    entity_types=[
                        EntityTypeDef(
                            name="PolicyDocument",
                            description="policy document",
                            persona_mode=PersonaMode.CONTEXT_ONLY,
                        )
                    ],
                    edge_types=[],
                )
            }
        )
        result = OntologyValidator().validate(
            a,
            _make_b(["policy_ai_basic_act"]),
        )

        assert any("context_only" in error for error in result.errors)

    def test_representative_entity_type_requires_actor_metadata(self) -> None:
        profile = _make_profile("gov_msit")
        profile = profile.model_copy(
            update={"entity_type": "GovernmentAgency", "name": "과학기술정보통신부"}
        )
        a = _make_a({"gov_msit": profile})
        a = a.model_copy(
            update={
                "ontology": Ontology(
                    entity_types=[
                        EntityTypeDef(
                            name="GovernmentAgency",
                            description="public agency",
                            persona_mode=PersonaMode.REPRESENTATIVE,
                        )
                    ],
                    edge_types=[],
                )
            }
        )
        result = OntologyValidator().validate(a, _make_b(["gov_msit"]))

        assert any("actor_kind='representative'" in error for error in result.errors)

    def test_representative_entity_type_passes_with_source_tracking(self) -> None:
        profile = _make_profile("gov_msit")
        profile = profile.model_copy(
            update={
                "entity_type": "GovernmentAgency",
                "name": "과학기술정보통신부 public official",
                "actor_kind": ActorKind.REPRESENTATIVE,
                "represented_entity_id": "gov_msit",
            }
        )
        a = _make_a({"gov_msit": profile})
        a = a.model_copy(
            update={
                "ontology": Ontology(
                    entity_types=[
                        EntityTypeDef(
                            name="GovernmentAgency",
                            description="public agency",
                            persona_mode=PersonaMode.REPRESENTATIVE,
                        )
                    ],
                    edge_types=[],
                )
            }
        )
        result = OntologyValidator().validate(a, _make_b(["gov_msit"]))

        assert result.valid

    def test_explicit_persona_mode_drives_actor_validation(self) -> None:
        profile = _make_profile("custom_org")
        profile = profile.model_copy(
            update={
                "entity_type": "StakeholderNode",
                "actor_kind": ActorKind.REPRESENTATIVE,
                "represented_entity_id": "custom_org",
            }
        )
        a = _make_a({"custom_org": profile})
        a = a.model_copy(
            update={
                "ontology": Ontology(
                    entity_types=[
                        EntityTypeDef(
                            name="StakeholderNode",
                            description="custom type",
                            persona_mode=PersonaMode.CONTEXT_ONLY,
                        )
                    ],
                    edge_types=[],
                )
            }
        )

        result = OntologyValidator().validate(a, _make_b(["custom_org"]))

        assert any("context_only" in error for error in result.errors)

    def test_new_actor_contract_enables_heuristic_validation(self) -> None:
        profile = _make_profile("gov_msit")
        profile = profile.model_copy(
            update={
                "entity_type": "GovernmentAgency",
                "name": "과학기술정보통신부",
                "skeleton": {"actor_kind": "direct_person", "layer": "GovernmentAgency"},
            }
        )

        result = OntologyValidator().validate(_make_a({"gov_msit": profile}), _make_b(["gov_msit"]))

        assert any("actor_kind='representative'" in error for error in result.errors)

    def test_ideology_distribution_warning(self) -> None:
        agents = {
            "a1": _make_profile("a1", ideology=0.1),
            "a2": _make_profile("a2", ideology=0.15),
            "a3": _make_profile("a3", ideology=0.2),
        }
        result = OntologyValidator().validate(_make_a(agents), _make_b(["a1", "a2", "a3"]))
        assert len(result.warnings) >= 1

    def test_derived_stance_distribution_warning(self) -> None:
        agents = {
            f"a{i}": _make_profile(
                f"a{i}",
                stance=0.2,
                origin=AgentOrigin.DERIVED,
            )
            for i in range(10)
        }
        result = OntologyValidator().validate(_make_a(agents), _make_b(list(agents)))
        assert any("derived stance distribution" in warning for warning in result.warnings)

    def test_balanced_derived_stance_distribution_does_not_warn(self) -> None:
        stances = [0.2] * 3 + [0.5] * 4 + [0.8] * 3
        agents = {
            f"a{i}": _make_profile(
                f"a{i}",
                stance=stance,
                origin=AgentOrigin.DERIVED,
            )
            for i, stance in enumerate(stances)
        }
        result = OntologyValidator().validate(_make_a(agents), _make_b(list(agents)))
        assert not any("derived stance distribution" in warning for warning in result.warnings)

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

    def test_persona_memory_topic_overlap_is_case_insensitive(self) -> None:
        agents = {"a1": _make_profile("a1", topics=["policy"])}
        b = OntologyB(
            stores={
                "a1": MemoryStore(
                    agent_id="a1",
                    semantic=[
                        SemanticMemory(
                            id="seed_a1_1",
                            summary="policy update",
                            topics=["Policy"],
                        )
                    ],
                )
            }
        )

        result = OntologyValidator().validate(_make_a(agents), b)

        assert result.valid
        assert not any("persona topics" in warning for warning in result.warnings)

    def test_empty_semantic_memory_skips_topic_mismatch_warning(self) -> None:
        agents = {"a1": _make_profile("a1")}
        b = OntologyB(stores={"a1": MemoryStore(agent_id="a1", semantic=[])})

        result = OntologyValidator().validate(_make_a(agents), b)

        assert result.valid
        assert not any("persona topics" in warning for warning in result.warnings)

    def test_persona_multi_word_phrase_overlaps_memory_bare_noun(self) -> None:
        """LLM 이 페르소나 토픽을 'AI 윤리 가이드라인' 같은 다어절 명사구로
        뽑고 메모리는 'AI' 같은 단일 명사로 추출돼 whole-string 매칭이 영구히
        실패하던 100 중 19 false positive (run debug3). 토큰 단위 비교로 잡혀야."""
        agents = {"a1": _make_profile("a1", topics=["AI 윤리 가이드라인", "내부 감독 체계"])}
        b = OntologyB(
            stores={
                "a1": MemoryStore(
                    agent_id="a1",
                    semantic=[
                        SemanticMemory(
                            id="seed_a1_1",
                            summary="플랫폼 정책",
                            topics=["인터넷 플랫폼", "Company", "AI"],
                        )
                    ],
                )
            }
        )

        result = OntologyValidator().validate(_make_a(agents), b)

        assert result.valid
        assert not any("persona topics" in warning for warning in result.warnings)

    def test_persona_token_overlaps_camelcase_memory_entity_type(self) -> None:
        """`_derive_entity_topics` 가 entity.type 을 그대로 토픽에 넣어 'AIProduct'
        같은 CamelCase 토큰이 메모리에 박힌다. 페르소나의 'AI' 와 매칭되려면
        CamelCase 분해가 필요. (run debug3 hyperclova 케이스)"""
        agents = {"a1": _make_profile("a1", topics=["국산 AI 기술 강화"])}
        b = OntologyB(
            stores={
                "a1": MemoryStore(
                    agent_id="a1",
                    semantic=[
                        SemanticMemory(
                            id="seed_a1_1",
                            summary="네이버가 개발한 LLM 제품",
                            topics=["AIProduct", "네이버", "Company"],
                        )
                    ],
                )
            }
        )

        result = OntologyValidator().validate(_make_a(agents), b)

        assert result.valid
        assert not any("persona topics" in warning for warning in result.warnings)

    def test_persona_korean_particle_stripped_before_overlap(self) -> None:
        """페르소나 LLM 산출에 'AI 안전성의 물리적 한계' 처럼 조사가 붙은 어절이
        섞여 들어와도 stem 으로 비교돼야 메모리의 '반도체' 와 매칭. (run debug3
        samsung_electronics 케이스)"""
        agents = {"a1": _make_profile("a1", topics=["반도체 기반 AI 안전성의 물리적 한계"])}
        b = OntologyB(
            stores={
                "a1": MemoryStore(
                    agent_id="a1",
                    semantic=[
                        SemanticMemory(
                            id="seed_a1_1",
                            summary="반도체 공정",
                            topics=["반도체/하드웨어", "Company"],
                        )
                    ],
                )
            }
        )

        result = OntologyValidator().validate(_make_a(agents), b)

        assert result.valid
        assert not any("persona topics" in warning for warning in result.warnings)

    def test_korean_compound_substring_overlap(self) -> None:
        """한국어는 '개인정보' 와 '개인정보보호위원회' 처럼 단어 경계 없이
        의미 단위가 합쳐지는 합성어가 흔해 토큰 분리로는 잡히지 않는다.
        (run debug3 gdpr_guideline 케이스) 길이 3 이상에서 substring fallback."""
        agents = {"a1": _make_profile("a1", topics=["개인정보 보호", "정보 주체 권리"])}
        b = OntologyB(
            stores={
                "a1": MemoryStore(
                    agent_id="a1",
                    semantic=[
                        SemanticMemory(
                            id="seed_a1_1",
                            summary="규제 기관",
                            topics=["개인정보보호위원회", "GovernmentAgency"],
                        )
                    ],
                )
            }
        )

        result = OntologyValidator().validate(_make_a(agents), b)

        assert result.valid
        assert not any("persona topics" in warning for warning in result.warnings)

    def test_short_ascii_substring_does_not_false_match(self) -> None:
        """길이 2 ASCII 약어는 substring 비교 대상에서 제외해야 한다 —
        'ai' in 'aim' 같은 의미 무관한 매칭이 잡혀서는 안 된다."""
        agents = {"a1": _make_profile("a1", topics=["ai"])}
        b = OntologyB(
            stores={
                "a1": MemoryStore(
                    agent_id="a1",
                    semantic=[
                        SemanticMemory(
                            id="seed_a1_1",
                            summary="목표 설정",
                            topics=["aim"],
                        )
                    ],
                )
            }
        )

        result = OntologyValidator().validate(_make_a(agents), b)

        assert result.valid
        assert any("persona topics" in warning for warning in result.warnings)

    def test_unrelated_topics_still_warn_after_tokenization(self) -> None:
        """토큰 단위로 풀어도 진짜 무관한 토픽 쌍은 여전히 경고해야 한다 —
        regression guard for over-permissive matching."""
        agents = {"a1": _make_profile("a1", topics=["축구 경기 중계", "야구 결과"])}
        b = OntologyB(
            stores={
                "a1": MemoryStore(
                    agent_id="a1",
                    semantic=[
                        SemanticMemory(
                            id="seed_a1_1",
                            summary="정책 분석",
                            topics=["RegulatoryIssue", "데이터", "프라이버시"],
                        )
                    ],
                )
            }
        )

        result = OntologyValidator().validate(_make_a(agents), b)

        assert result.valid
        assert any("persona topics" in warning for warning in result.warnings)
