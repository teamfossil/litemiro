"""``BeliefUpdater`` — 라운드 종료 후 ideology 변동 적용.

Deffuant bounded-confidence 모델 (#165):
- FOLLOW: mu_follow (기본 0.05) 만큼 수렴
- LIKE/QUOTE/REPOST: 영향력 가중치 적용 (0.33 / 0.5 / 0.5)
- confidence bound ε 이내인 쌍만 수렴 (기본 0.3)

행위자(actor) 일방 업데이트 — 대상(target) ideology 는 변하지 않음.
같은 라운드 events 는 이전 라운드의 ideology 기준으로 계산(배치 적용).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import IO, TYPE_CHECKING

from litemiro.models import ActionType, RoundEvent

if TYPE_CHECKING:
    from litemiro.interfaces import StateStoreLike

_DEFAULT_EPSILON: float = 0.3
_DEFAULT_MU_FOLLOW: float = 0.05

_INFLUENCE_WEIGHT: dict[ActionType, float] = {
    ActionType.FOLLOW: 1.0,
    ActionType.LIKE_POST: 1 / 3,
    ActionType.QUOTE_POST: 0.5,
    ActionType.REPOST: 0.5,
}


class BeliefUpdater:
    """Deffuant bounded-confidence belief update.

    ``trajectory_path`` 가 주어지면 라운드마다 모든 에이전트의 ideology
    스냅샷을 ``belief_trajectory.jsonl`` 에 기록한다.
    """

    def __init__(
        self,
        *,
        epsilon: float = _DEFAULT_EPSILON,
        mu_follow: float = _DEFAULT_MU_FOLLOW,
        trajectory_path: Path | None = None,
    ) -> None:
        self._epsilon = epsilon
        self._mu_follow = mu_follow
        self._trajectory_path = trajectory_path
        self._handle: IO[str] | None = None
        if trajectory_path is not None:
            trajectory_path.parent.mkdir(parents=True, exist_ok=True)
            is_new = not trajectory_path.exists() or trajectory_path.stat().st_size == 0
            self._handle = open(trajectory_path, "a", encoding="utf-8", newline="")  # noqa: SIM115
            if is_new:
                self._handle.write(json.dumps({"schema": "belief_trajectory/v1"}) + "\n")
                self._handle.flush()

    def apply_round(
        self,
        events: list[RoundEvent],
        store: StateStoreLike,
        round_num: int,
    ) -> dict[str, float]:
        """이 라운드 events 기반 ideology 업데이트 + 선택적 스냅샷 기록.

        반환값: {agent_id: new_ideology} — 실제로 변동이 발생한 에이전트만.
        """
        # 배치 적용: 같은 라운드 내 이전 events 의 결과가 이후 events 에
        # 영향을 주지 않도록 이전 라운드 ideology 를 기준으로 모두 계산한 뒤
        # 한꺼번에 반영 (Deffuant 원안과 동일).
        pre_ideology: dict[str, float] = {
            aid: store.get_agent(aid).ideology for aid in store.list_agent_ids()
        }

        # 같은 라운드 내 여러 events 의 시프트를 합산한 뒤 일괄 반영.
        # ideology_a 는 항상 pre_ideology 기준 (배치 적용 원칙).
        shifts: dict[str, float] = {}

        for event in events:
            weight = _INFLUENCE_WEIGHT.get(event.action.type)
            if weight is None:
                continue

            actor_id = event.agent_id
            ideology_a = pre_ideology.get(actor_id, 0.5)

            ideology_b: float | None = None
            if event.action.type == ActionType.FOLLOW:
                target_id = event.action.target_agent_id
                if target_id is None:
                    continue
                ideology_b = pre_ideology.get(target_id)
            else:
                target_post_id = event.action.target_post_id
                if target_post_id is None:
                    continue
                try:
                    post = store.get_post(target_post_id)
                except KeyError:
                    continue
                ideology_b = pre_ideology.get(post.author_id)

            if ideology_b is None:
                continue

            delta = ideology_b - ideology_a
            if abs(delta) >= self._epsilon:
                continue

            shifts[actor_id] = shifts.get(actor_id, 0.0) + self._mu_follow * weight * delta

        pending: dict[str, float] = {}
        for agent_id, total_shift in shifts.items():
            new_ideology = max(0.0, min(1.0, pre_ideology[agent_id] + total_shift))
            pending[agent_id] = new_ideology
            store.update_agent_ideology(agent_id, new_ideology)

        if self._handle is not None:
            snapshot = {aid: pending.get(aid, pre_ideology[aid]) for aid in sorted(pre_ideology)}
            line = json.dumps(
                {"round_num": round_num, "ideology": snapshot},
                ensure_ascii=False,
                sort_keys=True,
            )
            self._handle.write(line + "\n")
            self._handle.flush()

        return pending

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


__all__ = ["BeliefUpdater"]
