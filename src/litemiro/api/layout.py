"""Plaza 부감 뷰용 노드 좌표 계산.

Phase 2 가 끝난 plaza 의 events.jsonl 에서 FOLLOW 엣지를 모아 Fruchterman-
Reingold force-directed layout 을 돌린다. networkx 없이 numpy 만으로 구현 —
100 agent · 50 iter 기준 ~수십 ms.

좌표는 ``[0.0, 1.0] x [0.0, 1.0]`` 정규화 박스로 떨어진다. 프론트는 그대로
scale 만 곱해 캔버스에 그린다. 같은 (agent_ids, edges, seed) 입력이면 항상
같은 결과 — seed 는 라우트가 plaza_id 해시로 고정해 폴링/리로드에서도 좌표가
점프하지 않는다.

엣지가 0 개인 경우 (FOLLOW 미발생) 도 fall through — 반발력만 작동해 격자처럼
퍼진다. isolated 노드만 있어도 결정적.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np


def compute_layout(
    agent_ids: Sequence[str],
    edges: Iterable[tuple[str, str]],
    *,
    seed: int,
    iterations: int = 60,
    width: float = 1.0,
    height: float = 1.0,
) -> dict[str, tuple[float, float]]:
    """Fruchterman-Reingold force-directed layout.

    Args:
        agent_ids: 그릴 노드 전체 (events.jsonl 에 안 나타나도 ontology 에 있으면
            포함). 순서는 입력 순서대로 인덱싱 — 결정성은 ``seed`` 가 보장.
        edges: FOLLOW (follower, followee) 튜플 stream. 방향 무시 — layout 은
            인접 노드를 끌어당기는 거리 metric 만 본다. agent_ids 에 없는 endpoint
            는 무시 (이방인 엣지 방어).
        seed: numpy RNG 시드. 라우트가 plaza_id 의 hex prefix 를 int 로 매핑해
            넘긴다 — 같은 plaza 면 항상 같은 좌표.
        iterations: FR 반복 횟수. 50~100 에서 100 node 정도 깔끔하게 수렴.
        width / height: 출력 박스 크기. 기본 [0, 1] x [0, 1].

    Returns:
        ``{agent_id: (x, y)}`` — agent_ids 순서대로의 dict.
    """
    n = len(agent_ids)
    if n == 0:
        return {}
    if n == 1:
        # FR + min-max normalize 가 단일 노드를 (0, 0) 으로 떨어뜨려 보기 안 좋다.
        # 명시적으로 박스 중앙으로.
        return {agent_ids[0]: (width / 2.0, height / 2.0)}
    id_to_idx = {aid: i for i, aid in enumerate(agent_ids)}

    # 엣지를 인덱스 쌍으로 변환. 양쪽 다 known agent 일 때만 채택.
    edge_pairs: list[tuple[int, int]] = []
    for u, v in edges:
        if u == v:
            continue
        i = id_to_idx.get(u)
        j = id_to_idx.get(v)
        if i is None or j is None:
            continue
        edge_pairs.append((i, j))

    rng = np.random.default_rng(seed)
    pos = rng.uniform(low=0.0, high=1.0, size=(n, 2)) * np.array([width, height])

    # FR optimal edge length — sqrt(area / n).
    k = float(np.sqrt(width * height / max(n, 1)))
    # 초기 temperature = 박스의 가장 큰 변 / 10. iteration 마다 선형 감소.
    t = max(width, height) / 10.0
    dt = t / max(iterations, 1)

    edge_array = np.array(edge_pairs, dtype=np.int64) if edge_pairs else None

    for _ in range(iterations):
        # 모든 쌍의 변위 벡터. shape (n, n, 2).
        delta = pos[:, None, :] - pos[None, :, :]
        dist = np.linalg.norm(delta, axis=2)
        # 0 거리 (자기자신) 와 매우 가까운 점은 epsilon 으로 클램프해 분모 폭주 방지.
        np.fill_diagonal(dist, np.inf)
        safe_dist = np.maximum(dist, 1e-6)

        # 반발력: f_r = k^2 / d, 방향은 delta / d.
        repulsion = (k * k) / safe_dist  # (n, n)
        disp = np.einsum("ij,ijd->id", repulsion / safe_dist, delta)

        # 인력: f_a = d^2 / k, FOLLOW 엣지만. 방향은 -delta (잡아당김).
        if edge_array is not None:
            i_idx = edge_array[:, 0]
            j_idx = edge_array[:, 1]
            edge_delta = pos[i_idx] - pos[j_idx]
            edge_dist = np.linalg.norm(edge_delta, axis=1)
            safe_edge_dist = np.maximum(edge_dist, 1e-6)
            attraction = (edge_dist * edge_dist) / k  # (m,)
            edge_force = (attraction / safe_edge_dist)[:, None] * edge_delta
            # i 는 j 쪽으로 끌리고, j 는 i 쪽으로 끌린다.
            np.add.at(disp, i_idx, -edge_force)
            np.add.at(disp, j_idx, edge_force)

        # 변위 노름으로 정규화 후 temperature 캡.
        disp_norm = np.linalg.norm(disp, axis=1, keepdims=True)
        safe_norm = np.maximum(disp_norm, 1e-9)
        step = disp / safe_norm * np.minimum(disp_norm, t)
        pos = pos + step
        # 박스 안에 가둔다.
        pos[:, 0] = np.clip(pos[:, 0], 0.0, width)
        pos[:, 1] = np.clip(pos[:, 1], 0.0, height)
        t -= dt

    # 최종 좌표를 박스 안으로 정규화 — FR 이 끝까지 박스 안에 못 박힌 경우를 대비.
    xmin, ymin = pos.min(axis=0)
    xmax, ymax = pos.max(axis=0)
    span_x = max(xmax - xmin, 1e-6)
    span_y = max(ymax - ymin, 1e-6)
    pos[:, 0] = (pos[:, 0] - xmin) / span_x * width
    pos[:, 1] = (pos[:, 1] - ymin) / span_y * height

    return {aid: (float(pos[i, 0]), float(pos[i, 1])) for aid, i in id_to_idx.items()}


def plaza_seed(plaza_id: str) -> int:
    """``plaza_id`` (uuid4 hex) 의 앞 16 자리를 int 로 매핑한 결정적 시드.

    같은 plaza 면 layout 호출이 몇 번이든 같은 RNG 시드 → 같은 좌표. uuid4 의
    randomness 가 plaza 간 시드 충돌을 막아준다.
    """
    return int(plaza_id[:16], 16)


__all__ = ["compute_layout", "plaza_seed"]
