"""OntologyPipeline unit tests with fully mocked LLM."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from litemiro.phase1.models import Preset
from litemiro.phase1.pipeline import OntologyPipeline, PipelineConfig

ONTOLOGY_RESP = json.dumps(
    {
        "entity_types": [
            {"name": "Journalist", "description": "기자", "attributes": ["name"]},
            {"name": "Organization", "description": "조직", "attributes": ["name"]},
        ],
        "edge_types": [
            {
                "name": "WORKS_FOR",
                "source": "Journalist",
                "target": "Organization",
                "description": "소속",
            },
        ],
    }
)

EXTRACT_RESP = json.dumps(
    {
        "entities": [
            {
                "id": "journalist_kim",
                "type": "Journalist",
                "name": "김기자",
                "attributes": {},
                "summary": "정치부 기자",
                "source_chunks": [0],
            },
            {
                "id": "org_daily",
                "type": "Organization",
                "name": "일간지",
                "attributes": {},
                "summary": "신문사",
                "source_chunks": [0],
            },
        ],
        "relationships": [
            {
                "source": "journalist_kim",
                "target": "org_daily",
                "type": "WORKS_FOR",
                "description": "소속 기자",
            },
        ],
    }
)

PROFILE_RESP = json.dumps(
    [
        {
            "agent_id": "journalist_kim",
            "personality": "비판적",
            "speech_style": "~다 체",
            "background": "10년차 기자",
            "ideology": 0.3,
            "topics": ["정치"],
            "sensitive_topics": [],
            "behavior_tendency": {
                "post_rate": 0.6,
                "reply_rate": 0.4,
                "repost_rate": 0.3,
                "controversy_affinity": 0.7,
            },
        },
        {
            "agent_id": "org_daily",
            "personality": "공식적",
            "speech_style": "보도 어투",
            "background": "종합 일간지",
            "ideology": 0.5,
            "topics": ["뉴스"],
            "sensitive_topics": [],
            "behavior_tendency": {
                "post_rate": 0.5,
                "reply_rate": 0.2,
                "repost_rate": 0.4,
                "controversy_affinity": 0.3,
            },
        },
    ]
)


class _QueueLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.call_count = 0

    async def complete(self, *, system: str, user: str, model: str) -> str:
        self.call_count += 1
        if self._responses:
            return self._responses.pop(0)
        return PROFILE_RESP


@pytest.mark.asyncio
async def test_pipeline_end_to_end(tmp_path: Path) -> None:
    doc = tmp_path / "doc.txt"
    doc.write_text("AI 규제 정책에 대한 보고서. 김기자는 일간지 소속이다.", encoding="utf-8")

    config = PipelineConfig(
        input_path=doc,
        requirement="AI 규제 시뮬레이션",
        preset=Preset.QUICK,
        seed=42,
        output_dir=tmp_path,
        model="test-model",
    )
    llm = _QueueLLM([ONTOLOGY_RESP, EXTRACT_RESP, PROFILE_RESP])
    a, b = await OntologyPipeline(config, llm).run()

    assert a.version == 1
    assert a.preset is Preset.QUICK
    assert len(a.agents) >= 1
    assert len(b.stores) == len(a.agents)
    assert (tmp_path / "ontology_a_persona.json").exists()
    assert (tmp_path / "ontology_b_memory.json").exists()


@pytest.mark.asyncio
async def test_pipeline_reads_txt(tmp_path: Path) -> None:
    doc = tmp_path / "doc.txt"
    doc.write_text("짧은 문서", encoding="utf-8")
    config = PipelineConfig(
        input_path=doc,
        requirement="test",
        preset=Preset.QUICK,
        seed=1,
        output_dir=tmp_path,
        model="m",
    )
    llm = _QueueLLM([ONTOLOGY_RESP, EXTRACT_RESP, PROFILE_RESP])
    pipeline = OntologyPipeline(config, llm)
    text = pipeline._read_document()
    assert "짧은 문서" in text


@pytest.mark.asyncio
async def test_pipeline_config_defaults() -> None:
    config = PipelineConfig(input_path=Path("x.pdf"), requirement="r")
    assert config.preset is Preset.QUICK
    assert config.seed == 42
