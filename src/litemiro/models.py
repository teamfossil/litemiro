"""Shared Pydantic v2 models — the contract between A/B/C owners.

The authoritative wire format is the JSON Schema at
``litemiro/schemas/round_event.schema.json``. The Python types here MUST
agree with that schema; ``tests/unit/test_models_schema_parity.py`` checks
that every model's ``model_dump(mode='json')`` validates clean against it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ActionType(StrEnum):
    """Agent actions exposed to the LLM and persisted in JSONL.

    Verbatim from the Phase 2 design doc — extending this enum requires
    updating ``round_event.schema.json`` and the Phase 3 ingest paths
    *first*.
    """

    CREATE_POST = "CREATE_POST"
    LIKE_POST = "LIKE_POST"
    REPOST = "REPOST"
    QUOTE_POST = "QUOTE_POST"
    FOLLOW = "FOLLOW"
    DO_NOTHING = "DO_NOTHING"


# _FROZEN drops ``strict`` so that values rehydrated from JSONL (where
# enums arrive as strings, ints as ints) parse cleanly. The wire-format
# JSON Schema is the strict gate. _STRICT keeps strict mode for engine-
# internal models (Post / Agent / ActionContext) that we always build
# directly in Python.
_FROZEN: ConfigDict = ConfigDict(extra="forbid", frozen=True)
_STRICT: ConfigDict = ConfigDict(extra="forbid", strict=True)


class Action(BaseModel):
    """One agent decision — produced by ``ActionSelector``."""

    model_config = _FROZEN

    type: ActionType
    target_post_id: str | None = None
    target_agent_id: str | None = None
    content: str | None = None

    @model_validator(mode="after")
    def _enforce_target_consistency(self) -> Action:
        t = self.type
        if t is ActionType.CREATE_POST and not self.content:
            raise ValueError("CREATE_POST requires non-empty content")
        if t is ActionType.QUOTE_POST and (not self.target_post_id or not self.content):
            raise ValueError("QUOTE_POST requires target_post_id and content")
        if t in (ActionType.LIKE_POST, ActionType.REPOST) and not self.target_post_id:
            raise ValueError(f"{t.value} requires target_post_id")
        if t is ActionType.FOLLOW and not self.target_agent_id:
            raise ValueError("FOLLOW requires target_agent_id")
        if t is ActionType.DO_NOTHING and (
            self.target_post_id or self.target_agent_id or self.content
        ):
            raise ValueError("DO_NOTHING must carry no payload")
        return self


class Post(BaseModel):
    model_config = _STRICT

    post_id: str
    author_id: str
    content: str
    topics: tuple[str, ...] = Field(default_factory=tuple)
    created_round: int = Field(ge=0)
    likes: int = Field(default=0, ge=0)
    reposts: int = Field(default=0, ge=0)
    quotes: int = Field(default=0, ge=0)
    quoted_post_id: str | None = None
    reposted_from: str | None = None

    def hot_score(self, current_round: int) -> float:
        """``(likes + 2*reposts + 3*quotes) / (age_in_rounds + 1)^1.5``.

        Verbatim from the design doc — *not* renormalised. Caller must
        pass a round number ≥ ``created_round``.
        """
        if current_round < self.created_round:
            raise ValueError(
                f"current_round={current_round} precedes created_round={self.created_round}"
            )
        age = current_round - self.created_round
        weighted = self.likes + 2 * self.reposts + 3 * self.quotes
        denominator: float = float((age + 1) ** 1.5)
        return weighted / denominator


class Agent(BaseModel):
    model_config = _STRICT

    agent_id: str
    interests: tuple[str, ...] = Field(default_factory=tuple)
    persona_traits: Mapping[str, Any] = Field(default_factory=dict)
    memory_summary: str | None = None
    activation_rate: float = Field(default=0.5, ge=0.0, le=1.0)


class ActionContext(BaseModel):
    """Inputs to ``ActionSelector.select_action``.

    Composition mirrors the design doc's four-part recipe: persona +
    memory + feed + recent action history.
    """

    model_config = _STRICT

    agent: Agent
    feed: tuple[Post, ...] = Field(default_factory=tuple)
    recent_actions: tuple[Action, ...] = Field(default_factory=tuple)
    follower_count: int = Field(default=0, ge=0)
    following_count: int = Field(default=0, ge=0)
    round_num: int = Field(ge=0)


class ContextSummary(BaseModel):
    """Subset of ``ActionContext`` persisted on each ``RoundEvent``."""

    model_config = _FROZEN

    feed_size: int = Field(ge=0)
    follower_count: int = Field(ge=0)
    following_count: int = Field(ge=0)


class LLMMeta(BaseModel):
    model_config = _FROZEN

    model: str
    tokens_used: int = Field(ge=0)
    latency_ms: float = Field(ge=0.0)
    fallback_used: bool = False


class LLMResponse(BaseModel):
    """Wire shape of one ``LLMClient.complete`` reply.

    Carries the raw content plus the prompt/completion token counts so
    ``ActionSelector`` can fill in :class:`LLMMeta.tokens_used` without
    a second roundtrip. Adapters that cannot get usage from their
    backend (e.g. some local fakes) leave the counts at zero.
    """

    model_config = _FROZEN

    content: str
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)


class ActionResult(BaseModel):
    """Composite return type from ``ActionSelector.select_action``.

    Phase 2 originally let ``select_action`` return a bare ``Action``,
    but the round runner needs the LLM accounting (tokens, latency,
    fallback flag) to populate :class:`RoundEvent.llm_meta`. Bundling
    them in one object keeps the call site to one ``await``.
    """

    model_config = _FROZEN

    action: Action
    llm_meta: LLMMeta


class RoundEvent(BaseModel):
    """One JSONL line — the Phase 2 → Phase 3 contract."""

    # extra="allow" so additive metadata (e.g. C's analytics tags) does
    # not break the schema. JSON Schema enforces named-field types; we
    # leave strict off here so ISO-8601 strings are parsed into datetime
    # when this model rehydrates JSONL written elsewhere.
    model_config = ConfigDict(extra="allow")

    round_num: int = Field(ge=0)
    timestamp: datetime
    agent_id: str
    action: Action
    context_summary: ContextSummary | None = None
    llm_meta: LLMMeta | None = None

    @field_validator("timestamp")
    @classmethod
    def _enforce_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware (use datetime.now(UTC))")
        return v

    def to_jsonl(self) -> str:
        """Serialise as one JSONL line (no trailing newline).

        ``exclude_none=True`` keeps optional blocks (``context_summary``,
        ``llm_meta``) absent rather than serialised as ``null`` so the
        JSON Schema's ``type: object`` typing on those fields is
        respected.
        """
        return json.dumps(
            self.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


__all__ = [
    "Action",
    "ActionContext",
    "ActionResult",
    "ActionType",
    "Agent",
    "ContextSummary",
    "LLMMeta",
    "LLMResponse",
    "Post",
    "RoundEvent",
]
