from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher

from litemiro.phase1.models import Edge, Entity, ExtractionResult

_SIMILARITY_THRESHOLD = 0.8
_CONTEXT_MAX_EDGES = 10


@dataclass
class LocalGraph:
    entities: dict[str, Entity]
    edges: list[Edge]
    adjacency: dict[str, list[Edge]]
    entity_index: dict[str, list[str]]

    @classmethod
    def build(cls, result: ExtractionResult) -> LocalGraph:
        entities: dict[str, Entity] = {e.id: e for e in result.entities}
        edges: list[Edge] = list(result.relationships)

        adjacency: dict[str, list[Edge]] = {eid: [] for eid in entities}
        for edge in edges:
            if edge.source in adjacency:
                adjacency[edge.source].append(edge)
            if edge.target in adjacency:
                adjacency[edge.target].append(edge)

        entity_index: dict[str, list[str]] = {}
        for entity in entities.values():
            entity_index.setdefault(entity.type, []).append(entity.id)

        return cls(
            entities=entities,
            edges=edges,
            adjacency=adjacency,
            entity_index=entity_index,
        )

    def get_neighbors(self, entity_id: str, max_depth: int = 1) -> list[Entity]:
        visited: set[str] = {entity_id}
        queue: deque[tuple[str, int]] = deque([(entity_id, 0)])
        neighbors: list[Entity] = []

        while queue:
            current_id, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for edge in self.adjacency.get(current_id, []):
                neighbor_id = edge.target if edge.source == current_id else edge.source
                if neighbor_id not in visited and neighbor_id in self.entities:
                    visited.add(neighbor_id)
                    neighbors.append(self.entities[neighbor_id])
                    queue.append((neighbor_id, depth + 1))

        return neighbors

    def get_context(self, entity_id: str) -> str:
        entity = self.entities.get(entity_id)
        if entity is None:
            return ""

        lines: list[str] = [
            f"이름:{entity.name}",
            f"유형:{entity.type}",
            f"요약:{entity.summary}",
            "관련 관계:",
        ]

        connected_edges = self.adjacency.get(entity_id, [])[:_CONTEXT_MAX_EDGES]
        for edge in connected_edges:
            neighbor_id = edge.target if edge.source == entity_id else edge.source
            neighbor = self.entities.get(neighbor_id)
            neighbor_summary = neighbor.summary if neighbor else ""
            lines.append(f"  [{edge.type}] {neighbor_id}: {neighbor_summary}")

        return "\n".join(lines)

    def merge_duplicates(self) -> int:
        merge_count = 0

        # Rule 1: same name + same type -> auto-merge (keep last attributes)
        # Group by (name, type)
        groups: dict[tuple[str, str], list[str]] = {}
        for eid, entity in self.entities.items():
            key = (entity.name, entity.type)
            groups.setdefault(key, []).append(eid)

        id_remap: dict[str, str] = {}
        for (_name, _etype), eids in groups.items():
            if len(eids) < 2:
                continue
            canonical_id = eids[0]
            for duplicate_id in eids[1:]:
                duplicate = self.entities[duplicate_id]
                canonical = self.entities[canonical_id]
                # keep last attributes (duplicate wins)
                merged = Entity(
                    id=canonical_id,
                    type=canonical.type,
                    name=canonical.name,
                    attributes={**canonical.attributes, **duplicate.attributes},
                    summary=duplicate.summary or canonical.summary,
                    source_chunks=sorted(set(canonical.source_chunks + duplicate.source_chunks)),
                )
                self.entities[canonical_id] = merged
                del self.entities[duplicate_id]
                id_remap[duplicate_id] = canonical_id
                merge_count += 1

        # Rule 2: similar name + same type (similarity > 0.8) -> flag as candidate
        # (no auto-merge; just log)
        entity_list = list(self.entities.values())
        for i, a in enumerate(entity_list):
            for b in entity_list[i + 1 :]:
                if a.type != b.type:
                    continue
                ratio = SequenceMatcher(None, a.name, b.name).ratio()
                if ratio > _SIMILARITY_THRESHOLD:
                    # Rule 2: flag only, do not merge
                    pass  # caller may inspect via a separate query

        # Rule 3: same name + different type -> keep separate (no action needed)

        if id_remap:
            self._rebuild_after_merge(id_remap)

        return merge_count

    def _rebuild_after_merge(self, id_remap: dict[str, str]) -> None:
        updated_edges: list[Edge] = []
        for edge in self.edges:
            new_source = id_remap.get(edge.source, edge.source)
            new_target = id_remap.get(edge.target, edge.target)
            if new_source == new_target:
                continue
            updated_edges.append(
                Edge(
                    source=new_source,
                    target=new_target,
                    type=edge.type,
                    description=edge.description,
                    weight=edge.weight,
                )
            )
        self.edges[:] = updated_edges

        self.adjacency.clear()
        for eid in self.entities:
            self.adjacency[eid] = []
        for edge in self.edges:
            if edge.source in self.adjacency:
                self.adjacency[edge.source].append(edge)
            if edge.target in self.adjacency:
                self.adjacency[edge.target].append(edge)

        self.entity_index.clear()
        for entity in self.entities.values():
            self.entity_index.setdefault(entity.type, []).append(entity.id)
