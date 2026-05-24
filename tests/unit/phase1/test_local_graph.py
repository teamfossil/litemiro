"""LocalGraph unit tests."""

from __future__ import annotations

from litemiro.phase1.local_graph import LocalGraph
from litemiro.phase1.models import Edge, Entity, ExtractionResult


class TestLocalGraph:
    def test_build_from_extraction(self, sample_extraction: ExtractionResult) -> None:
        graph = LocalGraph.build(sample_extraction)
        assert len(graph.entities) == 3
        assert len(graph.edges) == 2
        assert "journalist_kim" in graph.adjacency
        assert "Journalist" in graph.entity_index

    def test_get_neighbors_depth_1(self, sample_extraction: ExtractionResult) -> None:
        graph = LocalGraph.build(sample_extraction)
        neighbors = graph.get_neighbors("journalist_kim", max_depth=1)
        neighbor_ids = {e.id for e in neighbors}
        assert "org_hankyoreh" in neighbor_ids
        assert "politician_park" in neighbor_ids

    def test_get_neighbors_unknown_id(self, sample_extraction: ExtractionResult) -> None:
        graph = LocalGraph.build(sample_extraction)
        neighbors = graph.get_neighbors("nonexistent")
        assert neighbors == []

    def test_get_context(self, sample_extraction: ExtractionResult) -> None:
        graph = LocalGraph.build(sample_extraction)
        ctx = graph.get_context("journalist_kim")
        assert "김영수" in ctx
        assert "Journalist" in ctx

    def test_get_context_unknown_id(self, sample_extraction: ExtractionResult) -> None:
        graph = LocalGraph.build(sample_extraction)
        ctx = graph.get_context("nonexistent")
        assert ctx == ""

    def test_merge_duplicates(self) -> None:
        result = ExtractionResult(
            entities=[
                Entity(id="kim_1", type="Journalist", name="김기자", summary="첫 번째"),
                Entity(id="kim_2", type="Journalist", name="김기자", summary="두 번째"),
                Entity(id="org_1", type="Organization", name="한겨레", summary="신문사"),
            ],
            relationships=[
                Edge(source="kim_1", target="org_1", type="WORKS_FOR", description="소속"),
                Edge(source="kim_2", target="org_1", type="WORKS_FOR", description="소속 기자"),
            ],
        )
        graph = LocalGraph.build(result)
        merged = graph.merge_duplicates()
        assert merged >= 1
        assert len(graph.entities) == 2

    def test_empty_extraction(self) -> None:
        graph = LocalGraph.build(ExtractionResult())
        assert len(graph.entities) == 0
        assert len(graph.edges) == 0

    def test_entity_index(self, sample_extraction: ExtractionResult) -> None:
        graph = LocalGraph.build(sample_extraction)
        assert "journalist_kim" in graph.entity_index.get("Journalist", [])
        assert "org_hankyoreh" in graph.entity_index.get("Organization", [])
