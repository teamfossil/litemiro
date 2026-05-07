"""Internal helper types for the engine core (A 오너).

Anything in this module is *not* part of the Phase 2 → Phase 3 wire
format — for that, see ``litemiro.models``. These types describe the
*runtime progression* of a simulation (e.g., per-round outcome,
early-exit signaling) and are deliberately kept out of the JSON Schema
contract.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RoundOutcome:
    """Result of a single ``RoundManager.run_round`` invocation.

    ``processed`` counts the active agents whose actions were applied
    in the round. ``early_exit`` is set when ``TokenBudgetManager``
    refuses entry — the simulation halts after the previous round's
    checkpoint, so the caller can surface this as a clean shutdown
    rather than a crash.
    """

    processed: int
    early_exit: bool


__all__ = ["RoundOutcome"]
