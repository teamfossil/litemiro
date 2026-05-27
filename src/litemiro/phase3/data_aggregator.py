"""`DataAggregator` — JSONL RoundEvent → 카테고리별 통계.

LLM 호출 없음. 결정적. 같은 입력은 항상 같은 ``AggregationResult`` 를
돌려준다 — 동일 보고서를 재현하려면 본 단계가 결정성을 보장해야 한다
(Section 3 Phase 3 메모리 노트의 "재현성 강제").

집계는 4 카테고리:

* ``action_distribution`` — ActionType 별 카운트 / 비율
* ``network_metrics`` — FOLLOW 액션 기반 신규 엣지 / 인기 노드
* ``topic_flow`` — CREATE_POST·QUOTE_POST 의 content 샘플
* ``time_series`` — 라운드별 액션 수 / DO_NOTHING 비율 / active agents
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from litemiro.models import ActionType, RoundEvent
from litemiro.phase3.models import (
    CATEGORY_ACTION_DISTRIBUTION,
    CATEGORY_NETWORK_METRICS,
    CATEGORY_TIME_SERIES,
    CATEGORY_TOPIC_FLOW,
    AggregationResult,
    QaMetrics,
)

_TOPIC_FLOW_SAMPLE_LIMIT = 10
_TOP_N = 5


class DataAggregator:
    @staticmethod
    def aggregate(jsonl_path: Path) -> AggregationResult:
        events = list(_load_events(jsonl_path))
        return DataAggregator.aggregate_events(events)

    @staticmethod
    def aggregate_events(events: list[RoundEvent]) -> AggregationResult:
        agents = sorted({e.agent_id for e in events})
        rounds = sorted({e.round_num for e in events})
        return AggregationResult(
            n_events=len(events),
            n_agents=len(agents),
            n_rounds=len(rounds),
            categories={
                CATEGORY_ACTION_DISTRIBUTION: _action_distribution(events),
                CATEGORY_NETWORK_METRICS: _network_metrics(events),
                CATEGORY_TOPIC_FLOW: _topic_flow(events),
                CATEGORY_TIME_SERIES: _time_series(events),
            },
            qa_metrics=_qa_metrics(events),
        )


def _load_events(path: Path) -> list[RoundEvent]:
    events: list[RoundEvent] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno} JSON 파싱 실패: {exc.msg}") from exc
            try:
                events.append(RoundEvent.model_validate(payload))
            except Exception as exc:
                raise ValueError(f"{path}:{lineno} RoundEvent 검증 실패: {exc}") from exc
    return events


def _action_distribution(events: list[RoundEvent]) -> dict[str, Any]:
    counts: Counter[str] = Counter(e.action.type.value for e in events)
    total = sum(counts.values()) or 1
    # ActionType enum 선언 순서를 그대로 유지해 결정성 확보.
    ordered_types = [t.value for t in ActionType]
    distribution = {t: counts.get(t, 0) for t in ordered_types}
    ratios = {t: counts.get(t, 0) / total for t in ordered_types}
    per_agent: dict[str, int] = defaultdict(int)
    for e in events:
        per_agent[e.agent_id] += 1
    return {
        "counts": distribution,
        "ratios": ratios,
        "total": total,
        "top_active_agents": [
            {"agent_id": aid, "actions": n}
            for aid, n in sorted(per_agent.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP_N]
        ],
    }


def _network_metrics(events: list[RoundEvent]) -> dict[str, Any]:
    follow_edges: list[tuple[str, str]] = []
    followee_counts: Counter[str] = Counter()
    follower_counts: Counter[str] = Counter()
    for e in events:
        if e.action.type is ActionType.FOLLOW and e.action.target_agent_id is not None:
            follow_edges.append((e.agent_id, e.action.target_agent_id))
            followee_counts[e.action.target_agent_id] += 1
            follower_counts[e.agent_id] += 1
    return {
        "n_follow_events": len(follow_edges),
        "top_followed": [
            {"agent_id": aid, "follows_received": n}
            for aid, n in sorted(followee_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP_N]
        ],
        "top_followers": [
            {"agent_id": aid, "follows_given": n}
            for aid, n in sorted(follower_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP_N]
        ],
    }


def _topic_flow(events: list[RoundEvent]) -> dict[str, Any]:
    """REPOST 가 round_manager 에서 새 Post 를 생성해 store/feed 에 들어가므로
    "신규 생성된 게시물" 이라는 표현은 CREATE+QUOTE+REPOST 합계가 맞다 (#110).
    반면 content sample 과 top_posters 는 작성자 인사이트용이라 본문이 있는
    CREATE/QUOTE 만 센다 — 두 의미를 한 카운터에 욱여넣지 않고 분리한다."""
    samples: list[dict[str, Any]] = []
    posts_per_agent: Counter[str] = Counter()
    posts_per_round: defaultdict[int, int] = defaultdict(int)
    n_amplifications = 0
    for e in events:
        if e.action.type in (ActionType.CREATE_POST, ActionType.QUOTE_POST):
            posts_per_agent[e.agent_id] += 1
            posts_per_round[e.round_num] += 1
            if len(samples) < _TOPIC_FLOW_SAMPLE_LIMIT:
                samples.append(
                    {
                        "round_num": e.round_num,
                        "agent_id": e.agent_id,
                        "action": e.action.type.value,
                        "content": e.action.content or "",
                    }
                )
        elif e.action.type is ActionType.REPOST:
            n_amplifications += 1
    n_content_posts = sum(posts_per_round.values())
    return {
        # 기존 키 — content 가 있는 게시물 (CREATE_POST + QUOTE_POST). 호환 유지.
        "n_posts": n_content_posts,
        # 명시 alias — "n_posts" 가 모호하니 보고서 prompt 에서 이 키를 쓰면
        # 의미가 분명해진다 (top_posters / samples 와 같은 모집단).
        "n_content_posts": n_content_posts,
        # REPOST 만 — 본문 없이 인용만 한 amplification.
        "n_amplifications": n_amplifications,
        # 사용자 입장에선 store/feed 에 새로 등장한 모든 Post 의 합계.
        # ReportComposer 가 "총 게시물 N건" 이라 표현할 때 인용해야 할 값.
        "total_posts_created": n_content_posts + n_amplifications,
        "posts_per_round": [
            {"round_num": r, "n": posts_per_round[r]} for r in sorted(posts_per_round)
        ],
        "top_posters": [
            {"agent_id": aid, "posts": n}
            for aid, n in sorted(posts_per_agent.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP_N]
        ],
        "samples": samples,
    }


def _time_series(events: list[RoundEvent]) -> dict[str, Any]:
    """LLM analyzer 가 series 배열을 직접 세다가 "14/15 라운드에서 0%" 같은
    환각을 낸 적이 있어서 (#110) round-level 집계는 여기서 미리 계산해 둔다.
    LLM 은 aggregate 의 사전 합산값을 그대로 인용하면 되고, 더 이상 라운드를
    세거나 분포를 추정할 필요가 없다."""
    by_round: defaultdict[int, list[RoundEvent]] = defaultdict(list)
    for e in events:
        by_round[e.round_num].append(e)
    rounds = sorted(by_round)
    series = []
    for r in rounds:
        round_events = by_round[r]
        do_nothing = sum(1 for ev in round_events if ev.action.type is ActionType.DO_NOTHING)
        active = len({ev.agent_id for ev in round_events})
        series.append(
            {
                "round_num": r,
                "n_actions": len(round_events),
                "n_do_nothing": do_nothing,
                "do_nothing_ratio": do_nothing / len(round_events) if round_events else 0.0,
                "n_active_agents": active,
            }
        )
    n_rounds = len(series)
    n_rounds_with_do_nothing = sum(1 for s in series if int(s["n_do_nothing"]) > 0)
    if series:
        ratios = [float(s["do_nothing_ratio"]) for s in series]
        avg_do_nothing_ratio = sum(ratios) / n_rounds
        max_do_nothing_ratio = max(ratios)
    else:
        avg_do_nothing_ratio = 0.0
        max_do_nothing_ratio = 0.0
    return {
        "rounds": rounds,
        "series": series,
        "aggregate": {
            "n_rounds": n_rounds,
            "n_rounds_with_do_nothing": n_rounds_with_do_nothing,
            "n_rounds_zero_do_nothing": n_rounds - n_rounds_with_do_nothing,
            "avg_do_nothing_ratio": avg_do_nothing_ratio,
            "max_do_nothing_ratio": max_do_nothing_ratio,
        },
    }


def _qa_metrics(events: list[RoundEvent]) -> QaMetrics:
    """OASIS 등가성 회귀 게이트용 결정적 수치 (`docs/qa/metrics.md`).

    빈 입력은 모든 메트릭을 0 으로 — 데이터가 없으면 다양성도 0 으로 보고하는
    것이 self-consistent (보고서 fallback 도 같은 규약).
    """
    return QaMetrics(
        action_entropy_normalized=_action_entropy_normalized(events),
        follow_clustering_coefficient=_follow_clustering_coefficient(events),
        content_word_entropy_normalized=_content_word_entropy_normalized(events),
    )


def _action_entropy_normalized(events: list[RoundEvent]) -> float:
    if not events:
        return 0.0
    counts = Counter(e.action.type for e in events)
    total = sum(counts.values())
    # K = ActionType 카테고리 수 (현 enum). 정규화 분모를 정의에 따라 고정해야
    # 라운드별 분포가 한쪽에 몰린 정도를 비교 가능.
    k = len(ActionType)
    if k <= 1:
        return 0.0
    probs = [c / total for c in counts.values() if c > 0]
    return _clamp_unit(_shannon_entropy(probs) / math.log2(k))


def _follow_clustering_coefficient(events: list[RoundEvent]) -> float:
    """FOLLOW 이벤트로 무방향 그래프 재구성 → 평균 local clustering coefficient.

    self-loop 와 중복 엣지는 무시 (set 으로 정규화). 노드 < 3 이면 정의되지 않아
    0.0 으로 떨어뜨림 — 같은 사유로 OASIS 비교 시 작은 시뮬레이션은 무의미.
    """
    neighbors: dict[str, set[str]] = defaultdict(set)
    for e in events:
        if e.action.type is not ActionType.FOLLOW:
            continue
        target = e.action.target_agent_id
        if target is None or target == e.agent_id:
            continue
        neighbors[e.agent_id].add(target)
        neighbors[target].add(e.agent_id)
    nodes = list(neighbors)
    if len(nodes) < 3:
        return 0.0
    total = 0.0
    counted = 0
    for node in nodes:
        nbrs = neighbors[node]
        if len(nbrs) < 2:
            # 정의에 따라 degree < 2 노드의 local coefficient 는 0 (분모 = 0).
            counted += 1
            continue
        possible = len(nbrs) * (len(nbrs) - 1) / 2
        sorted_neighbors = sorted(nbrs)
        actual = sum(
            1
            for i, a in enumerate(sorted_neighbors)
            for b in sorted_neighbors[i + 1 :]
            if b in neighbors[a]
        )
        total += actual / possible
        counted += 1
    return total / counted if counted else 0.0


def _content_word_entropy_normalized(events: list[RoundEvent]) -> float:
    """CREATE_POST / QUOTE_POST 의 content 공백 토크나이즈 → word freq Shannon /
    log2(|vocab|). 한국어 형태소 분석 없이 어휘 다양성만 근사 — 진짜 토픽
    entropy 는 RoundEvent.topics 필드 추가 후 별도 PR (`docs/qa/metrics.md`).
    """
    tokens: list[str] = []
    for e in events:
        if e.action.type not in (ActionType.CREATE_POST, ActionType.QUOTE_POST):
            continue
        content = e.action.content or ""
        tokens.extend(content.split())
    if not tokens:
        return 0.0
    counts = Counter(tokens)
    vocab = len(counts)
    if vocab <= 1:
        return 0.0
    total = sum(counts.values())
    probs = [c / total for c in counts.values()]
    return _clamp_unit(_shannon_entropy(probs) / math.log2(vocab))


def _shannon_entropy(probs: list[float]) -> float:
    return -sum(p * math.log2(p) for p in probs if p > 0.0)


def _clamp_unit(value: float) -> float:
    # 정규화 결과가 부동소수 오차로 1.0 을 미세하게 넘기는 경우 (예: 1+2e-16)
    # QaMetrics 의 Field(le=1.0) 검증을 통과시키기 위해 [0, 1] 로 잘라낸다.
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


__all__ = ["DataAggregator"]
