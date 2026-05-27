"""Phase 1 Pydantic models — the contract Phase 2 OntologyLoader consumes.

Two output files:
  ontology_a_persona.json  →  OntologyA
  ontology_b_memory.json   →  OntologyB

Internal pipeline models (Entity, Edge, LocalGraph, etc.) are also here
so every Phase 1 component shares a single source of truth.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_FROZEN: ConfigDict = ConfigDict(extra="forbid", frozen=True)
_STRICT: ConfigDict = ConfigDict(extra="forbid", strict=True)


# ── Ontology schema (Step 1 output) ──────────────────────────────────


class EntityTypeDef(BaseModel):
    model_config = _FROZEN

    name: str
    description: str
    attributes: list[str] = Field(default_factory=list)


class EdgeTypeDef(BaseModel):
    model_config = _FROZEN

    name: str
    source: str
    target: str
    description: str


class Ontology(BaseModel):
    model_config = _FROZEN

    entity_types: list[EntityTypeDef]
    edge_types: list[EdgeTypeDef]


# ── Internal pipeline models (Step 2-3) ──────────────────────────────


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: str
    name: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    source_chunks: list[int] = Field(default_factory=list)


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    type: str
    description: str = ""
    weight: float = Field(default=1.0, ge=0.0)


class TextChunk(BaseModel):
    model_config = _FROZEN

    index: int = Field(ge=0)
    text: str
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entities: list[Entity] = Field(default_factory=list)
    relationships: list[Edge] = Field(default_factory=list)


# ── Agent profile (Step 4 output) ────────────────────────────────────


class AgentOrigin(StrEnum):
    EXTRACTED = "extracted"
    DERIVED = "derived"


class BehaviorTendency(BaseModel):
    model_config = _FROZEN

    post_rate: float = Field(default=0.5, ge=0.0, le=1.0)
    reply_rate: float = Field(default=0.3, ge=0.0, le=1.0)
    repost_rate: float = Field(default=0.2, ge=0.0, le=1.0)
    like_rate: float = Field(default=0.4, ge=0.0, le=1.0)
    follow_rate: float = Field(default=0.2, ge=0.0, le=1.0)
    controversy_affinity: float = Field(default=0.5, ge=0.0, le=1.0)


class AgentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    name: str
    entity_type: str
    origin: AgentOrigin
    derived_from: str | None = None
    skeleton: dict[str, Any] = Field(default_factory=dict)
    ideology: float = Field(default=0.5, ge=0.0, le=1.0)
    topics: list[str] = Field(default_factory=list)
    sensitive_topics: list[str] = Field(default_factory=list)
    personality: str = ""
    speech_style: str = ""
    background: str = ""
    behavior_tendency: BehaviorTendency = Field(default_factory=BehaviorTendency)
    initial_following: list[str] = Field(default_factory=list)

    @field_validator("initial_following")
    @classmethod
    def _no_self_follow(cls, v: list[str], info: Any) -> list[str]:
        agent_id = info.data.get("agent_id")
        if agent_id and agent_id in v:
            return [fid for fid in v if fid != agent_id]
        return v


# ── Memory models (Step 5 output) ────────────────────────────────────


class KeyRelationship(BaseModel):
    model_config = _FROZEN

    agent_id: str
    nature: str  # conflict / agreement / neutral


class SemanticMemory(BaseModel):
    model_config = _FROZEN

    id: str
    summary: str
    topics: list[str] = Field(default_factory=list)
    dominant_sentiment: str = "중립"
    key_relationships: list[KeyRelationship] = Field(default_factory=list)
    simulation_count: int = Field(default=0, ge=0)
    last_relevant_sim: int = Field(default=0, ge=0)


class MemoryStore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    episodic: list[Any] = Field(default_factory=list)
    semantic: list[SemanticMemory] = Field(default_factory=list)


class MemoryConfig(BaseModel):
    model_config = _FROZEN

    episodic_max: int = Field(default=10, ge=1)
    semantic_max: int = Field(default=5, ge=1)
    episodic_decay_rate: float = Field(default=0.1, ge=0.0, le=1.0)
    semantic_decay_rate: float = Field(default=0.05, ge=0.0, le=1.0)
    retrieval_max: int = Field(default=3, ge=1)
    token_budget_per_agent: int = Field(default=120, ge=1)


# ── Top-level output schemas ─────────────────────────────────────────


class Preset(StrEnum):
    QUICK = "quick"
    STANDARD = "standard"
    FULL = "full"


PRESET_AGENT_COUNTS: dict[Preset, int] = {
    Preset.QUICK: 100,
    Preset.STANDARD: 300,
    Preset.FULL: 500,
}


class OntologyA(BaseModel):
    """ontology_a_persona.json — the Phase 1 → Phase 2 persona contract."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    seed: int
    agent_count: int = Field(ge=1)
    preset: Preset
    source_document: str
    simulation_requirement: str
    generated_at: datetime
    ontology: Ontology
    agents: dict[str, AgentProfile]

    @field_validator("generated_at")
    @classmethod
    def _enforce_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")
        return v


class OntologyB(BaseModel):
    """ontology_b_memory.json — the Phase 1 → Phase 2 memory contract."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    config: MemoryConfig = Field(default_factory=MemoryConfig)
    stores: dict[str, MemoryStore]


# ── Agent seed (internal, Step 4 input) ──────────────────────────────


class AgentSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    entity: Entity | None = None
    origin: AgentOrigin
    derived_from: str | None = None
    context: str = ""


__all__ = [
    "PRESET_AGENT_COUNTS",
    "AgentOrigin",
    "AgentProfile",
    "AgentSeed",
    "BehaviorTendency",
    "Edge",
    "EdgeTypeDef",
    "Entity",
    "EntityTypeDef",
    "ExtractionResult",
    "KeyRelationship",
    "MemoryConfig",
    "MemoryStore",
    "Ontology",
    "OntologyA",
    "OntologyB",
    "Preset",
    "SemanticMemory",
    "TextChunk",
]
