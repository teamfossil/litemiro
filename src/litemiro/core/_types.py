"""Internal helper types — not part of the Phase 2 → Phase 3 wire format."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RoundOutcome:
    processed: int
    early_exit: bool


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """``run_simulation`` 의 반환 — 시뮬레이션 한 회의 외부 가시 상태.

    Phase 3 분석/리포트가 ``event_log_path`` 와 ``checkpoint_dir`` 로 데이터에
    재진입하므로, 본 result 는 그 경로 + 진행 메타 (rounds_run / early_exit /
    tokens_used) 만 담는다. 더 깊은 통계는 EventLogger 가 남긴 JSONL 에서
    재계산.
    """

    rounds_run: int
    early_exit: bool
    event_log_path: Path
    checkpoint_dir: Path
    tokens_used: int


__all__ = ["RoundOutcome", "SimulationResult"]
