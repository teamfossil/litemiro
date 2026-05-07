from __future__ import annotations

import dataclasses

import pytest

from litemiro.core import RoundOutcome


def test_round_outcome_is_frozen() -> None:
    outcome = RoundOutcome(processed=5, early_exit=False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.processed = 10  # type: ignore[misc]


def test_round_outcome_uses_slots() -> None:
    outcome = RoundOutcome(processed=0, early_exit=True)
    # 3.11~3.13 raises AttributeError, 3.14+ raises TypeError.
    with pytest.raises((AttributeError, TypeError)):
        outcome.extra = "nope"  # type: ignore[attr-defined]


def test_round_outcome_field_values_round_trip() -> None:
    outcome = RoundOutcome(processed=42, early_exit=True)
    assert outcome.processed == 42
    assert outcome.early_exit is True


def test_round_outcome_equality() -> None:
    a = RoundOutcome(processed=1, early_exit=False)
    b = RoundOutcome(processed=1, early_exit=False)
    c = RoundOutcome(processed=1, early_exit=True)
    assert a == b
    assert a != c
