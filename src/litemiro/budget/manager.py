"""``TokenBudgetManager`` — per-simulation LLM-token cap.

Implements ``TokenBudgetManagerLike`` in ``litemiro.interfaces``. The
contract pinned by the unit suite:

* **Peek-only check**: ``has_budget`` does *not* reserve. The Protocol
  signature is split into ``has_budget`` + ``consume`` so a caller can
  gate an action ("can I afford this?") and then bill the actual
  ``tokens_used`` after the LLM round-trip — backends sometimes return
  more (or fewer) tokens than estimated, and the recorded number must
  be the real one, not the guess.
* **No reservation, no rollback**: race-window safety belongs to the
  scheduler. ``RoundManager`` performs the per-round ``has_budget``
  sweep up front so each round is a discrete checkpoint — within one
  round the live count is whatever the previous round closed at.
* **Over-consume is allowed**: a single oversized response can push
  usage past ``total_budget``; ``has_budget`` will then return ``False``
  for subsequent rounds and ``remaining`` clamps to zero. We never
  retroactively reject a real ``tokens_used`` value — that would
  silently misreport actual spend.

Negative inputs (budget, estimated, used) are rejected at the boundary
so the running count can't go backwards via API misuse.
"""

from __future__ import annotations


class TokenBudgetManager:
    def __init__(self, *, total_budget: int) -> None:
        if total_budget < 0:
            raise ValueError(f"total_budget must be non-negative, got {total_budget}")
        self._total = total_budget
        self._used = 0

    def has_budget(self, *, estimated_tokens: int) -> bool:
        if estimated_tokens < 0:
            raise ValueError(f"estimated_tokens must be non-negative, got {estimated_tokens}")
        return self._used + estimated_tokens <= self._total

    def consume(self, *, tokens_used: int) -> None:
        if tokens_used < 0:
            raise ValueError(f"tokens_used must be non-negative, got {tokens_used}")
        self._used += tokens_used

    def remaining(self) -> int:
        # Clamp at zero so an over-consume can't surface as a negative
        # "remaining" — callers display this and we don't want a minus
        # sign leaking into the operator dashboard.
        return max(0, self._total - self._used)


__all__ = ["TokenBudgetManager"]
