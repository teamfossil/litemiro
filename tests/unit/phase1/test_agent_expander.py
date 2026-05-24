"""AgentExpander unit tests."""

from __future__ import annotations

from litemiro.phase1.agent_expander import AgentExpander
from litemiro.phase1.local_graph import LocalGraph
from litemiro.phase1.models import AgentOrigin, AgentSeed, ExtractionResult


class TestAgentExpander:
    def test_no_expansion_needed(
        self,
        sample_extraction: ExtractionResult,
        sample_agent_seeds: list[AgentSeed],
    ) -> None:
        graph = LocalGraph.build(sample_extraction)
        expander = AgentExpander(graph=graph, requirement="AI 규제", seed=42)
        result = expander.expand(sample_agent_seeds, target_count=2)
        assert len(result) == 2

    def test_trim_excess(
        self,
        sample_extraction: ExtractionResult,
        sample_agent_seeds: list[AgentSeed],
    ) -> None:
        graph = LocalGraph.build(sample_extraction)
        expander = AgentExpander(graph=graph, requirement="AI 규제", seed=42)
        result = expander.expand(sample_agent_seeds, target_count=1)
        assert len(result) == 1

    def test_expansion_generates_derived(
        self,
        sample_extraction: ExtractionResult,
        sample_agent_seeds: list[AgentSeed],
    ) -> None:
        graph = LocalGraph.build(sample_extraction)
        expander = AgentExpander(graph=graph, requirement="AI 규제 정책", seed=42)
        result = expander.expand(sample_agent_seeds, target_count=10)
        assert len(result) == 10
        derived = [s for s in result if s.origin == AgentOrigin.DERIVED]
        assert len(derived) >= 1

    def test_deterministic_with_same_seed(
        self,
        sample_extraction: ExtractionResult,
        sample_agent_seeds: list[AgentSeed],
    ) -> None:
        graph = LocalGraph.build(sample_extraction)
        exp1 = AgentExpander(graph=graph, requirement="AI", seed=42)
        exp2 = AgentExpander(graph=graph, requirement="AI", seed=42)
        r1 = exp1.expand(sample_agent_seeds, target_count=5)
        r2 = exp2.expand(sample_agent_seeds, target_count=5)
        assert [s.agent_id for s in r1] == [s.agent_id for s in r2]

    def test_expand_empty_seeds(self, sample_extraction: ExtractionResult) -> None:
        graph = LocalGraph.build(sample_extraction)
        expander = AgentExpander(graph=graph, requirement="test", seed=42)
        result = expander.expand([], target_count=5)
        assert len(result) == 5
        assert all(s.origin == AgentOrigin.DERIVED for s in result)
