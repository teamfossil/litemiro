from __future__ import annotations

import random
import re

from litemiro.phase1.local_graph import LocalGraph
from litemiro.phase1.models import (
    AgentProfile,
    Edge,
    Entity,
    KeyRelationship,
    MemoryStore,
    SemanticMemory,
)

_FOLLOW_PROBS: dict[str, float] = {
    "WORKS_FOR": 1.0,
    "BELONGS_TO": 1.0,
    "COLLEAGUES": 0.8,
    "ALLIES": 0.8,
    "REPORTS_ON": 0.7,
    "COVERS": 0.7,
    "OPPOSES": 0.0,
    "RIVALS": 0.0,
}

_CONFLICT_TYPES = {"OPPOSES", "RIVALS"}
_ALLY_TYPES = {"COLLEAGUES", "ALLIES", "WORKS_FOR", "BELONGS_TO"}
_BIDIRECTIONAL_TYPES = {"COLLEAGUES", "ALLIES"}
_MAX_MEMORY_TOPICS = 3
_KEYWORD_RE = re.compile(r"\w+")
_ENGLISH_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "have",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "with",
}
_TOPIC_ATTRIBUTE_KEYS = {
    "beat",
    "category",
    "categories",
    "domain",
    "domains",
    "field",
    "fields",
    "keyword",
    "keywords",
    "sector",
    "sectors",
    "tag",
    "tags",
    "topic",
    "topics",
    "type",
}


class MemoryInitializer:
    def __init__(self, graph: LocalGraph, seed: int = 42) -> None:
        self._graph = graph
        self._rng = random.Random(seed)

    def initialize(self, agents: dict[str, AgentProfile]) -> dict[str, MemoryStore]:
        following = self._derive_initial_following(agents)
        for agent_id, following_list in following.items():
            if agent_id in agents:
                profile = agents[agent_id]
                merged = list(dict.fromkeys(profile.initial_following + following_list))
                merged = [fid for fid in merged if fid != agent_id]
                agents[agent_id] = profile.model_copy(update={"initial_following": merged})

        return {
            agent_id: MemoryStore(
                agent_id=agent_id,
                episodic=[],
                semantic=self._generate_seed_memories(profile, self._graph.entities.get(agent_id)),
            )
            for agent_id, profile in agents.items()
        }

    def _generate_seed_memories(
        self, agent: AgentProfile, entity: Entity | None
    ) -> list[SemanticMemory]:
        memories: list[SemanticMemory] = []
        seq = 0

        if entity and entity.summary:
            memories.append(
                SemanticMemory(
                    id=f"seed_{agent.agent_id}_{seq}",
                    summary=entity.summary,
                    topics=_derive_entity_topics(entity),
                    dominant_sentiment="중립",
                    key_relationships=[],
                )
            )
            seq += 1

        if entity:
            edges = sorted(
                self._graph.adjacency.get(entity.id, []),
                key=lambda e: e.weight,
                reverse=True,
            )[:3]

            for edge in edges:
                if seq >= 5:
                    break
                neighbor_id = edge.target if edge.source == entity.id else edge.source
                neighbor = self._graph.entities.get(neighbor_id)
                if neighbor is None:
                    continue

                neighbor_agent_id = _find_agent_for_entity(neighbor_id, self._graph)
                sentiment = _infer_sentiment(edge.type)
                memory_topics = _derive_relationship_topics(edge, neighbor)
                summary = f"{neighbor.name}와(과) {edge.description}"

                key_rels: list[KeyRelationship] = []
                if neighbor_agent_id:
                    key_rels.append(
                        KeyRelationship(
                            agent_id=neighbor_agent_id,
                            nature=_edge_type_to_nature(edge.type),
                        )
                    )

                memories.append(
                    SemanticMemory(
                        id=f"seed_{agent.agent_id}_{seq}",
                        summary=summary,
                        topics=memory_topics,
                        dominant_sentiment=sentiment,
                        key_relationships=key_rels,
                    )
                )
                seq += 1

        return memories[:5]

    def _derive_initial_following(self, agents: dict[str, AgentProfile]) -> dict[str, list[str]]:
        following: dict[str, list[str]] = {aid: [] for aid in agents}
        entity_to_agent: dict[str, str] = {
            aid: aid for aid in agents if aid in self._graph.entities
        }
        self._apply_graph_edges(agents, entity_to_agent, following)
        self._apply_derived_rules(agents, entity_to_agent, following)
        return following

    def _apply_graph_edges(
        self,
        agents: dict[str, AgentProfile],
        entity_to_agent: dict[str, str],
        following: dict[str, list[str]],
    ) -> None:
        for agent_id in agents:
            if agent_id not in self._graph.entities:
                continue
            for edge in self._graph.adjacency.get(agent_id, []):
                neighbor_id = edge.target if edge.source == agent_id else edge.source
                neighbor_agent_id = entity_to_agent.get(neighbor_id)
                if neighbor_agent_id is None or neighbor_agent_id == agent_id:
                    continue
                edge_type_upper = edge.type.upper()
                prob = _FOLLOW_PROBS.get(edge_type_upper)
                if prob is None or prob == 0.0:
                    continue
                if (prob == 1.0 or self._rng.random() < prob) and (
                    neighbor_agent_id not in following[agent_id]
                ):
                    following[agent_id].append(neighbor_agent_id)
                if edge_type_upper in _BIDIRECTIONAL_TYPES and (
                    self._rng.random() < 0.8 and agent_id not in following[neighbor_agent_id]
                ):
                    following[neighbor_agent_id].append(agent_id)

    def _apply_derived_rules(
        self,
        agents: dict[str, AgentProfile],
        entity_to_agent: dict[str, str],
        following: dict[str, list[str]],
    ) -> None:
        derived_ids = [aid for aid in agents if aid not in entity_to_agent]
        for agent_id in derived_ids:
            profile = agents[agent_id]
            for other_id, other_profile in agents.items():
                if other_id == agent_id:
                    continue
                if abs(profile.ideology - other_profile.ideology) < 0.2:
                    if self._rng.random() < 0.3 and other_id not in following[agent_id]:
                        following[agent_id].append(other_id)
                    if self._rng.random() < 0.3 and agent_id not in following[other_id]:
                        following[other_id].append(agent_id)
                elif _jaccard(profile.topics, other_profile.topics) > 0.4:
                    if self._rng.random() < 0.2 and other_id not in following[agent_id]:
                        following[agent_id].append(other_id)
                elif self._rng.random() < 0.02 and other_id not in following[agent_id]:
                    following[agent_id].append(other_id)


