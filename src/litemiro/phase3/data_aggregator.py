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

import structlog
from pydantic import ValidationError

from litemiro.models import ActionType, RoundEvent
from litemiro.phase3.models import (
    CATEGORY_ACTION_DISTRIBUTION,
    CATEGORY_NETWORK_METRICS,
    CATEGORY_TIME_SERIES,
    CATEGORY_TOPIC_FLOW,
    AggregationResult,
    PhenomenaMetrics,
    QaMetrics,
)

_TOPIC_FLOW_SAMPLE_LIMIT = 10
# top_* 리스트 길이. 상위 5 → 10 으로 늘려 롱테일 일부를 직접 노출하고, 그 너머
# 분포는 `_distribution_summary` 의 gini / top5_share 로 요약한다.
_TOP_N = 10

_log = structlog.get_logger(__name__)


class DataAggregator:
    @staticmethod
    def aggregate(jsonl_path: Path, ontology_path: Path | None = None) -> AggregationResult:
        """events.jsonl → 카테고리 통계 + QA/현상 메트릭.

        ``ontology_path`` 가 주어지면 agent 별 ideology 를 로드해 양극화 메트릭을
        계산한다. 없으면 (기존 단일 인자 호출 그대로) 양극화는 None — 하위호환.
        """
        events = list(_load_events(jsonl_path))
        ideology = _load_ideology(ontology_path) if ontology_path is not None else None
        return DataAggregator.aggregate_events(events, ideology=ideology)

    @staticmethod
    def aggregate_events(
        events: list[RoundEvent], ideology: dict[str, float] | None = None
    ) -> AggregationResult:
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
            phenomena=_phenomena_metrics(events, ideology),
        )


def _load_events(path: Path) -> list[RoundEvent]:
    """events.jsonl → RoundEvent 리스트. 깨진 라인은 skip + warning.

    ``api/store.py:_parse_event_log`` 와 동일 lenient 패턴. 같은 jsonl 이
    SSE 재연결엔 살아있고 ``/report`` 엔 죽는 비대칭을 막는다 — 라운드
    200 x agent 100 = 20k 라인 jsonl 에서 last-line truncate 같은 partial
    write 한 줄로 보고서 전체가 사망하면 ROI 가 안 맞는다. 구조화 로그
    (``data_aggregator_event_skipped`` / ``..._skipped_total``) 로 카운트가
    호출자에게 흘러간다 — 후속에서 ``AggregationResult`` 노출 후보.
    """
    events: list[RoundEvent] = []
    skipped = 0
    with path.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                events.append(RoundEvent.model_validate_json(stripped))
            except ValidationError as exc:
                skipped += 1
                # 첫 줄만 — pydantic 메시지 본문은 멀티라인이고 라운드당 같은
                # 사유로 다발 발생할 수 있어 로그가 시끄럽지 않게 자른다.
                first_line = str(exc).splitlines()[0] if str(exc) else ""
                _log.warning(
                    "data_aggregator_event_skipped",
                    path=str(path),
                    lineno=lineno,
                    error=first_line[:200],
                )
    if skipped:
        _log.warning(
            "data_aggregator_skipped_total",
            path=str(path),
            skipped=skipped,
            kept=len(events),
        )
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
        "agent_activity_concentration": _distribution_summary(per_agent),
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
        # 상위 N 명 너머 수신/발신 분포의 집중도 — 허브-스포크 vs 분산 정량화.
        "followee_concentration": _distribution_summary(followee_counts),
        "follower_concentration": _distribution_summary(follower_counts),
    }


