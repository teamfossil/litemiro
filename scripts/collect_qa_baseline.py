#!/usr/bin/env python
"""self-baseline 수집 — 같은 ontology, seed 만 N 개로 바꿔 Phase 2 를 N 회 돌려
QaMetrics + PhenomenaMetrics 의 run-to-run 분포(평균±sigma)를 측정한다.

Phase 1(ontology 생성)은 재실행하지 않는다 — ``OntologyA.seed`` 필드만 바꾸면
같은 페르소나 모집단으로 다른 시뮬 궤적이 나온다 (seed 가 게이트 RNG·피드 정렬
등 비결정 요소의 단독 소스). 집계·요약 로직은 ``litemiro.phase3.baseline``
(순수 함수, 단위 테스트됨)에 있고, 본 스크립트는 실행+IO wrapper 다.

``OPENROUTER_API_KEY`` 는 각 ``litemiro-run`` 서브프로세스가 자체 ``.env`` 에서
로드한다 (repo 루트에서 실행할 것).

예::

    python scripts/collect_qa_baseline.py \
        --ontology-a runs/e2e2-2026-05-29/ontology_a_persona.json \
        --ontology-b runs/e2e2-2026-05-29/ontology_b_memory.json \
        --seeds 11 12 13 14 15 --rounds 5 --batch-size 20
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from litemiro.phase3 import DataAggregator
from litemiro.phase3.baseline import BASELINE_SCHEMA, extract_metrics, summarize_baseline


def _run_one(
    *,
    ontology_a: Path,
    ontology_b: Path,
    seed: int,
    rounds: int,
    out_root: Path,
    model: str,
    batch_size: int,
    semaphore_limit: int,
) -> dict[str, float | None]:
    """ontology_a 를 복사해 seed 만 바꾸고 Phase 2 1 회 → 메트릭 평면 dict."""
    data = json.loads(ontology_a.read_text(encoding="utf-8"))
    data["seed"] = seed
    run_dir = out_root / f"seed{seed}"
    run_dir.mkdir(parents=True, exist_ok=True)
    seeded = run_dir / "ontology_a.json"
    seeded.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    cmd = [
        sys.executable,
        "-m",
        "litemiro.cli.run",
        "--ontology-a",
        str(seeded),
        "--ontology-b",
        str(ontology_b),
        "--rounds",
        str(rounds),
        "--output-dir",
        str(run_dir),
        "--llm-model",
        model,
        "--batch-size",
        str(batch_size),
        "--semaphore-limit",
        str(semaphore_limit),
        "--reuse-output-dir",
    ]
    print(f"[seed {seed}] {' '.join(cmd)}", file=sys.stderr)
    subprocess.run(cmd, check=True)
    return extract_metrics(DataAggregator.aggregate(run_dir / "events.jsonl", seeded))


def _to_markdown(payload: dict[str, Any]) -> str:
    meta = payload["meta"]
    lines = [
        "# QA self-baseline",
        "",
        f"- runs: {meta['n_runs']} (seeds {meta['seeds']})",
        f"- rounds: {meta['rounds']}, model: `{meta['model']}`",
        f"- ontology_a: `{meta['ontology_a']}`",
    ]
    if meta.get("failed_seeds"):
        lines.append(f"- failed seeds (집계 제외): {meta['failed_seeds']}")
    lines += [
        "",
        "| metric | n | mean | std | min | max |",
        "|---|---|---|---|---|---|",
    ]
    for name, s in payload["metrics"].items():
        if s["mean"] is None:
            lines.append(f"| {name} | 0 | — | — | — | — |")
        else:
            lines.append(
                f"| {name} | {s['n']} | {s['mean']:.4f} | {s['std']:.4f} "
                f"| {s['min']:.4f} | {s['max']:.4f} |"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="collect_qa_baseline")
    p.add_argument("--ontology-a", required=True, type=Path)
    p.add_argument("--ontology-b", required=True, type=Path)
    p.add_argument(
        "--seeds", type=int, nargs="+", required=True, help="seed 리스트 — seed 당 1 run"
    )
    p.add_argument("--rounds", type=int, default=5)
    p.add_argument("--out-root", type=Path, default=Path("runs/qa-baseline"))
    p.add_argument("--llm-model", default="openrouter/qwen/qwen-plus")
    p.add_argument("--baseline-json", type=Path, default=Path("docs/qa/baseline.json"))
    p.add_argument("--batch-size", type=int, default=20, help="litemiro-run --batch-size")
    p.add_argument("--semaphore-limit", type=int, default=10, help="litemiro-run --semaphore-limit")
    args = p.parse_args(argv)

    rows: list[dict[str, float | None]] = []
    ok_seeds: list[int] = []
    failed: list[int] = []
    for seed in args.seeds:
        try:
            row = _run_one(
                ontology_a=args.ontology_a,
                ontology_b=args.ontology_b,
                seed=seed,
                rounds=args.rounds,
                out_root=args.out_root,
                model=args.llm_model,
                batch_size=args.batch_size,
                semaphore_limit=args.semaphore_limit,
            )
        except (subprocess.CalledProcessError, OSError, ValueError) as exc:
            # 한 seed 의 시뮬/집계 실패가 전체 수집을 버리지 않게 — 그 seed 만
            # 건너뛰고 나머지로 집계한다 (마지막 seed 실패 시 앞 N-1 손실 방지).
            failed.append(seed)
            print(f"[seed {seed}] FAILED — {exc}; 건너뜀", file=sys.stderr)
            continue
        rows.append(row)
        ok_seeds.append(seed)
        print(f"[seed {seed}] done", file=sys.stderr)

    if not rows:
        print("모든 seed 실패 — baseline 을 쓰지 않는다.", file=sys.stderr)
        return 1
    if failed:
        print(
            f"경고: {len(failed)}/{len(args.seeds)} seed 실패 {failed} — "
            f"성공 {len(rows)} 개로만 집계한다.",
            file=sys.stderr,
        )

    payload = {
        "meta": {
            "schema": BASELINE_SCHEMA,
            "n_runs": len(rows),
            "seeds": ok_seeds,
            "failed_seeds": failed,
            "rounds": args.rounds,
            "model": args.llm_model,
            "ontology_a": str(args.ontology_a),
        },
        "metrics": summarize_baseline(rows),
    }
    args.baseline_json.parent.mkdir(parents=True, exist_ok=True)
    args.baseline_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"baseline → {args.baseline_json}", file=sys.stderr)
    print(_to_markdown(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
