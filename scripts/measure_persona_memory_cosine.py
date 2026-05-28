"""§6.5 페르소나-메모리 cosine 분포 calibration 측정.

`OntologyLoader.validate_consistency` 의 legacy (set intersection) 와 옵션 B
(STEmbedder) 두 경로를 같은 입력에 돌려 warning rate + cosine 분포 +
threshold sweep 을 stdout 으로 보고한다. ADR-0001 (`docs/decisions/0001-
persona-memory-cosine-threshold.md`) 의 측정 표를 재생성하는 진입점.

입력: 경로 인자 1 개. 그 아래 `seed-{42,43,44}/ontology_a_persona.json`,
`ontology_b_memory.json` 6 개 파일이 있어야 한다. quick 프리셋으로 새로
생성하려면:

    for s in 42 43 44; do
        litemiro-ontology --preset quick --seed $s \\
            --output-dir runs/persona-mem-remeasure/seed-$s
    done

옵션 B 경로는 [-1, 1] clamp 후의 cosine 을 그대로 사용한다 (production
`validate_consistency` 와 동일 로직, 단 raise 없이 분포만 수집).
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

from litemiro.embedding.sentence_transformers import STEmbedder
from litemiro.integration.ontology_loader import (
    OntologyLoader,
    _cosine,
)

SEEDS = (42, 43, 44)
THRESHOLDS = (0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60)


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    k = max(0, min(n - 1, round((n - 1) * p)))
    return s[k]


def _describe(values: list[float]) -> str:
    if not values:
        return "n=0"
    return (
        f"n={len(values):3d}  min={min(values):.3f}  p25={_pct(values, 0.25):.3f}  "
        f"p50={_pct(values, 0.50):.3f}  p75={_pct(values, 0.75):.3f}  "
        f"max={max(values):.3f}  mean={statistics.fmean(values):.3f}"
    )


def _max_pairwise(
    persona: set[str],
    memory: set[str],
    *,
    embedder: STEmbedder,
    cache: dict[str, tuple[float, ...]],
) -> float:
    if not persona or not memory:
        return 0.0
    for t in persona | memory:
        if t not in cache:
            cache[t] = embedder.embed(t)
    return max(_cosine(cache[p], cache[m]) for p in persona for m in memory)


def measure(root: Path) -> None:
    embedder = STEmbedder()
    embedder.embed("warmup")
    cache: dict[str, tuple[float, ...]] = {}

    print("LEGACY (set intersection) — validate_consistency, embedder=None")
    pooled_active = pooled_warn = 0
    for seed in SEEDS:
        a, b = OntologyLoader.load(
            ontology_a_path=root / f"seed-{seed}" / "ontology_a_persona.json",
            ontology_b_path=root / f"seed-{seed}" / "ontology_b_memory.json",
        )
        active = sum(1 for aid in a.agents if (s := b.stores.get(aid)) and s.semantic)
        warnings = OntologyLoader.validate_consistency(ontology_a=a, ontology_b=b)
        rate = len(warnings) / active * 100 if active else 0.0
        print(f"  seed={seed}  active={active:3d}  warn={len(warnings):3d}  rate={rate:5.1f}%")
        pooled_active += active
        pooled_warn += len(warnings)
    rate = pooled_warn / pooled_active * 100 if pooled_active else 0.0
    print(f"  POOLED active={pooled_active}  warn={pooled_warn}  rate={rate:.1f}%")
    print()

    print("OPTION B (STEmbedder all-MiniLM-L6-v2) — cosine 분포 + threshold sweep")
    records: list[tuple[int, str, str, float]] = []
    per_seed_active: dict[int, int] = {}
    for seed in SEEDS:
        a, b = OntologyLoader.load(
            ontology_a_path=root / f"seed-{seed}" / "ontology_a_persona.json",
            ontology_b_path=root / f"seed-{seed}" / "ontology_b_memory.json",
        )
        active = 0
        for aid in sorted(a.agents):
            profile = a.agents[aid]
            store = b.stores.get(aid)
            mems = store.semantic if store else []
            if not mems:
                continue
            active += 1
            memory_topics: set[str] = set().union(*(set(m.topics) for m in mems))
            sim = _max_pairwise(set(profile.topics), memory_topics, embedder=embedder, cache=cache)
            records.append((seed, aid, profile.origin.value, sim))
        per_seed_active[seed] = active

    sims = [s for _, _, _, s in records]
    print(f"  active 총 {len(records)} (= persona 평가 대상)")
    print(f"  전체 cosine 분포: {_describe(sims)}")
    for origin in sorted({r[2] for r in records}):
        os_ = [s for _, _, o, s in records if o == origin]
        print(f"    [{origin:<10}] {_describe(os_)}")

    print()
    print("  threshold sweep (warning = max_similarity < threshold):")
    print("  threshold | warn | rate    | extracted | derived")
    print("  ----------+------+---------+-----------+--------")
    for th in THRESHOLDS:
        wt = sum(1 for s in sims if s < th)
        we = sum(1 for _, _, o, s in records if o == "extracted" and s < th)
        wd = sum(1 for _, _, o, s in records if o == "derived" and s < th)
        rate = wt / len(records) * 100 if records else 0.0
        print(f"   {th:>8.2f} | {wt:>4d} | {rate:>6.1f}% | {we:>9d} | {wd:>6d}")

    print()
    print("  하위 10 (가장 mismatch — threshold 와 가까운 케이스):")
    for seed, aid, origin, sim in sorted(records, key=lambda t: t[3])[:10]:
        print(f"    seed={seed}  {aid:<40}  origin={origin:<10}  cos={sim:.3f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root",
        type=Path,
        help="seed-{42,43,44}/ontology_{a,b}*.json 6 파일이 있는 디렉토리",
    )
    args = parser.parse_args(argv)
    if not args.root.is_dir():
        print(f"디렉토리 없음: {args.root}", file=sys.stderr)
        return 2
    missing = [
        p
        for seed in SEEDS
        for name in ("ontology_a_persona.json", "ontology_b_memory.json")
        if not (p := args.root / f"seed-{seed}" / name).exists()
    ]
    if missing:
        for p in missing:
            print(f"파일 없음: {p}", file=sys.stderr)
        return 2
    measure(args.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
