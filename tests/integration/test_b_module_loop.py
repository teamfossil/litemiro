"""Integration spec for B's module stack.

The unit suites pin each component in isolation. These tests prove
that the three B-owned components — ``ActionSelector`` (LLM call plus
fallback chain), ``FeedEngine`` (indexing, candidacy, ranking, and
engagement), and ``SocialGraph`` (follow graph) — compose correctly
under a minimal round-runner shim, with ``TopicExtractor`` bridging
``CREATE_POST`` content into the topic vocabulary the rest of the
stack consumes.

The shim mimics what A's ``RoundManager`` will do in production:
build a context from the live feed, drive the selector, apply the
returned action back into the engine / graph. It stays deliberately
thin — no concurrency, no token budget, no event log — so the focus
stays on B's seams. A/C surfaces stay out of scope.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from litemiro.action.selector import ActionSelector
from litemiro.feed.engine import FeedEngine
from litemiro.models import (
    Action,
    ActionContext,
    ActionResult,
    ActionType,
    Agent,
    LLMResponse,
    Post,
)
from litemiro.social.graph import SocialGraph
from litemiro.topics.extractor import TopicExtractor

pytestmark = pytest.mark.integration


# ---- shared test doubles -----------------------------------------------------


class _QueueLLM:
    """LLM driven by a pre-set response queue.

    Each round consumes one queued response. Strings are wrapped into
    ``LLMResponse(content=...)``; ``BaseException`` items are raised
    so a test can exercise the retry / fallback legs alongside the
    happy responses.
    """

    def __init__(self, *responses: str | LLMResponse | BaseException) -> None:
        self._queue: list[str | LLMResponse | BaseException] = list(responses)
        self.calls: list[tuple[str, str, str]] = []

    async def complete(self, *, system: str, user: str, model: str) -> LLMResponse:
        self.calls.append((system, user, model))
        if not self._queue:
            raise AssertionError("integration LLM queue exhausted")
        item = self._queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item if isinstance(item, LLMResponse) else LLMResponse(content=item)


class _AxisEmbedder:
    """Deterministic embedder mapping each vocab word to its own axis.

    The vector space has one dimension per registered word plus one
    reserved noise axis, so anything that doesn't substring-match a
    vocab word lands orthogonally and scores cosine zero against every
    vocab vector. Substring match is case-insensitive so prose like
    ``"politics talk for today"`` embeds onto the ``politics`` axis.
    """

    def __init__(self, *vocab: str) -> None:
        self._axes: dict[str, int] = {w: i for i, w in enumerate(vocab)}
        self._dim = len(vocab) + 1  # final axis = noise

    def embed(self, text: str) -> tuple[float, ...]:
        components = [0.0] * self._dim
        lowered = text.lower()
        matched = False
        for word, axis in self._axes.items():
            if word.lower() in lowered:
                components[axis] = 1.0
                matched = True
        if not matched:
            components[-1] = 1.0
        return tuple(components)


def _payload(action_type: ActionType, **fields: Any) -> str:
    body: dict[str, Any] = {"type": action_type.value}
    body.update({k: v for k, v in fields.items() if v is not None})
    return json.dumps(body)


# ---- minimal round-runner shim -----------------------------------------------


class _Runner:
    """Stand-in for the production ``RoundManager``.

    For each ``step`` it builds the context from the live feed and the
    social graph, drives the selector, and applies the returned action
    back into ``feed`` / ``social``. The action-to-effect mapping
    mirrors the Phase 2 design table: ``CREATE_POST`` indexes a new
    post (topics from the extractor); the three target-bearing actions
    increment the matching engagement counter via
    ``update_engagement``; ``FOLLOW`` adds an edge to the graph;
    ``DO_NOTHING`` is a no-op.
    """

    def __init__(
        self,
        *,
        feed: FeedEngine,
        social: SocialGraph,
        selector: ActionSelector,
        extractor: TopicExtractor | None = None,
        post_id_factory: Callable[[int, str], str] | None = None,
    ) -> None:
        self._feed = feed
        self._social = social
        self._selector = selector
        self._extractor = extractor
        self._mint = post_id_factory or (lambda r, aid: f"r{r}-{aid}")

    async def step(self, agent: Agent, *, round_num: int) -> ActionResult:
        candidates = self._feed.build_feed(agent=agent, current_round=round_num)
        ctx = ActionContext(
            agent=agent,
            feed=candidates,
            recent_actions=(),
            follower_count=self._social.follower_count(agent.agent_id),
            following_count=self._social.following_count(agent.agent_id),
            round_num=round_num,
        )
        result = await self._selector.select_action(agent.agent_id, ctx)
        self._apply(agent, candidates, result.action, round_num)
        return result

    def _apply(
        self,
        agent: Agent,
        feed_snapshot: tuple[Post, ...],
        action: Action,
        round_num: int,
    ) -> None:
        if action.type is ActionType.CREATE_POST and action.content:
            topics = self._extractor.extract(action.content) if self._extractor else ()
            self._feed.index_post(
                Post(
                    post_id=self._mint(round_num, agent.agent_id),
                    author_id=agent.agent_id,
                    content=action.content,
                    topics=topics,
                    created_round=round_num,
                )
            )
            return
        if action.type is ActionType.FOLLOW and action.target_agent_id:
            self._social.follow(agent.agent_id, action.target_agent_id)
            return
        counter_for = {
            ActionType.LIKE_POST: "likes",
            ActionType.REPOST: "reposts",
            ActionType.QUOTE_POST: "quotes",
        }
        counter = counter_for.get(action.type)
        if counter is None or action.target_post_id is None:
            return  # DO_NOTHING (or any action with no engagement effect)
        target = next(p for p in feed_snapshot if p.post_id == action.target_post_id)
        self._feed.update_engagement(
            target.model_copy(update={counter: getattr(target, counter) + 1})
        )


# ---- scenarios ---------------------------------------------------------------


async def test_create_post_flows_through_extractor_into_other_agent_feed() -> None:
    # A authors a CREATE_POST whose content matches the "politics"
    # vocab word; TopicExtractor must label the new post with that
    # topic so B — who is interested in "politics" but follows nobody
    # — picks it up on her very next ``build_feed``. This is the
    # round-runner glue path: selector → extractor → index_post →
    # build_feed.
    embedder = _AxisEmbedder("politics", "ai", "music")
    extractor = TopicExtractor(
        embedder=embedder,
        vocabulary=("politics", "ai", "music"),
        threshold=0.5,
    )
    social = SocialGraph()
    feed = FeedEngine(social=social)
    llm = _QueueLLM(_payload(ActionType.CREATE_POST, content="politics talk for today"))
    selector = ActionSelector(llm=llm, model="m")
    runner = _Runner(feed=feed, social=social, selector=selector, extractor=extractor)

    author = Agent(agent_id="A", interests=("politics",))
    reader = Agent(agent_id="B", interests=("politics",))

    result = await runner.step(author, round_num=0)
    assert result.action.type is ActionType.CREATE_POST
    assert result.llm_meta.fallback_used is False

    seen = feed.build_feed(agent=reader, current_round=1)
    assert [p.author_id for p in seen] == ["A"]
    assert "politics" in seen[0].topics


async def test_like_increments_engagement_and_reorders_next_round() -> None:
    # Two pre-existing posts with identical engagement → tie-break by
    # post_id ascending puts p1 first. After B LIKEs p2 through the
    # selector, the next round's feed must put p2 ahead (higher likes,
    # same age), which proves update_engagement actually mutated the
    # snapshot the next build_feed reads.
    social = SocialGraph()
    feed = FeedEngine(social=social)
    feed.index_post(
        Post(
            post_id="p1",
            author_id="A",
            content="ai post one",
            topics=("ai",),
            created_round=0,
            likes=4,
        )
    )
    feed.index_post(
        Post(
            post_id="p2",
            author_id="A",
            content="ai post two",
            topics=("ai",),
            created_round=0,
            likes=4,
        )
    )

    reader = Agent(agent_id="B", interests=("ai",))
    pre = feed.build_feed(agent=reader, current_round=1)
    assert [p.post_id for p in pre] == ["p1", "p2"]  # tie -> id ascending

    llm = _QueueLLM(_payload(ActionType.LIKE_POST, target_post_id="p2"))
    selector = ActionSelector(llm=llm, model="m")
    runner = _Runner(feed=feed, social=social, selector=selector)

    result = await runner.step(reader, round_num=1)
    assert result.action == Action(type=ActionType.LIKE_POST, target_post_id="p2")
    assert result.llm_meta.fallback_used is False

    after = feed.build_feed(agent=reader, current_round=2)
    assert [p.post_id for p in after] == ["p2", "p1"]
    assert {p.post_id: p.likes for p in after} == {"p2": 5, "p1": 4}


async def test_follow_path_admits_off_topic_post_to_feed() -> None:
    # Topic mismatch (politics vs. ai) excludes the post on its own,
    # but adding a follow edge makes it visible through the follow
    # candidacy path. This proves FeedEngine consults the live
    # SocialGraph rather than holding its own follow snapshot.
    social = SocialGraph()
    feed = FeedEngine(social=social)
    feed.index_post(
        Post(
            post_id="p1",
            author_id="A",
            content="x",
            topics=("politics",),
            created_round=0,
        )
    )

    reader = Agent(agent_id="B", interests=("ai",))
    assert feed.build_feed(agent=reader, current_round=1) == ()

    social.follow("B", "A")
    after = feed.build_feed(agent=reader, current_round=1)
    assert [p.post_id for p in after] == ["p1"]


async def test_target_hallucination_collapses_to_do_nothing_inside_round() -> None:
    # End-to-end fallback: even with a live FeedEngine in the loop, an
    # LLM-hallucinated target_post_id still collapses to ``DO_NOTHING``
    # with ``fallback_used=True``, and the engagement counter on the
    # real post must stay untouched.
    social = SocialGraph()
    feed = FeedEngine(social=social)
    feed.index_post(
        Post(
            post_id="p1",
            author_id="A",
            content="x",
            topics=("ai",),
            created_round=0,
        )
    )

    llm = _QueueLLM(_payload(ActionType.LIKE_POST, target_post_id="ghost"))
    selector = ActionSelector(llm=llm, model="m")
    runner = _Runner(feed=feed, social=social, selector=selector)

    reader = Agent(agent_id="B", interests=("ai",))
    result = await runner.step(reader, round_num=1)
    assert result.action == Action(type=ActionType.DO_NOTHING)
    assert result.llm_meta.fallback_used is True
    after = feed.build_feed(agent=reader, current_round=1)
    assert after[0].likes == 0
