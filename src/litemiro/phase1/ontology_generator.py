from __future__ import annotations

import json
import logging

from json_repair import repair_json
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from litemiro.phase1.content_filter import retry_unless_content_filter
from litemiro.phase1.llm import Phase1LLMClient, response_text
from litemiro.phase1.models import EdgeTypeDef, EntityTypeDef, Ontology

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an ontology design expert. Analyze the provided document and simulation requirement,
then extract a domain ontology as structured JSON.

Output ONLY valid JSON with this exact shape:
{
  "entity_types": [
    {
      "name": "PascalCaseName",
      "description": "what this entity type represents",
      "attributes": ["attr1", "attr2"]
    }
  ],
  "edge_types": [
    {
      "name": "PascalCaseName",
      "source": "SourceEntityType",
      "target": "TargetEntityType",
      "description": "what this relationship means"
    }
  ]
}

Rules:
- Extract 5-15 entity types based on document complexity (do not pad or truncate artificially).
- Extract 6-10 edge/relationship types that are meaningful for the simulation.
- All type names must be PascalCase.
- Attributes should be concrete, observable properties of that entity type.
- No commentary, no markdown fences — raw JSON only.
"""


class OntologyGenerator:
    def __init__(
        self,
        llm: Phase1LLMClient,
        model: str = "openrouter/qwen/qwen-plus",
    ) -> None:
        self._llm = llm
        self._model = model

    @retry(
        retry=retry_if_exception(retry_unless_content_filter),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def generate(self, document_text: str, simulation_requirement: str) -> Ontology:
        user_prompt = (
            f"Simulation requirement:\n{simulation_requirement}\n\nDocument:\n{document_text}"
        )
        response = await self._llm.complete(
            system=_SYSTEM_PROMPT,
            user=user_prompt,
            model=self._model,
        )
        raw = response_text(response)
        repaired = repair_json(raw)
        data = json.loads(repaired)
        entity_types = [EntityTypeDef(**et) for et in data.get("entity_types", [])]
        edge_types = [EdgeTypeDef(**ed) for ed in data.get("edge_types", [])]
        return Ontology(entity_types=entity_types, edge_types=edge_types)
