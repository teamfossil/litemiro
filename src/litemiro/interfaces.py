"""Owner-boundary Protocols.

These let B's modules be unit-tested with in-memory fakes for A/C
surfaces, and document the contract that A/C implementations must keep.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable

from litemiro.models import ActionContext, ActionResult, Agent, LLMResponse, Post, RoundEvent


@runtime_checkable
class LLMClient(Protocol):
    """Async LLM contract used by ``ActionSelector``.

    Implementations: real (litellm/openai over OpenRouter) and a
    deterministic fake used in tests (``tests/conftest.py``).
    """

    async def complete(self, *, system: str, user: str, model: str) -> LLMResponse: ...


@runtime_checkable
class SocialGraphLike(Protocol):
    """Owned by **B**.

    Mutating methods are expected to be idempotent.
    """

    def follow(self, follower: str, followee: str) -> None: ...
    def unfollow(self, follower: str, followee: str) -> None: ...
    def followers(self, agent_id: str) -> frozenset[str]: ...
    def following(self, agent_id: str) -> frozenset[str]: ...
    def follower_count(self, agent_id: str) -> int: ...
    def following_count(self, agent_id: str) -> int: ...
    def to_dict(self) -> Mapping[str, list[str]]: ...


@runtime_checkable
class FeedEngineLike(Protocol):
    """Owned by **B**."""

    def index_post(self, post: Post) -> None: ...
    def remove_post(self, post_id: str) -> None: ...
    def update_engagement(self, post: Post) -> None: ...
    def build_feed(
        self, *, agent: Agent, current_round: int, limit: int = 20
    ) -> tuple[Post, ...]: ...


@runtime_checkable
class EmbedderLike(Protocol):
    """Topic embedding contract used by ``FeedEngine``.

    Notion Section 3.2 says interest-based candidacy uses
    "sentence-transformers 임베딩 유사도". The Protocol stays
    framework-agnostic so the unit suite can drive ``FeedEngine`` with
    a deterministic fake; the real ``sentence-transformers`` adapter is
    wired in at integration time (W3).
    """

    def embed(self, text: str) -> tuple[float, ...]: ...


@runtime_checkable
class TopicExtractorLike(Protocol):
    """Maps free-form post content to a tuple of topic labels.

    Used at CREATE_POST time so a freshly authored ``Post`` carries the
    same topic vocabulary that ``FeedEngine`` and ``Agent.interests``
    speak. Implementations are framework-agnostic; the unit suite drives
    a deterministic fake while the W3 integration uses the same
    ``EmbedderLike`` adapter as ``FeedEngine``.
    """

    def extract(self, content: str) -> tuple[str, ...]: ...


@runtime_checkable
class ActionSelectorLike(Protocol):
    """Owned by **B**."""

    async def select_action(self, agent_id: str, context: ActionContext) -> ActionResult: ...


@runtime_checkable
class StateStoreLike(Protocol):
    """Owned by **A** — B reads/writes via this Protocol."""

    def get_agent(self, agent_id: str) -> Agent: ...
    def list_agent_ids(self) -> tuple[str, ...]: ...
    def get_post(self, post_id: str) -> Post: ...
    def list_posts(self) -> tuple[Post, ...]: ...
    def add_post(self, post: Post) -> None: ...
    def replace_post(self, post: Post) -> None: ...
    def get_random_seed(self, agent_id: str) -> int: ...
    async def save_checkpoint(self, round_num: int) -> Path: ...


@runtime_checkable
class EventLoggerLike(Protocol):
    """Owned by **C** — B emits via this Protocol."""

    async def log_event(self, event: RoundEvent) -> None: ...
    async def aclose(self) -> None: ...


@runtime_checkable
class TokenBudgetManagerLike(Protocol):
    """Owned by **C** — A reads / consumes via this Protocol."""

    def has_budget(self, *, estimated_tokens: int) -> bool: ...
    def consume(self, *, tokens_used: int) -> None: ...
    def remaining(self) -> int: ...


__all__ = [
    "ActionSelectorLike",
    "EmbedderLike",
    "EventLoggerLike",
    "FeedEngineLike",
    "LLMClient",
    "SocialGraphLike",
    "StateStoreLike",
    "TokenBudgetManagerLike",
    "TopicExtractorLike",
]
