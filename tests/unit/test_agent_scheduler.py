"""Behaviour pinning for ``AgentScheduler``.

Covers the four concerns the design doc enumerated:
* construction validation
* determinism (same inputs → same output, different round → different output)
* boundary values (rate=0/1 collapse to never/always active)
* input-order preservation
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from litemiro.core import AgentScheduler
from litemiro.models import Agent


class TestConstruction:
    def test_accepts_int_seed(self) -> None:
        AgentScheduler(global_seed=42)

    def test_negative_round_rejected(self, make_agent: Callable[..., Agent]) -> None:
        scheduler = AgentScheduler(global_seed=0)
        with pytest.raises(ValueError, match="round_num"):
            scheduler.select_active((make_agent(),), round_num=-1)


class TestDeterminism:
    def test_same_inputs_yield_same_output(self, make_agent: Callable[..., Agent]) -> None:
        agents = tuple(make_agent(agent_id=f"a-{i:03d}", activation_rate=0.5) for i in range(20))
        scheduler_a = AgentScheduler(global_seed=42)
        scheduler_b = AgentScheduler(global_seed=42)
        assert scheduler_a.select_active(agents, round_num=3) == scheduler_b.select_active(
            agents, round_num=3
        )

    def test_different_rounds_yield_different_subsets(
        self, make_agent: Callable[..., Agent]
    ) -> None:
        agents = tuple(make_agent(agent_id=f"a-{i:03d}", activation_rate=0.5) for i in range(50))
        scheduler = AgentScheduler(global_seed=42)
        round_0 = scheduler.select_active(agents, round_num=0)
        round_1 = scheduler.select_active(agents, round_num=1)
        # Statistically the two subsets should differ for ~25 agents.
        assert round_0 != round_1

    def test_different_seeds_yield_different_subsets(
        self, make_agent: Callable[..., Agent]
    ) -> None:
        agents = tuple(make_agent(agent_id=f"a-{i:03d}", activation_rate=0.5) for i in range(50))
        a = AgentScheduler(global_seed=1).select_active(agents, round_num=0)
        b = AgentScheduler(global_seed=2).select_active(agents, round_num=0)
        assert a != b


class TestBoundaryRates:
    def test_zero_rate_never_active(self, make_agent: Callable[..., Agent]) -> None:
        agents = tuple(make_agent(agent_id=f"a-{i}", activation_rate=0.0) for i in range(10))
        scheduler = AgentScheduler(global_seed=42)
        for r in range(20):
            assert scheduler.select_active(agents, round_num=r) == ()

    def test_one_rate_always_active(self, make_agent: Callable[..., Agent]) -> None:
        agents = tuple(make_agent(agent_id=f"a-{i}", activation_rate=1.0) for i in range(10))
        scheduler = AgentScheduler(global_seed=42)
        for r in range(20):
            ids = scheduler.select_active(agents, round_num=r)
            assert ids == tuple(a.agent_id for a in agents)


class TestEmptyAndOrder:
    def test_empty_agents_yields_empty_output(self) -> None:
        scheduler = AgentScheduler(global_seed=42)
        assert scheduler.select_active((), round_num=0) == ()

    def test_output_preserves_input_order(self, make_agent: Callable[..., Agent]) -> None:
        # IDs deliberately *not* alphabetical so we can detect any sort.
        ids = ["zeta", "alpha", "mu", "beta", "kappa"]
        agents = tuple(make_agent(agent_id=aid, activation_rate=1.0) for aid in ids)
        scheduler = AgentScheduler(global_seed=99)
        assert scheduler.select_active(agents, round_num=0) == tuple(ids)


class TestPerAgentRate:
    def test_mixed_rates_respect_per_agent(self, make_agent: Callable[..., Agent]) -> None:
        # Only the rate=1.0 agents should ever appear; rate=0.0 never.
        always = make_agent(agent_id="always-A", activation_rate=1.0)
        never = make_agent(agent_id="never-A", activation_rate=0.0)
        sometimes = make_agent(agent_id="sometimes-A", activation_rate=0.5)
        scheduler = AgentScheduler(global_seed=42)
        rounds_with_never = sum(
            1
            for r in range(50)
            if "never-A" in scheduler.select_active((always, never, sometimes), r)
        )
        rounds_with_always = sum(
            1
            for r in range(50)
            if "always-A" in scheduler.select_active((always, never, sometimes), r)
        )
        assert rounds_with_never == 0
        assert rounds_with_always == 50

    def test_rate_0_5_distribution_within_tolerance(self, make_agent: Callable[..., Agent]) -> None:
        # 1000 trials with rate=0.5 should land near 500 actives — wide
        # tolerance to keep the test stable across stdlib RNG changes.
        agents = tuple(make_agent(agent_id=f"a-{i:04d}", activation_rate=0.5) for i in range(1000))
        scheduler = AgentScheduler(global_seed=42)
        active = scheduler.select_active(agents, round_num=0)
        assert 400 <= len(active) <= 600