def _find_agent_for_entity(entity_id: str, graph: LocalGraph) -> str | None:
    return entity_id if entity_id in graph.entities else None


def _infer_sentiment(edge_type: str) -> str:
    upper = edge_type.upper()
    if upper in _CONFLICT_TYPES:
        return "갈등"
    if upper in _ALLY_TYPES:
        return "협력"
    return "중립"


def _edge_type_to_nature(edge_type: str) -> str:
    upper = edge_type.upper()
    if upper in _CONFLICT_TYPES:
        return "conflict"
    if upper in _ALLY_TYPES:
        return "agreement"
    return "neutral"


def _derive_entity_topics(entity: Entity) -> list[str]:
    return _dedupe_topics(
        [
            *_topics_from_attributes(entity.attributes),
            entity.type,
            *_keywords_from_text(entity.summary),
        ]
    )


def _derive_relationship_topics(edge: Edge, neighbor: Entity) -> list[str]:
    return _dedupe_topics(
        [
            *_topics_from_attributes(neighbor.attributes),
            neighbor.type,
            *_keywords_from_text(edge.description),
            *_keywords_from_text(neighbor.summary),
            edge.type,
        ]
    )


def _topics_from_attributes(attributes: dict[str, object]) -> list[str]:
    topics: list[str] = []
    for key, value in attributes.items():
        if key.lower() in _TOPIC_ATTRIBUTE_KEYS:
            topics.extend(_topic_values(value))
    return topics


def _topic_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple | set):
        topics: list[str] = []
        for item in value:
            topics.extend(_topic_values(item))
        return topics
    if isinstance(value, dict):
        nested_topics: list[str] = []
        for item in value.values():
            nested_topics.extend(_topic_values(item))
        return nested_topics
    return []


def _keywords_from_text(text: str) -> list[str]:
    return [token for token in _KEYWORD_RE.findall(text) if _is_topic_token(token)]


def _is_topic_token(token: str) -> bool:
    cleaned = token.strip("_")
    if len(cleaned) < 2 or cleaned.isdigit():
        return False
    return not (cleaned.isascii() and cleaned.casefold() in _ENGLISH_STOP_WORDS)


def _dedupe_topics(candidates: list[str]) -> list[str]:
    topics: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        topic = str(candidate).strip()
        if not topic:
            continue
        key = topic.casefold()
        if key in seen:
            continue
        seen.add(key)
        topics.append(topic)
        if len(topics) >= _MAX_MEMORY_TOPICS:
            break
    return topics


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    union = sa | sb
    if not union:
        return 0.0
    return len(sa & sb) / len(union)
