"""``RoundManager`` — Phase 2 라운드 루프 오케스트레이터.

PHASE-2-A.md §5.4 명세 그대로:

* AgentScheduler → ConcurrencyController → ActionSelector → apply_action
  → EventLogger → TokenBudget.consume → save_checkpoint 의 6 단계 직렬 흐름.
* 합의 #3 옵션 A (라운드 매니저 책임 + 라운드 내 직렬 적용) 으로 race 자체를
  코드 구조로 차단 — ``asyncio.Lock`` 불필요.
* TokenBudget 신호로 라운드 *시작 전* 조기 종료 (PRD §3.10). 이미 시작된
  라운드는 끝까지 진행한 뒤 다음 라운드부터 차단.
* ``recent_actions`` 는 agent_id 별 ``deque(maxlen=_RECENT_LIMIT)`` 로 보관 —
  Agent 모델 무수정 유지 (PHASE-2-A.md 결정 #1).
* ``apply_action`` 은 RoundManager 메서드로 남긴다 — 100 줄 임계 도달 전까지
  분리하지 않음 (PHASE-2-A.md 결정 #2).
"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from litemiro.core._types import RoundOutcome
from litemiro.core.context_builder import build_context
from litemiro.models import (
    Action,
    ActionType,
    Post,
    RoundEvent,
)

if TYPE_CHECKING:
    from litemiro.core.agent_scheduler import AgentScheduler
    from litemiro.core.concurrency_controller import ConcurrencyController
    from litemiro.interfaces import (
        ActionSelectorLike,
        EventLoggerLike,
        FeedEngineLike,
        SocialGraphLike,
        StateStoreLike,
        TokenBudgetManagerLike,
        TopicExtractorLike,
    )
    from litemiro.models import ActionResult

_RECENT_LIMIT = 5
_TOKENS_PER_CALL_ESTIMATE = 1000  # PRD §6.1 추정. token_budget.has_budget 게이트용.


class RoundManager:
    def __init__(
        self,
        *,
        store: StateStoreLike,
        scheduler: AgentScheduler,
        concurrency: ConcurrencyController,
        action_selector: ActionSelectorLike,
        feed: FeedEngineLike,
        social: SocialGraphLike,
        event_logger: EventLoggerLike,
        token_budget: TokenBudgetManagerLike,
        topic_extractor: TopicExtractorLike,
        llm_model: str,
    ) -> None:
        self._store = store
        self._scheduler = scheduler
        self._concurrency = concurrency
        self._action_selector = action_selector
        self._feed = feed
        self._social = social
        self._event_logger = event_logger
        self._token_budget = token_budget
        self._topic_extractor = topic_extractor
        self._llm_model = llm_model
        self._recent_actions: dict[str, deque[Action]] = {}

    async def run_round(self, round_num: int) -> RoundOutcome:
        """한 라운드 실행. PHASE-2-A.md §5.4 의 6 단계 흐름.

        TokenBudget 부족 시 액션 실행 없이 ``RoundOutcome(early_exit=True)``
        반환. 체크포인트 저장 실패는 호출자에게 그대로 전파 (시뮬레이션 중단).
        """
        agents = tuple(self._store.get_agent(aid) for aid in self._store.list_agent_ids())
        if not agents:
            return RoundOutcome(processed=0, early_exit=False)

        active_ids = self._scheduler.select_active(agents, round_num=round_num)
        estimated = len(active_ids) * _TOKENS_PER_CALL_ESTIMATE
        if not self._token_budget.has_budget(estimated_tokens=estimated):
            return RoundOutcome(processed=0, early_exit=True)

        if active_ids:
            agents_by_id = {a.agent_id: a for a in agents}

            async def _task(agent_id: str) -> tuple[str, ActionResult]:
                ctx = build_context(
                    agent=agents_by_id[agent_id],
                    feed=self._feed,
                    social=self._social,
                    recent_actions=self._recent_actions.get(agent_id, ()),
                    round_num=round_num,
                )
                return agent_id, await self._action_selector.select_action(agent_id, ctx)

            results = await self._concurrency.run_batched(active_ids, _task)
            for agent_id, result in results:
                event = self._apply_action(
                    agent_id=agent_id,
                    action=result.action,
                    round_num=round_num,
                )
                event = event.model_copy(update={"llm_meta": result.llm_meta})
                await self._event_logger.log_event(event)
                self._token_budget.consume(tokens_used=result.llm_meta.tokens_used)

        await self._store.save_checkpoint(round_num)
        return RoundOutcome(processed=len(active_ids), early_exit=False)

    async def run_simulation(self, total_rounds: int) -> None:
        """라운드 순서 1 → 2 → … 보장. 조기 종료 시 즉시 break."""
        if total_rounds < 0:
            raise ValueError(f"total_rounds must be >= 0, got {total_rounds}")
        for round_num in range(total_rounds):
            outcome = await self.run_round(round_num)
            if outcome.early_exit:
                return

    # ── action application ─────────────────────────────────────────────

    def _apply_action(
        self,
        *,
        agent_id: str,
        action: Action,
        round_num: int,
    ) -> RoundEvent:
        """6 종 분기. 부수효과는 store / feed / social 에 적용 후 ``RoundEvent``
        를 *llm_meta 없이* 반환한다. ``llm_meta`` 는 호출자가 model_copy 로 첨부.
        """
        if action.type is ActionType.CREATE_POST:
            self._create_post(agent_id=agent_id, action=action, round_num=round_num)
        elif action.type is ActionType.LIKE_POST:
            self._like_post(action=action)
        elif action.type is ActionType.REPOST:
            self._repost(agent_id=agent_id, action=action, round_num=round_num)
        elif action.type is ActionType.QUOTE_POST:
            self._quote_post(agent_id=agent_id, action=action, round_num=round_num)
        elif action.type is ActionType.FOLLOW:
            self._follow(agent_id=agent_id, action=action)
        # DO_NOTHING → no-op
        self._record_recent(agent_id=agent_id, action=action)
        return RoundEvent(
            round_num=round_num,
            timestamp=datetime.now(UTC),
            agent_id=agent_id,
            action=action,
        )

    def _create_post(self, *, agent_id: str, action: Action, round_num: int) -> None:
        content = action.content or ""
        post = Post(
            post_id=_post_id_for(agent_id, round_num),
            author_id=agent_id,
            content=content,
            topics=self._topic_extractor.extract(content),
            created_round=round_num,
        )
        self._store.add_post(post)
        self._feed.index_post(post)

    def _like_post(self, *, action: Action) -> None:
        target_id = action.target_post_id
        assert target_id is not None  # Action 검증으로 보장됨
        target = self._store.get_post(target_id)
        updated = target.model_copy(update={"likes": target.likes + 1})
        self._store.replace_post(updated)
        self._feed.update_engagement(updated)

    def _repost(self, *, agent_id: str, action: Action, round_num: int) -> None:
        target_id = action.target_post_id
        assert target_id is not None
        target = self._store.get_post(target_id)
        updated = target.model_copy(update={"reposts": target.reposts + 1})
        self._store.replace_post(updated)
        self._feed.update_engagement(updated)
        # 합의 #3 옵션 A: 새 Post 도 등장 (reposted_from 으로 링크). content 는
        # 빈 문자열로 둔다 — REPOST 는 원문 보강 없이 전파.
        new_post = Post(
            post_id=_post_id_for(agent_id, round_num),
            author_id=agent_id,
            content="",
            topics=target.topics,
            created_round=round_num,
            reposted_from=target_id,
        )
        self._store.add_post(new_post)
        self._feed.index_post(new_post)

    def _quote_post(self, *, agent_id: str, action: Action, round_num: int) -> None:
        target_id = action.target_post_id
        assert target_id is not None
        target = self._store.get_post(target_id)
        updated = target.model_copy(update={"quotes": target.quotes + 1})
        self._store.replace_post(updated)
        self._feed.update_engagement(updated)
        content = action.content or ""
        new_post = Post(
            post_id=_post_id_for(agent_id, round_num),
            author_id=agent_id,
            content=content,
            topics=self._topic_extractor.extract(content),
            created_round=round_num,
            quoted_post_id=target_id,
        )
        self._store.add_post(new_post)
        self._feed.index_post(new_post)

    def _follow(self, *, agent_id: str, action: Action) -> None:
        target_agent_id = action.target_agent_id
        assert target_agent_id is not None
        self._social.follow(agent_id, target_agent_id)

    def _record_recent(self, *, agent_id: str, action: Action) -> None:
        bucket = self._recent_actions.get(agent_id)
        if bucket is None:
            bucket = deque(maxlen=_RECENT_LIMIT)
            self._recent_actions[agent_id] = bucket
        bucket.append(action)


def _post_id_for(agent_id: str, round_num: int) -> str:
    """결정성 보장 post_id. 한 에이전트는 한 라운드에 최대 한 액션이므로
    ``{agent_id}_r{round_num:04d}`` 로 충돌 없이 유일. 비결정성 (시계 / 카운터)
    을 piping 하지 않아 같은 시뮬레이션 재실행 시 동일 ID 가 나온다."""
    return f"{agent_id}_r{round_num:04d}"


__all__ = ["RoundManager"]
