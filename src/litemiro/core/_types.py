"""Internal helper types — not part of the Phase 2 → Phase 3 wire format."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RoundOutcome:
    processed: int
    early_exit: bool


__all__ = ["RoundOutcome"]
