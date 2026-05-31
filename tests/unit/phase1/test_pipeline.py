"""OntologyPipeline unit tests with fully mocked LLM."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from litemiro.phase1.entity_extractor import EntityExtractor
from litemiro.phase1.models import (
    AgentProfile,
    AgentSeed,
    ExtractionResult,
    Ontology,
    Preset,
    TextChunk,
)
from litemiro.phase1.ontology_generator import OntologyGenerator
from litemiro.phase1.pipeline import OntologyPipeline, OntologyResumeState, PipelineConfig
from litemiro.phase1.profile_generator import ProfileGenerator
from litemiro.phase1.validator import OntologyValidator, ValidationResult

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
    assert OntologyValidator().validate(a, b).valid
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


@pytest.mark.asyncio
async def test_pipeline_stops_before_write_on_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
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

    def _fail_validation(
        self: OntologyValidator,
        a: object,
        b: object,
    ) -> ValidationResult:
        return ValidationResult(valid=False, errors=["forced validation failure"])

    monkeypatch.setattr(OntologyValidator, "validate", _fail_validation)

    with pytest.raises(ValueError, match="ontology validation failed"):
        await OntologyPipeline(config, llm).run()

    assert not (tmp_path / "ontology_a_persona.json").exists()
    assert not (tmp_path / "ontology_b_memory.json").exists()


# --- #126: content filter fallback 단계별 재시도 (인-메모리 resume) ---------------
#
# generator 를 "1차 호출은 content filter 예외, 2차는 정상" 으로 monkeypatch 해
# fallback 재시도를 시뮬한다 (각 generator 전체를 patch 하므로 내부 retry/sleep
# 우회 — 테스트가 빠르다). 핵심 검증: 막힌 step 이전 산출물이 동일 객체로 보존돼
# 재호출되지 않는다 (state identity).
_FILTER_EXC = "provider error: data_inspection_failed"


def _resume_config(tmp_path: Path) -> PipelineConfig:
    doc = tmp_path / "doc.txt"
    doc.write_text("AI 규제 정책. 김기자는 일간지 소속이다.", encoding="utf-8")
    return PipelineConfig(
        input_path=doc,
        requirement="AI 규제 시뮬레이션",
        preset=Preset.QUICK,
        seed=42,
        output_dir=tmp_path,
        model="m",
    )


@pytest.mark.asyncio
async def test_resume_from_step2_reuses_step1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Step2(extract) 차단 → 재시도는 Step1(ontology) 재호출 없이 재개."""
    config = _resume_config(tmp_path)
    llm = _QueueLLM([ONTOLOGY_RESP, EXTRACT_RESP, PROFILE_RESP])
    orig_extract = EntityExtractor.extract
    calls = {"n": 0}

    async def _flaky(
        self: EntityExtractor, batches: list[list[TextChunk]], ontology: Ontology
    ) -> ExtractionResult:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError(_FILTER_EXC)
        return await orig_extract(self, batches, ontology)

    monkeypatch.setattr(EntityExtractor, "extract", _flaky)

    state = OntologyResumeState()
    with pytest.raises(RuntimeError, match="data_inspection_failed"):
        await OntologyPipeline(config, llm).run(state=state)
    assert state.ontology is not None
    assert state.extraction_result is None
    onto_obj = state.ontology

    a, _b = await OntologyPipeline(config, llm).run(state=state)
    assert state.ontology is onto_obj  # Step1 재호출 안 됨 (동일 객체)
    assert state.extraction_result is not None
    assert len(a.agents) >= 1


@pytest.mark.asyncio
async def test_resume_from_step4_reuses_step1_step2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Step4(profile) 차단 → Step1·2 산출물 보존, Step4 만 재개."""
    config = _resume_config(tmp_path)
    llm = _QueueLLM([ONTOLOGY_RESP, EXTRACT_RESP, PROFILE_RESP])
    orig_gen = ProfileGenerator.generate
    calls = {"n": 0}

    async def _flaky(
        self: ProfileGenerator, seeds: list[AgentSeed], requirement: str
    ) -> list[AgentProfile]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError(_FILTER_EXC)
        return await orig_gen(self, seeds, requirement)

    monkeypatch.setattr(ProfileGenerator, "generate", _flaky)

    state = OntologyResumeState()
    with pytest.raises(RuntimeError, match="data_inspection_failed"):
        await OntologyPipeline(config, llm).run(state=state)
    onto_obj, extr_obj = state.ontology, state.extraction_result
    assert onto_obj is not None
    assert extr_obj is not None
    assert state.profiles is None

    a, _b = await OntologyPipeline(config, llm).run(state=state)
    assert state.ontology is onto_obj
    assert state.extraction_result is extr_obj
    assert state.profiles is not None
    assert len(a.agents) >= 1


@pytest.mark.asyncio
async def test_resume_from_step1_reuses_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Step1(ontology) 차단 → Step0(document) 보존, Step1 부터 재개."""
    config = _resume_config(tmp_path)
    llm = _QueueLLM([ONTOLOGY_RESP, EXTRACT_RESP, PROFILE_RESP])
    orig_gen = OntologyGenerator.generate
    calls = {"n": 0}

    async def _flaky(self: OntologyGenerator, text: str, requirement: str) -> Ontology:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError(_FILTER_EXC)
        return await orig_gen(self, text, requirement)

    monkeypatch.setattr(OntologyGenerator, "generate", _flaky)

    state = OntologyResumeState()
    with pytest.raises(RuntimeError, match="data_inspection_failed"):
        await OntologyPipeline(config, llm).run(state=state)
    assert state.document_text is not None
    assert state.ontology is None
    doc_text = state.document_text

    a, _b = await OntologyPipeline(config, llm).run(state=state)
    assert state.document_text is doc_text  # Step0 재실행 안 됨
    assert state.ontology is not None
    assert len(a.agents) >= 1
