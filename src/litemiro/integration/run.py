"""``run_simulation`` — Phase 1 산출 → Phase 2 시뮬레이션 → Phase 3 입력 (JSONL).

``OntologyLoader`` 가 만들어주는 검증된 ``OntologyA`` / ``OntologyB`` 위에
``StateStore`` + ``AgentScheduler`` + ``ConcurrencyController`` + ``FeedEngine``
+ ``ActionSelector`` + ``TopicExtractor`` + ``EventLogger`` + ``TokenBudgetManager``
를 결선해 ``RoundManager`` 에 넘긴다. CLI 진입점 (``#53``) 은 본 함수 위의
얇은 argparse wrapper.

LLM / Embedder / 토픽 vocab 등 무거운 외부 의존은 본 함수의 인자로 주입한다
— 테스트는 fake 로 닫고 (``tests/e2e/test_run_simulation_smoke.py``), 실 실행은
CLI 에서 sentence-transformers + LiteLLM 인스턴스를 만들어 넘긴다. issue #52
본문의 ``Preset`` / ``seed`` 인자는 제거 — ``OntologyA.seed`` / Phase 1 의
preset 이 이미 진실 공급원이라 중복이다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from litemiro.action.selector import ActionSelector
from litemiro.budget.manager import TokenBudgetManager
from litemiro.core._types import SimulationResult
from litemiro.core.agent_scheduler import AgentScheduler
from litemiro.core.concurrency_controller import ConcurrencyController
from litemiro.core.round_manager import RoundManager
from litemiro.core.state_store import StateStore
from litemiro.eventlog.logger import EventLogger
from litemiro.feed.engine import FeedEngine
from litemiro.integration.ontology_loader import OntologyLoader
from litemiro.social.graph import SocialGraph
from litemiro.topics.extractor import TopicExtractor

log = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from litemiro.interfaces import EmbedderLike, LLMClient
    from litemiro.phase1.models import OntologyA


def derive_topic_vocabulary(ontology_a: OntologyA) -> tuple[str, ...]:
    """OntologyA 의 모든 agent topics 의 union, 정렬.

    ``run_simulation`` 의 ``topic_vocabulary`` 인자가 ``None`` 일 때 기본값
    으로 사용된다. CLI 는 인자를 안 넘기면 본 함수가 호출되므로 ``OntologyLoader.
    load`` 가 한 번만 일어난다. ``topic_hierarchy`` 는 contract Section 1 의
    post-MVP 항목이라 미사용.
    """
    vocab: set[str] = set()
    for profile in ontology_a.agents.values():
        vocab.update(profile.topics)
    return tuple(sorted(vocab))


async def run_simulation(
    *,
    ontology_a_path: Path,
    ontology_b_path: Path,
    llm_client: LLMClient,
    embedder: EmbedderLike,
    rounds: int,
    event_log_path: Path,
    checkpoint_dir: Path,
    topic_vocabulary: Sequence[str] | None = None,
    llm_model: str = "openrouter/qwen/qwen-plus",
    token_budget: int = 3_000_000,
    semaphore_limit: int = 10,
    batch_size: int = 20,
    cooldown_seconds: float = 0.5,
) -> SimulationResult:
    """결정성 보장: 동일 입력 + 동일 seed → 동일 JSONL + 체크포인트.

    ``topic_vocabulary`` 가 ``None`` 이면 ``derive_topic_vocabulary`` 가 자동
    도출 — CLI 가 별도로 ``OntologyLoader.load`` 를 한 번 더 부르지 않도록 함.

    실패 시 부분 산출물 (이미 기록된 JSONL 라인 / 체크포인트) 은 그대로 유지
    — EventLogger 의 line-level flush 가 partial-but-valid 를 보장하므로 Phase 3
    가 부분 데이터로도 정상 파싱 가능. RoundManager 의 ``run_simulation`` 이
    체크포인트 저장 실패를 그대로 raise 하면 본 함수도 같은 예외를 흘려보낸다
    (데이터 손실 방지).
    """
    if rounds < 0:
        raise ValueError(f"rounds must be >= 0, got {rounds}")

    ontology_a, ontology_b = OntologyLoader.load(
        ontology_a_path=ontology_a_path,
        ontology_b_path=ontology_b_path,
    )
    # Section 6.5 persona-memory topic overlap. MVP 는 warning-only 라 진행은
    # 막지 않지만 어디서도 호출이 안 되면 (#21 task 2) 누적 비율 측정이 사장
    # 되므로 RunBootstrap 단에서 한 번 부르고 structlog 로 흘린다.
    # #58 옵션 B: 임베딩 cosine 으로 어휘공간 분리 (페르소나 LLM 추상 개념 vs
    # NER 엔티티명) 를 우회. 이미 FeedEngine 용으로 인스턴스화된 ``embedder``
    # 를 재사용해 모델 로딩을 한 번에 묶는다.
    consistency_warnings = OntologyLoader.validate_consistency(
        ontology_a=ontology_a, ontology_b=ontology_b, embedder=embedder
    )
    if consistency_warnings:
        log.warning(
            "run_simulation.persona_memory_overlap_warnings",
            count=len(consistency_warnings),
            agent_ids=tuple(w.agent_id for w in consistency_warnings),
        )
    if topic_vocabulary is None:
        topic_vocabulary = derive_topic_vocabulary(ontology_a)
    agents = OntologyLoader.build_agents(ontology_a=ontology_a, ontology_b=ontology_b)
    social = OntologyLoader.build_social_graph(ontology_a=ontology_a)
    store = StateStore(
        agents=agents,
        social=social,
        social_factory=SocialGraph.from_dict,
        checkpoint_dir=checkpoint_dir,
        global_seed=ontology_a.seed,
    )

    scheduler = AgentScheduler(global_seed=ontology_a.seed)
    concurrency = ConcurrencyController(
        semaphore_limit=semaphore_limit,
        batch_size=batch_size,
        cooldown_seconds=cooldown_seconds,
    )
    feed = FeedEngine(social=social, embedder=embedder)
    action_selector = ActionSelector(llm=llm_client, model=llm_model)
    topic_extractor = TopicExtractor(embedder=embedder, vocabulary=topic_vocabulary)
    budget = TokenBudgetManager(total_budget=token_budget)
    logger = EventLogger(event_log_path)

    manager = RoundManager(
        store=store,
        scheduler=scheduler,
        concurrency=concurrency,
        action_selector=action_selector,
        feed=feed,
        social=social,
        event_logger=logger,
        token_budget=budget,
        topic_extractor=topic_extractor,
        llm_model=llm_model,
    )

    rounds_run = 0
    early_exit = False
    try:
        for r in range(rounds):
            outcome = await manager.run_round(round_num=r)
            if outcome.early_exit:
                early_exit = True
                break
            rounds_run += 1
    finally:
        await logger.aclose()

    return SimulationResult(
        rounds_run=rounds_run,
        early_exit=early_exit,
        event_log_path=event_log_path,
        checkpoint_dir=checkpoint_dir,
        tokens_used=token_budget - budget.remaining(),
    )


__all__ = ["derive_topic_vocabulary", "run_simulation"]
