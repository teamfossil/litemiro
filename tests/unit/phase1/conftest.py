"""Shared fixtures for Phase 1 unit tests."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from litemiro.interfaces import LLMClient
from litemiro.phase1.models import (
    AgentOrigin,
    AgentProfile,
    AgentSeed,
    BehaviorTendency,
    Edge,
    EdgeTypeDef,
    Entity,
    EntityTypeDef,
    ExtractionResult,
    Ontology,
    TextChunk,
)


class FakeLLMClient:
    def __init__(self, *responses: str) -> None:
        self._responses: list[str] = list(responses)
        self.calls: list[tuple[str, str, str]] = []

    async def complete(self, *, system: str, user: str, model: str) -> str:
        self.calls.append((system, user, model))
        if not self._responses:
            raise RuntimeError("FakeLLMClient: no more queued responses")
        return self._responses.pop(0)


@pytest.fixture
def fake_llm() -> Callable[..., LLMClient]:
    def _make(*responses: str) -> LLMClient:
        return FakeLLMClient(*responses)

    return _make


@pytest.fixture
def sample_ontology() -> Ontology:
    return Ontology(
        entity_types=[
            EntityTypeDef(
                name="Journalist",
                description="보도 기사를 작성하는 기자",
                attributes=["name", "affiliation", "beat"],
            ),
            EntityTypeDef(
                name="Politician", description="정치인", attributes=["name", "party", "position"]
            ),
            EntityTypeDef(
                name="Organization", description="조직/기관", attributes=["name", "type"]
            ),
        ],
        edge_types=[
            EdgeTypeDef(
                name="REPORTS_ON",
                source="Journalist",
                target="Organization",
                description="기자가 특정 조직을 취재/보도",
            ),
            EdgeTypeDef(
                name="WORKS_FOR",
                source="Journalist",
                target="Organization",
                description="기자가 소속된 언론사",
            ),
            EdgeTypeDef(
                name="OPPOSES",
                source="Politician",
                target="Politician",
                description="정치적 반대 입장",
            ),
        ],
    )


@pytest.fixture
def sample_entities() -> list[Entity]:
    return [
        Entity(
            id="journalist_kim",
            type="Journalist",
            name="김영수",
            attributes={"affiliation": "한겨레", "beat": "정치"},
            summary="진보 성향의 정치부 기자",
            source_chunks=[0, 2],
        ),
        Entity(
            id="org_hankyoreh",
            type="Organization",
            name="한겨레",
            attributes={"type": "언론사"},
            summary="진보 성향 신문사",
            source_chunks=[0],
        ),
        Entity(
            id="politician_park",
            type="Politician",
            name="박영선",
            attributes={"party": "더불어민주당", "position": "위원장"},
            summary="개인정보보호위원회 위원장",
            source_chunks=[1],
        ),
    ]


@pytest.fixture
def sample_edges() -> list[Edge]:
    return [
        Edge(
            source="journalist_kim",
            target="org_hankyoreh",
            type="WORKS_FOR",
            description="한겨레 소속 기자",
        ),
        Edge(
            source="journalist_kim",
            target="politician_park",
            type="REPORTS_ON",
            description="박영선 위원장 관련 보도",
        ),
    ]


@pytest.fixture
def sample_extraction(sample_entities: list[Entity], sample_edges: list[Edge]) -> ExtractionResult:
    return ExtractionResult(entities=sample_entities, relationships=sample_edges)


@pytest.fixture
def sample_chunks() -> list[TextChunk]:
    return [
        TextChunk(
            index=0,
            text="AI 규제 정책에 대한 분석 보고서. 최근 인공지능 기술의 급속한 발전에 따라 각국 정부는 AI 규제 정책을 수립하고 있다.",
            start_char=0,
            end_char=80,
        ),
        TextChunk(
            index=1,
            text="과학기술정보통신부는 AI 규제의 주무 부처로, AI 기본법의 시행령을 마련하고 있다.",
            start_char=70,
            end_char=140,
        ),
    ]


@pytest.fixture
def sample_agent_seeds(sample_entities: list[Entity]) -> list[AgentSeed]:
    return [
        AgentSeed(
            agent_id="agent_0001",
            entity=sample_entities[0],
            origin=AgentOrigin.EXTRACTED,
            context="한겨레 소속 정치부 기자",
        ),
        AgentSeed(
            agent_id="agent_0002",
            entity=sample_entities[2],
            origin=AgentOrigin.EXTRACTED,
            context="개인정보보호위원회 위원장",
        ),
    ]


@pytest.fixture
def sample_agent_profile() -> AgentProfile:
    return AgentProfile(
        agent_id="agent_0001",
        name="김영수",
        entity_type="Journalist",
        origin=AgentOrigin.EXTRACTED,
        skeleton={"layer": "미디어", "region": "서울", "affiliation": "한겨레"},
        ideology=0.3,
        topics=["정치", "경제"],
        sensitive_topics=["부동산"],
        personality="날카로운 분석력과 비판적 시각",
        speech_style="~다 체, 통계 인용, 반어적 표현 사용",
        background="한겨레 입사 10년차 정치부 기자",
        behavior_tendency=BehaviorTendency(
            post_rate=0.6, reply_rate=0.4, repost_rate=0.3, controversy_affinity=0.7
        ),
        initial_following=["agent_0002"],
    )
