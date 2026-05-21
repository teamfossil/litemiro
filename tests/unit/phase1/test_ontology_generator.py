"""OntologyGenerator unit tests."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from litemiro.interfaces import LLMClient
from litemiro.phase1.ontology_generator import OntologyGenerator

VALID_ONTOLOGY_RESPONSE = json.dumps(
    {
        "entity_types": [
            {
                "name": "Journalist",
                "description": "보도 기사를 작성하는 기자",
                "attributes": ["name", "affiliation", "beat"],
            },
            {"name": "Politician", "description": "정치인", "attributes": ["name", "party"]},
            {"name": "Organization", "description": "조직/기관", "attributes": ["name", "type"]},
            {
                "name": "Researcher",
                "description": "연구자",
                "attributes": ["name", "institution", "field"],
            },
            {"name": "Activist", "description": "시민 활동가", "attributes": ["name", "group"]},
        ],
        "edge_types": [
            {
                "name": "REPORTS_ON",
                "source": "Journalist",
                "target": "Organization",
                "description": "취재/보도",
            },
            {
                "name": "WORKS_FOR",
                "source": "Journalist",
                "target": "Organization",
                "description": "소속",
            },
            {
                "name": "OPPOSES",
                "source": "Politician",
                "target": "Politician",
                "description": "반대 입장",
            },
            {
                "name": "ALLIES_WITH",
                "source": "Politician",
                "target": "Activist",
                "description": "연대",
            },
            {
                "name": "RESEARCHES",
                "source": "Researcher",
                "target": "Organization",
                "description": "연구 대상",
            },
            {
                "name": "BELONGS_TO",
                "source": "Researcher",
                "target": "Organization",
                "description": "소속 기관",
            },
        ],
    }
)


@pytest.mark.asyncio
async def test_generate_returns_ontology(fake_llm: Callable[..., LLMClient]) -> None:
    llm = fake_llm(VALID_ONTOLOGY_RESPONSE)
    gen = OntologyGenerator(llm=llm, model="test-model")
    ontology = await gen.generate("테스트 문서 텍스트", "AI 규제 시뮬레이션")
    assert len(ontology.entity_types) == 5
    assert len(ontology.edge_types) == 6
    assert ontology.entity_types[0].name == "Journalist"


@pytest.mark.asyncio
async def test_generate_uses_correct_model(fake_llm: Callable[..., LLMClient]) -> None:
    client = fake_llm(VALID_ONTOLOGY_RESPONSE)
    gen = OntologyGenerator(llm=client, model="custom-model")
    await gen.generate("doc", "req")
    assert client.calls[0][2] == "custom-model"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_generate_handles_json_with_markdown_fence(
    fake_llm: Callable[..., LLMClient],
) -> None:
    wrapped = f"```json\n{VALID_ONTOLOGY_RESPONSE}\n```"
    llm = fake_llm(wrapped)
    gen = OntologyGenerator(llm=llm, model="test")
    ontology = await gen.generate("doc", "req")
    assert len(ontology.entity_types) >= 1