def _topic_flow(events: list[RoundEvent]) -> dict[str, Any]:
    """REPOST 가 round_manager 에서 새 Post 를 생성해 store/feed 에 들어가므로
    "신규 생성된 게시물" 이라는 표현은 CREATE+QUOTE+REPOST 합계가 맞다 (#110).
    반면 content sample 과 top_posters 는 작성자 인사이트용이라 본문이 있는
    CREATE/QUOTE 만 센다 — 두 의미를 한 카운터에 욱여넣지 않고 분리한다."""
    candidates_by_round: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    posts_per_agent: Counter[str] = Counter()
    posts_per_round: defaultdict[int, int] = defaultdict(int)
    n_amplifications = 0
    for e in events:
        if e.action.type in (ActionType.CREATE_POST, ActionType.QUOTE_POST):
            posts_per_agent[e.agent_id] += 1
            posts_per_round[e.round_num] += 1
            # 라운드별로 모아 round-robin 으로 표본을 뽑는다 — 이벤트 순서대로 앞
            # _TOPIC_FLOW_SAMPLE_LIMIT 개만 자르면 라운드 0 에 쏠려 후반 라운드
            # 콘텐츠 흐름이 보고서에서 사라졌다.
            candidates_by_round[e.round_num].append(
                {
                    "round_num": e.round_num,
                    "agent_id": e.agent_id,
                    "action": e.action.type.value,
                    "content": e.action.content or "",
                }
            )
        elif e.action.type is ActionType.REPOST:
            n_amplifications += 1
    samples = _round_robin_sample(candidates_by_round, _TOPIC_FLOW_SAMPLE_LIMIT)
    n_content_posts = sum(posts_per_round.values())
    return {
        # [DEPRECATED] "n_posts" 가 "총 게시물" 로 오해돼 REPOST 누락 (#110) 원인이
        # 됐다. 새 코드는 의미가 명확한 n_content_posts / n_amplifications /
        # total_posts_created 중 하나를 쓰고, 본 키는 구버전 prompt·외부
        # consumer 호환을 위해서만 유지한다. 다음 마이너에서 제거 후보.
        "n_posts": n_content_posts,
        # content 가 있는 게시물 (CREATE_POST + QUOTE_POST) — top_posters /
        # samples 와 같은 모집단.
        "n_content_posts": n_content_posts,
        # REPOST 액션 건수와 정확히 같다 — 본문 없이 재게시한 증폭. QUOTE_POST 는
        # 본문이 있어 여기 들어가지 않고 n_content_posts 로 집계되므로, 보고서는
        # n_amplifications 와 action_distribution 의 REPOST 카운트를 같은 값으로
        # 다뤄야 한다 (라벨 모호 해소).
        "n_amplifications": n_amplifications,
        # store/feed 에 새로 등장한 모든 Post 의 합계. ReportComposer 가
        # "총 게시물 N건" 이라 표현할 때 인용해야 하는 정식 키.
        "total_posts_created": n_content_posts + n_amplifications,
        "posts_per_round": [
            {"round_num": r, "n": posts_per_round[r]} for r in sorted(posts_per_round)
        ],
        "top_posters": [
            {"agent_id": aid, "posts": n}
            for aid, n in sorted(posts_per_agent.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP_N]
        ],
        # 상위 N 명 너머 작성 분포의 집중도 — 소수 헤비 작성자 vs 분산.
        "poster_concentration": _distribution_summary(posts_per_agent),
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


def _gini(values: list[int]) -> float:
    """분포의 지니 계수 [0,1] — 0 = 완전 균등, 1 = 한 행위자에 집중.

    top_* 리스트가 가리지 못하는 롱테일의 불평등을 한 수치로 요약한다. 표준
    정의 (정렬값의 가중 누적합) 를 쓰며, 빈 입력·합 0 은 0.0. 값만 보므로 입력
    순서와 무관하게 결정적.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    total = sum(ordered)
    if total == 0:
        return 0.0
    cumulative = sum((i + 1) * x for i, x in enumerate(ordered))
    return _clamp_unit((2.0 * cumulative) / (n * total) - (n + 1) / n)


def _distribution_summary(counts: dict[str, int]) -> dict[str, Any]:
    """행위자별 카운트 분포의 집중도 요약 — top_* 너머 롱테일 정량화.

    * ``n_unique`` — 분포에 등장한 고유 행위자 수.
    * ``top5_share`` — 상위 5 행위자가 차지하는 비율 (집중 vs 분산).
    * ``gini`` — 지니 계수 (롱테일 불평등).
    """
    values = [int(v) for v in counts.values()]
    total = sum(values)
    if total == 0:
        return {"n_unique": len(values), "top5_share": 0.0, "gini": 0.0}
    top5 = sum(sorted(values, reverse=True)[:5])
    return {
        "n_unique": len(values),
        "top5_share": top5 / total,
        "gini": _gini(values),
    }


def _round_robin_sample(
    by_round: dict[int, list[dict[str, Any]]], limit: int
) -> list[dict[str, Any]]:
    """라운드별 후보를 라운드로빈으로 ``limit`` 까지 뽑아 라운드 순으로 돌려준다.

    이벤트 순서대로 앞에서 자르면 표본이 라운드 0 에 쏠려 후반 라운드 콘텐츠
    흐름이 보고서에서 사라진다. 라운드 오름차순으로 한 개씩 번갈아 뽑아 모든
    라운드가 표본에 대표되게 한다. 라운드 안에서는 등장 순서를 유지 — 결정적.
    """
    rounds = sorted(by_round)
    picked: list[dict[str, Any]] = []
    depth = 0
    while len(picked) < limit:
        advanced = False
        for r in rounds:
            bucket = by_round[r]
            if depth < len(bucket):
                picked.append(bucket[depth])
                advanced = True
                if len(picked) >= limit:
                    break
        if not advanced:
            break
        depth += 1
    picked.sort(key=lambda s: s["round_num"])
    return picked


def _load_ideology(ontology_path: Path) -> dict[str, float]:
    """ontology_a JSON 의 agent 별 ideology([0,1]) 맵.

    Phase 1 산출의 ``agents`` 는 {key: {agent_id, ideology, ...}} dict — value 의
    ``agent_id`` 로 events 와 join 한다 (둘은 동일 식별자). ideology 누락/비수치
    agent 는 건너뛴다 (graceful).
    """
    with ontology_path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    agents = data.get("agents", {})
    rows = agents.values() if isinstance(agents, dict) else agents
    result: dict[str, float] = {}
    for a in rows:
        if not isinstance(a, dict):
            continue
        aid = a.get("agent_id")
        ideo = a.get("ideology")
        if isinstance(aid, str) and isinstance(ideo, int | float) and not isinstance(ideo, bool):
            result[aid] = float(ideo)
    return result


def _phenomena_metrics(
    events: list[RoundEvent], ideology: dict[str, float] | None
) -> PhenomenaMetrics:
    depth, breadth, scale, n_cascades = _cascade_metrics(events)
    gap, assortativity = _polarization(events, ideology)
    popularity_gini, early = _herd(events)
    return PhenomenaMetrics(
        cascade_max_depth=depth,
        cascade_max_breadth=breadth,
        cascade_max_scale=scale,
        n_cascades=n_cascades,
        follow_ideology_gap=gap,
        ideology_assortativity=assortativity,
        popularity_gini=popularity_gini,
        early_mover_share=early,
    )


def _cascade_metrics(events: list[RoundEvent]) -> tuple[int, int, int, int]:
    """REPOST/QUOTE 의 target_post_id 체인으로 전파 트리를 재구성 (정보 확산).

    post_id 는 ``{agent}_r{round:04d}`` (round_manager 보장 — agent 당 라운드당 1
    액션이라 유일). CREATE_POST 가 루트, REPOST/QUOTE 가 부모를 가리키는 자식.
    반환: (depth=재게시 체인 최대 깊이, breadth=한 포스트 최대 직접 재게시 수,
    scale=한 캐스케이드 고유 참여 에이전트 수, n_cascades=재게시 1+ 인 루트 수).
    post_id 의 round 가 단조 증가라 사이클이 없어 재귀가 종료한다.
    """
    children: dict[str, list[str]] = defaultdict(list)
    author: dict[str, str] = {}
    nodes: list[str] = []
    for e in events:
        if e.action.type not in (
            ActionType.CREATE_POST,
            ActionType.QUOTE_POST,
            ActionType.REPOST,
        ):
            continue
        pid = f"{e.agent_id}_r{e.round_num:04d}"
        nodes.append(pid)
        author[pid] = e.agent_id
        if e.action.type is not ActionType.CREATE_POST and e.action.target_post_id is not None:
            children[e.action.target_post_id].append(pid)
    if not nodes:
        return 0, 0, 0, 0
    depth_cache: dict[str, int] = {}

    def node_depth(pid: str) -> int:
        if pid in depth_cache:
            return depth_cache[pid]
        kids = children.get(pid, ())
        depth_cache[pid] = 0 if not kids else 1 + max(node_depth(c) for c in kids)
        return depth_cache[pid]

    def subtree_authors(pid: str) -> set[str]:
        acc = {author[pid]} if pid in author else set()
        for c in children.get(pid, ()):
            acc |= subtree_authors(c)
        return acc

    has_parent = {c for kids in children.values() for c in kids}
    roots = [p for p in nodes if p not in has_parent]
    max_depth = max(node_depth(p) for p in nodes)
    max_breadth = max((len(children.get(p, ())) for p in nodes), default=0)
    max_scale = max((len(subtree_authors(r)) for r in roots), default=0)
    n_cascades = sum(1 for r in roots if children.get(r))
    return max_depth, max_breadth, max_scale, n_cascades


def _polarization(
    events: list[RoundEvent], ideology: dict[str, float] | None
) -> tuple[float | None, float | None]:
    """FOLLOW 엣지의 ideology 동질성 (집단 양극화). ontology 없으면 (None, None).

    gap=평균 |ideology[follower] - ideology[followee]| ([0,1], 낮을수록 끼리끼리),
    assortativity=follower/followee ideology Pearson 상관 ([-1,1], 양수=동질 선호).
    """
    if not ideology:
        return None, None
    follower_ideo: list[float] = []
    followee_ideo: list[float] = []
    for e in events:
        if e.action.type is not ActionType.FOLLOW or e.action.target_agent_id is None:
            continue
        f = ideology.get(e.agent_id)
        t = ideology.get(e.action.target_agent_id)
        if f is not None and t is not None:
            follower_ideo.append(f)
            followee_ideo.append(t)
    if not follower_ideo:
        return None, None
    gap = sum(abs(f - t) for f, t in zip(follower_ideo, followee_ideo, strict=True)) / len(
        follower_ideo
    )
    return _clamp_unit(gap), _pearson(follower_ideo, followee_ideo)


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson 상관. 표본 < 2 또는 한쪽 분산 0 이면 None (정의되지 않음)."""
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0.0 or syy <= 0.0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    return max(-1.0, min(1.0, float(sxy / (sxx**0.5 * syy**0.5))))


def _herd(events: list[RoundEvent]) -> tuple[float, float | None]:
    """herd 효과 — 인기 집중(popularity_gini) + early-mover 지속성.

    popularity_gini=피팔로우 수 분포의 지니([0,1], #153 followee gini 승격).
    early_mover_share=전반부 라운드 상위 5 피팔로우 노드가 후반부 FOLLOW 의 몇
    비율을 흡수하는가([0,1], 높을수록 "이미 인기있는 노드를 더 follow").
    """
    followee_counts: Counter[str] = Counter()
    timeline: list[tuple[int, str]] = []
    for e in events:
        if e.action.type is ActionType.FOLLOW and e.action.target_agent_id is not None:
            followee_counts[e.action.target_agent_id] += 1
            timeline.append((e.round_num, e.action.target_agent_id))
    popularity_gini = _gini(list(followee_counts.values())) if followee_counts else 0.0
    return popularity_gini, _early_mover_share(timeline)


def _early_mover_share(timeline: list[tuple[int, str]]) -> float | None:
    if not timeline:
        return None
    rounds = sorted({r for r, _ in timeline})
    if len(rounds) < 2:
        return None
    split = rounds[len(rounds) // 2]
    early = [a for r, a in timeline if r < split]
    late = [a for r, a in timeline if r >= split]
    if not early or not late:
        return None
    top_early = {a for a, _ in Counter(early).most_common(5)}
    return sum(1 for a in late if a in top_early) / len(late)


__all__ = ["DataAggregator"]
