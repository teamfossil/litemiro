from __future__ import annotations

import asyncio
import json
import logging

from json_repair import repair_json
from tenacity import retry, stop_after_attempt, wait_exponential

from litemiro.phase1.llm import Phase1LLMClient, response_text
from litemiro.phase1.models import Edge, Entity, ExtractionResult, Ontology, TextChunk

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an entity and relationship extraction expert. Given a domain ontology and text,
extract all entities and relationships present in the text.

Output ONLY valid JSON with this exact shape:
{
  "entities": [
    {
      "id": "snake_case_unique_id",
      "type": "OntologyEntityType",
      "name": "exact name as it appears",
      "attributes": {"key": "value"},
      "summary": "one-sentence description",
      "source_chunks": [0]
    }
  ],
  "relationships": [
    {
      "source": "source_entity_id",
      "target": "target_entity_id",
      "type": "OntologyEdgeType",
      "description": "brief description",
      "weight": 1.0
    }
  ]
}

Rules:
- Use only entity types and edge types defined in the provided ontology.
- Entity IDs must be unique snake_case strings (transliterate non-ASCII names).
- Do not invent types not in the ontology.
- No commentary, no markdown fences — raw JSON only.
"""


class EntityExtractor:
    def __init__(
        self,
        llm: Phase1LLMClient,
        model: str = "openrouter/qwen/qwen-plus",
        max_concurrency: int = 5,
    ) -> None:
        self._llm = llm
        self._model = model
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def extract(self, chunks: list[list[TextChunk]], ontology: Ontology) -> ExtractionResult:
        tasks = [self._extract_batch(batch, ontology) for batch in chunks]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        merged_entities: dict[str, Entity] = {}
        merged_relationships: list[Edge] = []
        for result in results:
            if isinstance(result, BaseException):
                logger.warning("entity extraction batch failed; continuing", exc_info=result)
                continue
            for entity in result.entities:
                merged_entities[entity.id] = entity
            merged_relationships.extend(result.relationships)

        return ExtractionResult(
            entities=list(merged_entities.values()),
            relationships=merged_relationships,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def _extract_batch(self, batch: list[TextChunk], ontology: Ontology) -> ExtractionResult:
        ontology_json = json.dumps(
            {
                "entity_types": [et.model_dump() for et in ontology.entity_types],
                "edge_types": [ed.model_dump() for ed in ontology.edge_types],
            },
            ensure_ascii=False,
        )
        chunk_text = "\n\n".join(f"[chunk {c.index}]\n{c.text}" for c in batch)
        chunk_indices = [c.index for c in batch]

        user_prompt = f"Ontology:\n{ontology_json}\n\nText chunks {chunk_indices}:\n{chunk_text}"

        async with self._semaphore:
            response = await self._llm.complete(
                system=_SYSTEM_PROMPT,
                user=user_prompt,
                model=self._model,
            )

        raw = response_text(response)
        repaired = repair_json(raw)
        data = json.loads(repaired)
        entities = [Entity(**e) for e in data.get("entities", [])]
        relationships = [Edge(**r) for r in data.get("relationships", [])]
        return ExtractionResult(entities=entities, relationships=relationships)
