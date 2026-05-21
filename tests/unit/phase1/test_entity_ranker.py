"""EntityRanker unit tests."""

from __future__ import annotations

from litemiro.phase1.entity_ranker import EntityRanker
from litemiro.phase1.local_graph import LocalGraph
from litemiro.phase1.models import ExtractionResult


class TestEntityRanker:
    def test_rank_returns_sorted(self, sample_extraction: ExtractionResult) -> None:
        graph = LocalGraph.build(sample_extraction)
        ranker = EntityRanker(graph=graph, simulation_requirement="AI 규제 정책")
        ranked = ranker.rank()
        assert len(ranked) == 3
        scores = [score for _, score in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_importance_nonnegative(self, sample_extraction: ExtractionResult) -> None:
        graph = LocalGraph.build(sample_extraction)
        ranker = EntityRanker(graph=graph, simulation_requirement="AI 규제")
        for entity in graph.entities.values():
            score = ranker.calculate_importance(entity)
            assert score >= 0.0

    def test_build_entity_context(self, sample_extraction: ExtractionResult) -> None:
        graph = LocalGraph.build(sample_extraction)
        ranker = EntityRanker(graph=graph, simulation_requirement="AI 규제")
        ctx = ranker.build_entity_context("journalist_kim")
        assert "김영수" in ctx
        assert "한겨레" in ctx or "WORKS_FOR" in ctx

    def test_rank_empty_graph(self) -> None:
        graph = LocalGraph.build(ExtractionResult())
        ranker = EntityRanker(graph=graph, simulation_requirement="test")
        ranked = ranker.rank()
        assert ranked == []
