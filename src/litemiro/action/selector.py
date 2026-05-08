"""``ActionSelector`` — owned by **B**.

The Phase 2 design doc fixes the recipe ("persona + memory + feed +
recent action → LLM → Action") but leaves robustness up to the owner.
B pins this contract in ``tests/unit/test_action_selector.py``:

* ``select_action`` *never* raises and always returns an
  :class:`ActionResult`. A flaky LLM cannot derail the round; on any
  failure path the call collapses to ``Action(type=DO_NOTHING)`` while
  ``llm_meta.fallback_used`` flips to ``True`` so the round runner can
  count fallbacks without reading the action payload.
* **3-step fallback** in this order:
    1. tenacity retry on transport errors raised by ``LLMClient``
       (``max_attempts`` defaults to 3).
    2. ``json_repair`` rescues malformed JSON before validation.
    3. ``DO_NOTHING`` if (a) retries exhaust, (b) JSON cannot be
       repaired, (c) the response fails ``Action`` validation, or
       (d) target validation rejects an LLM-hallucinated id.
* **Target visibility**: ``target_post_id`` must reference a post in
  ``context.feed``; for ``FOLLOW``, ``target_agent_id`` must be a feed
  author. Self-likes / self-follows also collapse to ``DO_NOTHING``.

The :class:`LLMMeta` attached to every result records the model name,
the prompt+completion token total, the wall-clock latency in
milliseconds, and whether the safety net was used. Token usage is
sourced from :class:`LLMResponse`; adapters that cannot get usage from
their backend leave the counts at zero, which is preserved here.

Prompt composition lives in ``litemiro.prompts.action_selector``; this
module is responsible only for the LLM call and its safety net.
"""

from __future__ import annotations

import json
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
from litemiro.prompts.action_selector import compose_system, compose_user

if TYPE_CHECKING:
    from litemiro.interfaces import LLMClient


_DO_NOTHING: Action = Action(type=ActionType.DO_NOTHING)


class ActionSelector:
    def __init__(self, *, llm: LLMClient, model: str, max_attempts: int = 3) -> None:
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
        self._llm = llm
        self._model = model
        self._max_attempts = max_attempts

    async def select_action(self, agent_id: str, context: ActionContext) -> ActionResult:
        system = compose_system(agent_id, context)
        user = compose_user(context)

        started = perf_counter()
        try:
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

        return self._build_result(action, response=response, started=started, fallback=False)

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
    interact with content it has actually seen, and never with itself.
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
        return target_agent in {p.author_id for p in context.feed}
    return True


__all__ = ["ActionSelector"]
