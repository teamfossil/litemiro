"""EntityExtractor unit tests."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from litemiro.phase1.entity_extractor import EntityExtractor
from litemiro.phase1.llm import Phase1LLMClient
from litemiro.phase1.models import Ontology, TextChunk

VALID_EXTRACTION_RESPONSE = json.dumps(
    {
        "entities": [
            {
                "id": "journalist_kim",
                "type": "Journalist",
                "name": "김영수",
                "attributes": {"affiliation": "한겨레", "beat": "정치"},
                "summary": "한겨레 정치부 기자",
                "source_chunks": [0],
            }
        ],
        "relationships": [
            {
                "source": "journalist_kim",
                "target": "org_hankyoreh",
                "type": "WORKS_FOR",
                "description": "한겨레 소속 기자",
            }
        ],
    }
)


@pytest.mark.asyncio
async def test_extract_single_batch(
    fake_llm: Callable[..., Phase1LLMClient],
    sample_ontology: Ontology,
    sample_chunks: list[TextChunk],
) -> None:
    llm = fake_llm(VALID_EXTRACTION_RESPONSE)
    extractor = EntityExtractor(llm=llm, model="test")
    result = await extractor.extract([sample_chunks], sample_ontology)
    assert len(result.entities) >= 1
    assert result.entities[0].name == "김영수"


@pytest.mark.asyncio
async def test_extract_merges_batches(
    fake_llm: Callable[..., Phase1LLMClient],
    sample_ontology: Ontology,
    sample_chunks: list[TextChunk],
) -> None:
    resp2 = json.dumps(
        {
            "entities": [
                {
                    "id": "org_hankyoreh",
                    "type": "Organization",
                    "name": "한겨레",
                    "attributes": {},
                    "summary": "신문사",
                    "source_chunks": [1],
                }
            ],
            "relationships": [],
        }
    )
    llm = fake_llm(VALID_EXTRACTION_RESPONSE, resp2)
    extractor = EntityExtractor(llm=llm, model="test")
    result = await extractor.extract([sample_chunks[:1], sample_chunks[1:]], sample_ontology)
    assert len(result.entities) >= 2


@pytest.mark.asyncio
async def test_extract_empty_batches(
    fake_llm: Callable[..., Phase1LLMClient],
    sample_ontology: Ontology,
) -> None:
    llm = fake_llm()
    extractor = EntityExtractor(llm=llm, model="test")
    result = await extractor.extract([], sample_ontology)
    assert len(result.entities) == 0
    assert len(result.relationships) == 0


@pytest.mark.asyncio
async def test_extract_continues_when_one_batch_fails(
    sample_ontology: Ontology,
    sample_chunks: list[TextChunk],
) -> None:
    class _PartialFailLLM:
        async def complete(self, *, system: str, user: str, model: str) -> str:
            if "[chunk 0]" in user:
                raise RuntimeError("batch failed")
            return VALID_EXTRACTION_RESPONSE

    extractor = EntityExtractor(llm=_PartialFailLLM(), model="test")
    result = await extractor.extract([sample_chunks[:1], sample_chunks[1:]], sample_ontology)
    assert len(result.entities) == 1
    assert result.entities[0].id == "journalist_kim"
