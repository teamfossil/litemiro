"""``ActionSelector`` — owned by **B**.

The Phase 2 design doc fixes the recipe ("persona + memory + feed +
recent action → LLM → Action") but leaves robustness up to the owner.
B pins this contract in ``tests/unit/test_action_selector.py``:

* ``select_action`` *never* raises and always returns an
  :class:`ActionResult`. A flaky LLM cannot derail the round; on any
  failure path — including prompt composition raising — the call
  collapses to ``Action(type=DO_NOTHING)`` while
  ``llm_meta.fallback_used`` flips to ``True`` so the round runner can
  count fallbacks without reading the action payload.
* **3-step fallback** in this order:
    1. tenacity retry on **any** exception raised by ``LLMClient``
       (``max_attempts`` defaults to 3). The retry scope is broad on
       purpose — adapters wrap their transport errors in opaque types
       (``litellm.APIConnectionError`` etc.) and we don't want a typo
       in the exception filter to silently break the safety net.
    2. ``json_repair`` rescues malformed JSON before validation.
    3. ``DO_NOTHING`` if (a) prompt composition raised, (b) retries
       exhaust, (c) JSON cannot be repaired, (d) the response fails
       ``Action`` validation, or (e) target validation rejects an
       LLM-hallucinated id.
* **Target visibility**: ``target_post_id`` must reference a post in
  ``context.feed``; for ``FOLLOW``, ``target_agent_id`` must be a feed
  author. Self-likes / self-follows also collapse to ``DO_NOTHING``.

The :class:`LLMMeta` attached to every result records the model name,
the prompt+completion token total, the wall-clock latency in
milliseconds, and whether the safety net was used. Token usage is
sourced from :class:`LLMResponse`; adapters that cannot get usage from
their backend leave the counts at zero, which is preserved here.

Prompt composition lives in ``litemiro.prompts.action_selector``; this
module owns the LLM call, its safety net, and (when a ``global_seed`` is
supplied) the behavior-tendency gate that samples the action family from
post_rate / follow_rate / reply_rate before the prompt is composed.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Mapping
from time import perf_counter
from typing import TYPE_CHECKING

from json_repair import repair_json
from pydantic import ValidationError
from tenacity import AsyncRetrying, stop_after_attempt, wait_none

from litemiro.models import (
    Action,
    ActionContext,
    ActionResult,
    ActionType,
    LLMMeta,
    LLMResponse,
)
from litemiro.prompts.action_selector import _as_rate, compose_system, compose_user

if TYPE_CHECKING:
    from litemiro.interfaces import LLMClient


_DO_NOTHING: Action = Action(type=ActionType.DO_NOTHING)

# 게이트 fallback — ``prompts.action_selector`` 의 reaction fallback 과 함께
# ``phase1.models.BehaviorTendency`` 디폴트에 동기 유지. 게이트는 family 가중치
# (CREATE=post_rate, FOLLOW=follow_rate, REACTION=reply_rate) 를 정규화해 샘플한다.
_POST_RATE_FALLBACK = 0.5
_REPLY_RATE_FALLBACK = 0.3
_FOLLOW_RATE_FALLBACK = 0.2


class ActionSelector:
    def __init__(
        self,
        *,
        llm: LLMClient,
        model: str,
        max_attempts: int = 3,
        global_seed: int | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
        self._llm = llm
        self._model = model
        self._max_attempts = max_attempts
        # None → behavior gate off (pre-gate free choice, full schema). The
        # simulation wiring passes ``ontology_a.seed``; unit tests omit it so
        # the prompt/selection contract is exercised without the gate.
        self._global_seed = global_seed

    async def select_action(self, agent_id: str, context: ActionContext) -> ActionResult:
        started = perf_counter()
        forced_family, react_only = self._gate(agent_id, context)
        try:
            system = compose_system(
                agent_id, context, forced_family=forced_family, react_only=react_only
            )
            user = compose_user(context)
            response = await self._call_with_retry(system, user)
        except Exception:
            return self._build_result(_DO_NOTHING, response=None, started=started, fallback=True)

        parsed = _parse_json(response.content)
        if parsed is None:
            return self._build_result(
                _DO_NOTHING, response=response, started=started, fallback=True
            )

        try:
            action = Action.model_validate(parsed)
        except ValidationError:
            return self._build_result(
                _DO_NOTHING, response=response, started=started, fallback=True
            )

        if not _target_is_valid(action, agent_id=agent_id, context=context):
            return self._build_result(
                _DO_NOTHING, response=response, started=started, fallback=True
            )

        # 게이트가 좁힌 허용 집합을 LLM 이 어기면 fallback 으로 떨어뜨린다 — 비율은
        # 약간 새지만 fallback_used 로 관측되고, allowed 를 단일/축소 집합으로 좁힌
        # 만큼 실제 위반은 드물다. 게이트 off (global_seed 없음) 면 항상 통과.
        if not _gate_allows(action.type, forced_family=forced_family, react_only=react_only):
            return self._build_result(
                _DO_NOTHING, response=response, started=started, fallback=True
            )

        return self._build_result(action, response=response, started=started, fallback=False)

    def _gate(self, agent_id: str, context: ActionContext) -> tuple[ActionType | None, bool]:
        """Family gate — samples the action family from behavior_tendency.

        The three families compete on their tendency weights (no sequential
        priority, so ``reply_rate`` is not crowded out by the originate axis):

        * CREATE_POST weight ``post_rate``
        * FOLLOW weight ``follow_rate`` (only when a not-yet-followed non-self
          feed author exists — already-followed authors are not followable again)
        * REACTION weight ``reply_rate`` (LIKE / REPOST / QUOTE / DO_NOTHING)

        Returns ``(forced_family, react_only)``:

        * ``(CREATE_POST, False)`` — cold-start (empty feed) or a CREATE draw.
        * ``(FOLLOW, False)`` — a FOLLOW draw.
        * ``(None, True)`` — reaction branch.
        * ``(None, False)`` — gate off: no ``global_seed`` or no
          ``behavior_tendency`` block → pre-gate free choice, full schema.

        Deterministic in ``(global_seed, agent_id, round_num)`` so a re-run with
        the same seed reproduces every gate decision (mirrors AgentScheduler).
        """
        if self._global_seed is None:
            return None, False
        bt = context.agent.persona_traits.get("behavior_tendency")
        if not isinstance(bt, Mapping):
            return None, False
        if not context.feed:
            return ActionType.CREATE_POST, False  # cold-start: nothing to react to
        weighted: list[tuple[ActionType | None, float]] = [
            (ActionType.CREATE_POST, _as_rate(bt.get("post_rate"), _POST_RATE_FALLBACK)),
            (None, _as_rate(bt.get("reply_rate"), _REPLY_RATE_FALLBACK)),
        ]
        following = context.following_ids
        if any(p.author_id != agent_id and p.author_id not in following for p in context.feed):
            weighted.append(
                (ActionType.FOLLOW, _as_rate(bt.get("follow_rate"), _FOLLOW_RATE_FALLBACK))
            )
        total = sum(weight for _, weight in weighted)
        if total <= 0.0:
            return None, True  # 모든 성향 0 → reaction 분기 (feed 있어도 사실상 DO_NOTHING)
        rng = random.Random(self._derive_seed(agent_id, context.round_num))
        threshold = rng.random() * total
        cumulative = 0.0
        for family, weight in weighted:
            cumulative += weight
            if threshold < cumulative:
                return (None, True) if family is None else (family, False)
        return None, True  # 부동소수 경계 안전망

    def _derive_seed(self, agent_id: str, round_num: int) -> int:
        digest = hashlib.sha256(f"{self._global_seed}:{agent_id}:{round_num}".encode()).digest()
        return int.from_bytes(digest[:8], "big", signed=False)

    async def _call_with_retry(self, system: str, user: str) -> LLMResponse:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_none(),
            reraise=True,
        ):
            with attempt:
                return await self._llm.complete(system=system, user=user, model=self._model)
        # AsyncRetrying with reraise=True either returns from inside the
        # ``with`` block or propagates the last exception, so this is
        # only reachable if max_attempts < 1 (rejected at construction).
        raise RuntimeError("AsyncRetrying terminated without success or reraise")

    def _build_result(
        self,
        action: Action,
        *,
        response: LLMResponse | None,
        started: float,
        fallback: bool,
    ) -> ActionResult:
        tokens = (response.prompt_tokens + response.completion_tokens) if response else 0
        return ActionResult(
            action=action,
            llm_meta=LLMMeta(
                model=self._model,
                tokens_used=tokens,
                latency_ms=(perf_counter() - started) * 1000.0,
                fallback_used=fallback,
            ),
        )


def _parse_json(raw: str) -> dict[str, object] | None:
    """Two-stage JSON parse — strict ``json.loads`` first, then ``json_repair``.

    Returns ``None`` if both stages fail or the result is not an object;
    callers translate that into a ``DO_NOTHING`` fallback.
    """
    try:
        candidate = json.loads(raw)
    except json.JSONDecodeError:
        try:
            repaired = repair_json(raw)
        except Exception:
            return None
        if not repaired:
            return None
        try:
            candidate = json.loads(repaired)
        except json.JSONDecodeError:
            return None
    return candidate if isinstance(candidate, dict) else None


_POST_TARGETED = frozenset({ActionType.LIKE_POST, ActionType.REPOST, ActionType.QUOTE_POST})


def _target_is_valid(action: Action, *, agent_id: str, context: ActionContext) -> bool:
    """Reject LLM hallucinations against the agent's actual visibility.

    Visibility = the post-ids and author-ids in ``context.feed``. The
    rules mirror Notion's social-mechanics intent: an agent can only
    interact with content it has actually seen, never with itself, and
    never re-follows an author it already follows.
    """
    if action.type in _POST_TARGETED:
        target = action.target_post_id
        if target is None:
            return False
        visible = {p.post_id for p in context.feed if p.author_id != agent_id}
        return target in visible
    if action.type is ActionType.FOLLOW:
        target_agent = action.target_agent_id
        if target_agent is None or target_agent == agent_id:
            return False
        if target_agent in context.following_ids:
            return False  # 이미 follow 중 — 중복 FOLLOW 는 신규 엣지가 아니므로 거른다
        return target_agent in {p.author_id for p in context.feed}
    return True


def _gate_allows(
    action_type: ActionType, *, forced_family: ActionType | None, react_only: bool
) -> bool:
    """Whether ``action_type`` is permitted under the gate decision.

    ``forced_family`` → exactly that type; ``react_only`` → anything except the
    two originate types (they only surface through their probability gates);
    gate off (both defaults) → everything.
    """
    if forced_family is not None:
        return action_type is forced_family
    if react_only:
        return action_type not in (ActionType.CREATE_POST, ActionType.FOLLOW)
    return True


__all__ = ["ActionSelector"]
