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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_FROZEN: ConfigDict = ConfigDict(extra="forbid", frozen=True)
_STRICT: ConfigDict = ConfigDict(extra="forbid", strict=True)


class PersonaMode(StrEnum):
    DIRECT_PERSON = "direct_person"
    REPRESENTATIVE = "representative"
    CONTEXT_ONLY = "context_only"


# ── Ontology schema (Step 1 output) ──────────────────────────────────


class EntityTypeDef(BaseModel):
    model_config = _FROZEN

    name: str
    description: str
    attributes: list[str] = Field(default_factory=list)
    persona_mode: PersonaMode | None = None


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


class ActorKind(StrEnum):
    DIRECT_PERSON = "direct_person"
    REPRESENTATIVE = "representative"


def actor_kind_from_persona_mode(mode: PersonaMode) -> ActorKind | None:
    """Map ontology persona classification to generated agent actor metadata."""
    if mode == PersonaMode.CONTEXT_ONLY:
        return None
    if mode == PersonaMode.REPRESENTATIVE:
        return ActorKind.REPRESENTATIVE
    return ActorKind.DIRECT_PERSON


class StanceBucket(StrEnum):
    CRITICAL = "critical"
    NEUTRAL = "neutral"
    SUPPORTIVE = "supportive"


# (bucket, quota ratio, seed target value). Derived citizens receive one of
# these targets before profile generation so the prompt can preserve topic
# attitude independently from progressive-conservative ideology.
STANCE_QUOTA: tuple[tuple[StanceBucket, float, float], ...] = (
    (StanceBucket.CRITICAL, 0.3, 0.2),
    (StanceBucket.NEUTRAL, 0.4, 0.5),
    (StanceBucket.SUPPORTIVE, 0.3, 0.8),
)
# Validator tolerance is intentionally wider than the largest remainder rounding
# drift so LLM variation warns on real stance skew, not on small preset sizes.
STANCE_DISTRIBUTION_TOLERANCE = 0.15
STANCE_DISTRIBUTION_MIN_DERIVED = 10
STANCE_CRITICAL_MAX = 0.4
STANCE_SUPPORTIVE_MIN = 0.6


def stance_bucket(value: float) -> StanceBucket:
    if value < STANCE_CRITICAL_MAX:
        return StanceBucket.CRITICAL
    if value > STANCE_SUPPORTIVE_MIN:
        return StanceBucket.SUPPORTIVE
    return StanceBucket.NEUTRAL


class BehaviorTendency(BaseModel):
    model_config = _FROZEN

    post_rate: float = Field(default=0.5, ge=0.0, le=1.0)
    reply_rate: float = Field(default=0.3, ge=0.0, le=1.0)
    repost_rate: float = Field(default=0.35, ge=0.0, le=1.0)
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
    actor_kind: ActorKind = ActorKind.DIRECT_PERSON
    represented_entity_id: str | None = None
    skeleton: dict[str, Any] = Field(default_factory=dict)
    ideology: float = Field(default=0.5, ge=0.0, le=1.0)
    stance: float = Field(default=0.5, ge=0.0, le=1.0)
    topics: list[str] = Field(default_factory=list)
    sensitive_topics: list[str] = Field(default_factory=list)
    personality: str = ""
    speech_style: str = ""
    background: str = ""
    behavior_tendency: BehaviorTendency = Field(default_factory=BehaviorTendency)
    initial_following: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _default_stance_from_legacy_ideology(cls, data: Any) -> Any:
        """Keep ontology files created before stance existed readable."""
        if isinstance(data, dict) and "stance" not in data and "ideology" in data:
            return {**data, "stance": data["ideology"]}
        return data

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
    actor_kind: ActorKind = ActorKind.DIRECT_PERSON
    represented_entity_id: str | None = None
    context: str = ""
    stance_target: float | None = Field(default=None, ge=0.0, le=1.0)


__all__ = [
    "PRESET_AGENT_COUNTS",
    "STANCE_CRITICAL_MAX",
    "STANCE_DISTRIBUTION_MIN_DERIVED",
    "STANCE_DISTRIBUTION_TOLERANCE",
    "STANCE_QUOTA",
    "STANCE_SUPPORTIVE_MIN",
    "ActorKind",
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
    "PersonaMode",
    "Preset",
    "SemanticMemory",
    "StanceBucket",
    "TextChunk",
    "actor_kind_from_persona_mode",
    "stance_bucket",
]
