from __future__ import annotations

import pytest

from litemiro.budget import TokenBudgetManager
from litemiro.interfaces import TokenBudgetManagerLike


class TestConstruction:
    def test_negative_total_rejected(self) -> None:
        with pytest.raises(ValueError, match="total_budget"):
            TokenBudgetManager(total_budget=-1)

    def test_zero_total_allowed(self) -> None:
        mgr = TokenBudgetManager(total_budget=0)
        assert mgr.remaining() == 0
        assert mgr.has_budget(estimated_tokens=0) is True
        assert mgr.has_budget(estimated_tokens=1) is False

    def test_satisfies_protocol(self) -> None:
        # ``TokenBudgetManagerLike`` is ``runtime_checkable`` — guard
        # against accidentally drifting the concrete signature.
        assert isinstance(TokenBudgetManager(total_budget=10), TokenBudgetManagerLike)


class TestHasBudget:
    def test_fresh_manager_passes_up_to_total(self) -> None:
        mgr = TokenBudgetManager(total_budget=1000)
        assert mgr.has_budget(estimated_tokens=0) is True
        assert mgr.has_budget(estimated_tokens=999) is True
        assert mgr.has_budget(estimated_tokens=1000) is True
        assert mgr.has_budget(estimated_tokens=1001) is False

    def test_reflects_prior_consume(self) -> None:
        mgr = TokenBudgetManager(total_budget=1000)
        mgr.consume(tokens_used=400)
        assert mgr.has_budget(estimated_tokens=600) is True
        assert mgr.has_budget(estimated_tokens=601) is False

    def test_negative_estimated_rejected(self) -> None:
        mgr = TokenBudgetManager(total_budget=1000)
        with pytest.raises(ValueError, match="estimated_tokens"):
            mgr.has_budget(estimated_tokens=-1)


class TestConsume:
    def test_negative_used_rejected(self) -> None:
        mgr = TokenBudgetManager(total_budget=1000)
        with pytest.raises(ValueError, match="tokens_used"):
            mgr.consume(tokens_used=-1)

    def test_consume_accumulates(self) -> None:
        mgr = TokenBudgetManager(total_budget=1000)
        mgr.consume(tokens_used=100)
        mgr.consume(tokens_used=250)
        assert mgr.remaining() == 650

    def test_overconsume_is_recorded(self) -> None:
        # Backends sometimes return more tokens than the estimate; the
        # recorded number must be the real one. Subsequent ``has_budget``
        # then returns False — that's the brake, not the consume itself.
        mgr = TokenBudgetManager(total_budget=1000)
        mgr.consume(tokens_used=1500)
        assert mgr.remaining() == 0
        assert mgr.has_budget(estimated_tokens=0) is False


class TestRemaining:
    def test_initial_is_total(self) -> None:
        assert TokenBudgetManager(total_budget=1000).remaining() == 1000

    def test_clamps_to_zero_after_overconsume(self) -> None:
        mgr = TokenBudgetManager(total_budget=100)
        mgr.consume(tokens_used=250)
        assert mgr.remaining() == 0
