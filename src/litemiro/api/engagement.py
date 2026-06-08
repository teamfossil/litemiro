"""events.jsonl engagement 집계 — ``/layout`` 과 positions SSE 가 공유.

``plazas.py`` (부감 뷰 ``/layout``) 와 ``store.py`` (라운드별 positions SSE) 가
같은 가중치/도출 규칙으로 받은 호응(``influence_scores``)·발화량
(``activity_counts``) 을 계산해야 해서 한 군데로 모았다. 두 호출자가 같은
events.jsonl 을 같은 방식으로 1-pass 집계한다.

``_read_engagement`` 는 ``max_round`` 로 라운드 누적값을 끊어 볼 수 있다 —
positions SSE 가 "라운드 N 시점까지의 누적 받은 호응/발화량" 을 라운드마다
계산하는 데 쓴다 (None 이면 전체, ``/layout`` 의 기존 동작).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from litemiro.models import ActionType, RoundEvent

# engagement → influence 가중치 (#132 B). LIKE 가벼운 동의 = 1, REPOST 가
# follower 망 전파 = 2, QUOTE 본인 의견 추가 = 3, FOLLOW 영구 구독 = 5. raw
# in-degree (FOLLOW only) 정규화는 sim 의 follower=0 long-tail 에서 노드 크기
# 차별이 0 으로 떨어졌다 (이슈 본문의 measurement).
_INFLUENCE_WEIGHTS: dict[ActionType, int] = {
    ActionType.LIKE_POST: 1,
    ActionType.REPOST: 2,
    ActionType.QUOTE_POST: 3,
    ActionType.FOLLOW: 5,
}


def _author_from_post_id(post_id: str) -> str | None:
    """``{agent_id}_r{round:04d}`` 에서 author 추출.

    Phase 2 ``core.round_manager.derive_post_id`` 가 만드는 결정적 포맷에
    의존. 외부 주입 / 구버전 events.jsonl 처럼 포맷이 깨진 라인은 ``None``
    을 돌려 호출자가 그 한 줄을 카운팅에서 빼게 한다.
    """
    head, sep, _ = post_id.rpartition("_r")
    return head if sep and head else None


def _read_engagement(
    path: Path,
    *,
    max_round: int | None = None,
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    """events.jsonl 1-pass — follower_counts + influence_scores + activity_counts.

    반환 3-tuple:
    - ``follower_counts`` — FOLLOW 받은 raw 카운트, 응답의 ``follower_count`` 표시용.
    - ``influence_scores`` — ``_INFLUENCE_WEIGHTS`` 가중합. LIKE/REPOST/QUOTE 의 author
      는 ``target_post_id`` 의 결정적 포맷에서 ``_author_from_post_id`` 로 도출.
    - ``activity_counts`` — agent 가 라운드 동안 발동한 액션 수 (DO_NOTHING 제외).
      ``/layout`` 의 ``y`` 축 (#133) — "광장에서 얼마나 적극적으로 발화 중인가".

    ``max_round`` 가 주어지면 ``round_num <= max_round`` 인 라인만 집계한다 —
    positions SSE 가 라운드 N 시점까지의 누적값을 뽑는 용도. None 이면 전체
    (``/layout`` 의 기존 동작 — 시그니처/결과 불변).

    파일 부재 / 빈 파일 → ``({}, {}, {})``. last-line truncate / 알려지지 않은
    action_type 라인은 그 한 줄만 건너뛴다.
    """
    follower_counts: dict[str, int] = {}
    influence_scores: dict[str, int] = {}
    activity_counts: dict[str, int] = {}
    if not path.exists():
        return follower_counts, influence_scores, activity_counts
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = RoundEvent.model_validate_json(line)
        except ValidationError:
            continue
        if max_round is not None and event.round_num > max_round:
            continue
        action_type = event.action.type
        if action_type is not ActionType.DO_NOTHING:
            activity_counts[event.agent_id] = activity_counts.get(event.agent_id, 0) + 1
        weight = _INFLUENCE_WEIGHTS.get(action_type)
        if weight is None:
            continue
        if action_type is ActionType.FOLLOW:
            target = event.action.target_agent_id
            if target is None:
                continue
            follower_counts[target] = follower_counts.get(target, 0) + 1
            influence_scores[target] = influence_scores.get(target, 0) + weight
            continue
        target_post = event.action.target_post_id
        if target_post is None:
            continue
        author = _author_from_post_id(target_post)
        if author is None:
            continue
        influence_scores[author] = influence_scores.get(author, 0) + weight
    return follower_counts, influence_scores, activity_counts


__all__ = [
    "_INFLUENCE_WEIGHTS",
    "_author_from_post_id",
    "_read_engagement",
]
