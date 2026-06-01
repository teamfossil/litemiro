"""Phase 1 — end-to-end ontology generation pipeline."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import structlog
from pydantic import BaseModel, Field

from litemiro.phase1.entity_extractor import EntityExtractor
from litemiro.phase1.llm import Phase1LLMClient
from litemiro.phase1.models import (
    PRESET_AGENT_COUNTS,
    AgentProfile,
    AgentSeed,
    ExtractionResult,
    MemoryConfig,
    MemoryStore,
    Ontology,
    OntologyA,
    OntologyB,
    Preset,
)
from litemiro.phase1.ontology_generator import OntologyGenerator
from litemiro.phase1.serializer import OntologySerializer
from litemiro.phase1.text_chunker import TextChunker
from litemiro.phase1.validator import OntologyValidator

log = structlog.get_logger(__name__)


class PipelineConfig(BaseModel):
    input_path: Path
    requirement: str
    preset: Preset = Preset.QUICK
    seed: int = 42
    output_dir: Path = Field(default_factory=lambda: Path("."))
    model: str = "openrouter/qwen/qwen-plus"


@dataclass
class OntologyResumeState:
    """content filter fallback 재시도 간 보존되는 LLM step 산출물 (#126).

    같은 async task 안에서 다음 모델로 재시도할 때 이미 성공한 LLM step
    (ontology / extraction / profiles)을 재호출하지 않도록 메모리에 들고 간다.
    막힌 step 의 필드는 None 으로 남아 그 step 부터 재개된다. 디스크 직렬화는
    하지 않는다 — 프로세스 크래시 복구가 아니라 한 코루틴 내 모델 루프 재시도다.
    """

    document_text: str | None = None
    ontology: Ontology | None = None
    extraction_result: ExtractionResult | None = None
    profiles: list[AgentProfile] | None = None
    profile_fallback_count: int = 0


class OntologyPipeline:
    def __init__(self, config: PipelineConfig, llm: Phase1LLMClient) -> None:
        self._config = config
        self._llm = llm
        # Step 4 (profile_generator) 의 fallback 카운트 — run() 후 CLI 가
        # 읽어 사용자에게 노출. #109: silent fallback 19% 사례 가시화.
        self.profile_fallback_count: int = 0

    async def run(  # noqa: PLR0915 — 7 step 시퀀스 + 검증/직렬화 → 자연스레 길다. 분할은 리팩토링 사안.
        self,
        *,
        on_progress: Callable[[str], None] | None = None,
        state: OntologyResumeState | None = None,
    ) -> tuple[OntologyA, OntologyB]:
        cfg = self._config
        target_count = PRESET_AGENT_COUNTS[cfg.preset]

        # #126: content filter fallback 재시도 시 이미 성공한 LLM step 을 건너뛰도록
        # 산출물을 ``state`` 에 누적한다. state 미지정이면 빈 state — 기존 동작 그대로.
        state = state or OntologyResumeState()

        # #126: step 진입 직전에 외부로 신호. ``OntologyStore`` 가 받아 DB row 의
        # ``active_step`` 컬럼에 박고 polling 응답으로 흘려보낸다. 호출자가
        # 콜백을 안 줬으면 no-op — pipeline 단독 호출 (CLI) 도 동일하게 동작.
        notify = on_progress or (lambda _step: None)

        # t0 는 가드 밖 — 재개로 step0 이 스킵돼도 step6 의 total_elapsed 가 참조한다.
        t0 = time.monotonic()
        # Step 0: Read document (순수, 캐시 — PDF 파싱 재실행 회피)
        if state.document_text is None:
            notify("step0_document")
            state.document_text = self._read_document()
            log.info(
                "step0_document_read",
                chars=len(state.document_text),
                elapsed=f"{time.monotonic() - t0:.2f}s",
            )
        document_text = state.document_text
        assert document_text is not None

        # Step 1: Generate ontology schema (LLM — content filter 가능)
        if state.ontology is None:
            notify("step1_ontology")
            t1 = time.monotonic()
            state.ontology = await OntologyGenerator(llm=self._llm, model=cfg.model).generate(
                document_text, cfg.requirement
            )
            log.info(
                "step1_ontology_generated",
                entity_types=len(state.ontology.entity_types),
                edge_types=len(state.ontology.edge_types),
                elapsed=f"{time.monotonic() - t1:.2f}s",
            )
        ontology = state.ontology
        assert ontology is not None

        # Step 2: Extract entities (LLM — content filter 가능). graph 는 순수 함수라
        # extraction_result 만 보존하고 매 시도 재구성한다 (LocalGraph 비직렬화 회피).
        if state.extraction_result is None:
            notify("step2_graph")
            t2 = time.monotonic()
            chunker = TextChunker()
            chunks = chunker.chunk(document_text)
            batches = chunker.batch(chunks)
            extractor = EntityExtractor(llm=self._llm, model=cfg.model)
            state.extraction_result = await extractor.extract(batches, ontology)
            log.info(
                "step2_extracted",
                entities=len(state.extraction_result.entities),
                relationships=len(state.extraction_result.relationships),
                elapsed=f"{time.monotonic() - t2:.2f}s",
            )
        extraction_result = state.extraction_result
        assert extraction_result is not None

        from litemiro.phase1.local_graph import LocalGraph  # noqa: PLC0415

        graph = LocalGraph.build(extraction_result)
        merged = graph.merge_duplicates()
        log.info(
            "step2_graph_built",
            entities=len(extraction_result.entities),
            merged_duplicates=merged,
        )

        # Step 3: Rank entities and expand to agent seeds
        notify("step3_seeds")
        t3 = time.monotonic()
        from litemiro.phase1.entity_ranker import EntityRanker  # noqa: PLC0415

        ranker = EntityRanker(graph=graph, simulation_requirement=cfg.requirement)
        ranked = ranker.rank()
        top_entities = [entity for entity, _ in ranked[:target_count]]
        core_seeds: list[AgentSeed] = [
            AgentSeed(
                agent_id=entity.id,
                entity=entity,
                origin="extracted",  # type: ignore[arg-type]
                context=ranker.build_entity_context(entity.id),
            )
            for entity in top_entities
        ]

        from litemiro.phase1.agent_expander import AgentExpander  # noqa: PLC0415

        expander = AgentExpander(graph=graph, requirement=cfg.requirement, seed=cfg.seed)
        seeds = expander.expand(core_seeds, target_count)
        log.info(
            "step3_seeds_expanded",
            seed_count=len(seeds),
            elapsed=f"{time.monotonic() - t3:.2f}s",
        )

        # Step 4: Generate agent profiles (LLM — content filter 가능)
        if state.profiles is None:
            notify("step4_profiles")
            t4 = time.monotonic()
            from litemiro.phase1.profile_generator import ProfileGenerator  # noqa: PLC0415

            profile_generator = ProfileGenerator(llm=self._llm, model=cfg.model)
            state.profiles = await profile_generator.generate(seeds, cfg.requirement)
            state.profile_fallback_count = profile_generator.fallback_count
            log.info(
                "step4_profiles_generated",
                profile_count=len(state.profiles),
                fallback_count=state.profile_fallback_count,
                elapsed=f"{time.monotonic() - t4:.2f}s",
            )
        profiles: list[AgentProfile] = state.profiles
        assert profiles is not None
        self.profile_fallback_count = state.profile_fallback_count
        agents: dict[str, AgentProfile] = {p.agent_id: p for p in profiles}

        # Step 5: Initialize memory stores
        notify("step5_memory")
        t5 = time.monotonic()
        from litemiro.phase1.memory_initializer import MemoryInitializer  # noqa: PLC0415

        stores: dict[str, MemoryStore] = MemoryInitializer(graph=graph, seed=cfg.seed).initialize(
            agents
        )
        log.info(
            "step5_memory_initialized",
            store_count=len(stores),
            elapsed=f"{time.monotonic() - t5:.2f}s",
        )

        # Step 6: Build output models, validate, serialize
        notify("step6_serialize")
        t6 = time.monotonic()
        ontology_a = OntologyA(
            seed=cfg.seed,
            agent_count=len(agents),
            preset=cfg.preset,
            source_document=str(cfg.input_path),
            simulation_requirement=cfg.requirement,
            generated_at=datetime.now(tz=timezone.utc),  # noqa: UP017
            ontology=ontology,
            agents=agents,
        )
        ontology_b = OntologyB(
            config=MemoryConfig(),
            stores=stores,
        )

        validator = OntologyValidator()
        result = validator.validate(ontology_a, ontology_b)
        if result.warnings:
            for w in result.warnings:
                log.warning("validation_warning", message=w)
        if result.errors:
            for e in result.errors:
                log.error("validation_error", message=e)
            raise ValueError("ontology validation failed: " + "; ".join(result.errors))

        serializer = OntologySerializer()
        serializer.write(ontology_a, ontology_b, cfg.output_dir)

        log.info(
            "step6_complete",
            valid=result.valid,
            elapsed=f"{time.monotonic() - t6:.2f}s",
            total_elapsed=f"{time.monotonic() - t0:.2f}s",
        )

        return ontology_a, ontology_b

    def _read_document(self) -> str:
        path = self._config.input_path
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            from PyPDF2 import PdfReader  # noqa: PLC0415

            reader = PdfReader(str(path))
            pages = [page.extract_text() or "" for page in reader.pages]
            return "\n".join(pages)
        return path.read_text(encoding="utf-8")


__all__ = ["OntologyPipeline", "PipelineConfig"]
