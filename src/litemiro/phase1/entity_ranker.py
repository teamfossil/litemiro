from __future__ import annotations

from litemiro.phase1.local_graph import LocalGraph
from litemiro.phase1.models import Edge, Entity


class EntityRanker:
    def __init__(self, graph: LocalGraph, simulation_requirement: str) -> None:
        self._graph = graph
        self._requirement = simulation_requirement

    def rank(self) -> list[tuple[Entity, float]]:
        entities = list(self._graph.entities.values())
        if not entities:
            return []
        scores = [(e, self.calculate_importance(e)) for e in entities]
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores

    def calculate_importance(self, entity: Entity) -> float:
        entities = list(self._graph.entities.values())
        degrees = [len(self._graph.adjacency.get(e.id, [])) for e in entities]
        mentions = [len(e.source_chunks) for e in entities]

        degree = len(self._graph.adjacency.get(entity.id, []))
        mention_count = len(entity.source_chunks)
        relevance = _keyword_overlap(entity.summary, self._requirement)

        norm_degree = _normalize(degree, degrees)
        norm_mention = _normalize(mention_count, mentions)

        return 0.4 * norm_degree + 0.3 * norm_mention + 0.3 * relevance

    def build_entity_context(self, entity_id: str) -> str:
        entity = self._graph.entities.get(entity_id)
        if entity is None:
            return ""

        lines: list[str] = [
            f"이름: {entity.name}",
            f"유형: {entity.type}",
            f"요약: {entity.summary}",
        ]

        if entity.attributes:
            lines.append("속성:")
            for k, v in entity.attributes.items():
                lines.append(f"  {k}: {v}")

        edges: list[Edge] = self._graph.adjacency.get(entity_id, [])[:10]
        if edges:
            lines.append("관련 관계:")
            for edge in edges:
                neighbor_id = edge.target if edge.source == entity_id else edge.source
                neighbor = self._graph.entities.get(neighbor_id)
                neighbor_summary = neighbor.summary[:50] if neighbor else ""
                lines.append(f"  [{edge.type}] {neighbor_id}: {neighbor_summary}")

        neighbors = self._graph.get_neighbors(entity_id)
        if neighbors:
            lines.append("이웃 엔티티:")
            for n in neighbors[:10]:
                lines.append(f"  {n.name} ({n.type}): {n.summary[:50]}")

        return "\n".join(lines)


def _normalize(value: float, all_values: list[float]) -> float:
    min_v = min(all_values, default=0.0)
    max_v = max(all_values, default=0.0)
    if max_v == min_v:
        return 0.0
    return (value - min_v) / (max_v - min_v)


def _keyword_overlap(text: str, requirement: str) -> float:
    if not text or not requirement:
        return 0.0
    text_words = set(text.lower().split())
    req_words = set(requirement.lower().split())
    if not req_words:
        return 0.0
    return len(text_words & req_words) / len(req_words)
