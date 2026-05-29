#!/usr/bin/env python
"""baseline ±ksigma 회귀 게이트.

입력 events 의 QaMetrics + PhenomenaMetrics 가 ``docs/qa/baseline.json`` 의
±sigma·sigma 범위를 벗어나면 위반을 stderr 에 찍고 non-zero exit 한다 (CI 게이트
후보). 판정 로직은 ``litemiro.phase3.baseline.check_regression`` (순수 함수,
단위 테스트됨). 분산 0 메트릭(단일 표본/동일값)은 게이트하지 않는다.

예::

    python scripts/check_qa_regression.py \
        --events runs/e2e2-2026-05-29/sim/events.jsonl \
        --ontology-a runs/e2e2-2026-05-29/ontology_a_persona.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from litemiro.phase3 import DataAggregator
from litemiro.phase3.baseline import check_regression, extract_metrics


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="check_qa_regression")
    p.add_argument("--events", required=True, type=Path)
    p.add_argument("--ontology-a", type=Path, default=None)
    p.add_argument("--baseline-json", type=Path, default=Path("docs/qa/baseline.json"))
    p.add_argument("--sigma", type=float, default=2.0)
    args = p.parse_args(argv)

    baseline = json.loads(args.baseline_json.read_text(encoding="utf-8"))
    now = extract_metrics(DataAggregator.aggregate(args.events, args.ontology_a))
    violations = check_regression(now, baseline["metrics"], sigma=args.sigma)
    if violations:
        print(
            f"REGRESSION: {len(violations)} metric(s) outside ±{args.sigma}sigma", file=sys.stderr
        )
        for v in violations:
            print(
                f"  {v['metric']}: {float(v['value']):.4f} outside "
                f"[{float(v['low']):.4f}, {float(v['high']):.4f}] "
                f"(mean {float(v['mean']):.4f} ± {float(v['std']):.4f})",
                file=sys.stderr,
            )
        return 1
    print(f"OK: all tracked metrics within ±{args.sigma}sigma of baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
