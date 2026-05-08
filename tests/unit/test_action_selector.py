"""TDD spec for ``litemiro.action.selector.ActionSelector``.

Notion §3.3 / §3.4 only stipulate "ActionSelector → LLM → Action" and a
prompt recipe of "persona + memory + feed + recent". B locks the
contract here:

* **Inputs**: ``agent_id`` and ``ActionContext`` (persona, feed,
  recent_actions, follower/following counts, current round).
* **Output**: a validated ``Action``. ``select_action`` *never* raises
  — a flaky LLM cannot derail the round; on any failure path the call
  collapses to ``Action(type=DO_NOTHING)``.
* **3-step fallback** in this order:
    1. tenacity-style retry on *transport* errors raised by ``LLMClient``
       (``max_attempts`` defaults to 3).
    2. ``json_repair`` rescues malformed JSON before validation.
    3. ``DO_NOTHING`` if (a) retries exhaust, (b) JSON cannot be
       repaired, (c) the response fails ``Action`` validation, or
       (d) target validation rejects an LLM-hallucinated id.
* **Target visibility**: ``target_post_id`` must reference a post that
  is in ``context.feed``; for ``FOLLOW``, ``target_agent_id`` must be a
  *feed author* (the only set of agents the actor has visibility into).
  Anything else collapses to ``DO_NOTHING``.
* **Prompts**: system prompt carries the persona card (id, interests,
  traits, memory). User prompt carries feed entries, recent actions,
  follower/following counts, current round, and a JSON-only output
  instruction.
"""

from __future__ import annotations

import json
from typing import Any

from litemiro.action.selector import ActionSelector
from litemiro.interfaces import ActionSelectorLike, LLMClient
from litemiro.models import Action, ActionContext, ActionType, Agent, Post


def _agent(
    agent_id: str = "me",
    *,
    interests: tuple[str, ...] = ("ai",),
    persona_traits: dict[str, Any] | None = None,
    memory_summary: str | None = None,
) -> Agent:
    return Agent(
        agent_id=agent_id,
        interests=interests,
        persona_traits=persona_traits or {"tone": "curious"},
        memory_summary=memory_summary,
    )


def _post(post_id: str, author: str = "alice", content: str = "hello ai") -> Post:
    return Post(
        post_id=post_id,
        author_id=author,
        content=content,
        topics=("ai",),
        created_round=0,
    )


def _ctx(
    *,
    agent: Agent | None = None,
    feed: tuple[Post, ...] = (),
    recent_actions: tuple[Action, ...] = (),
    round_num: int = 1,
    follower_count: int = 0,
    following_count: int = 0,
) -> ActionContext:
    return ActionContext(
        agent=agent or _agent(),
        feed=feed,
        recent_actions=recent_actions,
        follower_count=follower_count,
        following_count=following_count,
        round_num=round_num,
    )


def _payload(action_type: ActionType, **fields: Any) -> str:
    body: dict[str, Any] = {"type": action_type.value}
    body.update({k: v for k, v in fields.items() if v is not None})
    return json.dumps(body)


class _FakeLLM:
    """Test-local fake — replays a queue that may include exceptions.

    Unlike ``conftest._FakeLLMClient`` this lets a single test interleave
    transport failures with successful responses, which is what the
    retry/fallback tests need.
    """

    def __init__(self, *responses: str | BaseException) -> None:
        self._queue: list[str | BaseException] = list(responses)
        self.calls: list[tuple[str, str, str]] = []

    async def complete(self, *, system: str, user: str, model: str) -> str:
        self.calls.append((system, user, model))
        if not self._queue:
            raise AssertionError("FakeLLM exhausted: tests should pre-queue all responses")
        item = self._queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _selector(llm: LLMClient, *, max_attempts: int = 3) -> ActionSelector:
    return ActionSelector(llm=llm, model="test-model", max_attempts=max_attempts)


class TestHappyPath:
    async def test_returns_parsed_like_action(self) -> None:
        feed = (_post("p1"),)
        llm = _FakeLLM(_payload(ActionType.LIKE_POST, target_post_id="p1"))
        action = await _selector(llm).select_action("me", _ctx(feed=feed))
        assert action == Action(type=ActionType.LIKE_POST, target_post_id="p1")

    async def test_returns_do_nothing_when_llm_says_so(self) -> None:
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        action = await _selector(llm).select_action("me", _ctx())
        assert action == Action(type=ActionType.DO_NOTHING)

    async def test_passes_model_through_to_llm(self) -> None:
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        await _selector(llm).select_action("me", _ctx())
        assert llm.calls[0][2] == "test-model"


class TestActionTypes:
    """Cover every ``ActionType`` end-to-end."""

    async def test_create_post(self) -> None:
        llm = _FakeLLM(_payload(ActionType.CREATE_POST, content="my new post"))
        action = await _selector(llm).select_action("me", _ctx())
        assert action == Action(type=ActionType.CREATE_POST, content="my new post")

    async def test_like_post(self) -> None:
        feed = (_post("p1"),)
        llm = _FakeLLM(_payload(ActionType.LIKE_POST, target_post_id="p1"))
        action = await _selector(llm).select_action("me", _ctx(feed=feed))
        assert action.type is ActionType.LIKE_POST
        assert action.target_post_id == "p1"

    async def test_repost(self) -> None:
        feed = (_post("p1"),)
        llm = _FakeLLM(_payload(ActionType.REPOST, target_post_id="p1"))
        action = await _selector(llm).select_action("me", _ctx(feed=feed))
        assert action.type is ActionType.REPOST
        assert action.target_post_id == "p1"

    async def test_quote_post(self) -> None:
        feed = (_post("p1"),)
        llm = _FakeLLM(_payload(ActionType.QUOTE_POST, target_post_id="p1", content="my quote"))
        action = await _selector(llm).select_action("me", _ctx(feed=feed))
        assert action.type is ActionType.QUOTE_POST
        assert action.target_post_id == "p1"
        assert action.content == "my quote"

    async def test_follow(self) -> None:
        feed = (_post("p1", author="alice"),)
        llm = _FakeLLM(_payload(ActionType.FOLLOW, target_agent_id="alice"))
        action = await _selector(llm).select_action("me", _ctx(feed=feed))
        assert action.type is ActionType.FOLLOW
        assert action.target_agent_id == "alice"

    async def test_do_nothing(self) -> None:
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        action = await _selector(llm).select_action("me", _ctx())
        assert action.type is ActionType.DO_NOTHING


class TestRetry:
    async def test_retries_on_transient_failure_then_succeeds(self) -> None:
        feed = (_post("p1"),)
        llm = _FakeLLM(
            RuntimeError("connection reset"),
            RuntimeError("connection reset"),
            _payload(ActionType.LIKE_POST, target_post_id="p1"),
        )
        action = await _selector(llm, max_attempts=3).select_action("me", _ctx(feed=feed))
        assert action.type is ActionType.LIKE_POST
        assert len(llm.calls) == 3

    async def test_exhausted_retries_returns_do_nothing(self) -> None:
        llm = _FakeLLM(
            RuntimeError("boom"),
            RuntimeError("boom"),
            RuntimeError("boom"),
        )
        action = await _selector(llm, max_attempts=3).select_action("me", _ctx())
        assert action == Action(type=ActionType.DO_NOTHING)
        assert len(llm.calls) == 3

    async def test_does_not_retry_on_success(self) -> None:
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        await _selector(llm, max_attempts=3).select_action("me", _ctx())
        assert len(llm.calls) == 1


class TestJsonRepair:
    async def test_repairs_trailing_comma(self) -> None:
        feed = (_post("p1"),)
        # json.loads chokes on trailing commas; json_repair rescues it.
        llm = _FakeLLM('{"type": "LIKE_POST", "target_post_id": "p1",}')
        action = await _selector(llm).select_action("me", _ctx(feed=feed))
        assert action == Action(type=ActionType.LIKE_POST, target_post_id="p1")

    async def test_repairs_python_style_quotes(self) -> None:
        feed = (_post("p1"),)
        llm = _FakeLLM("{'type': 'LIKE_POST', 'target_post_id': 'p1'}")
        action = await _selector(llm).select_action("me", _ctx(feed=feed))
        assert action == Action(type=ActionType.LIKE_POST, target_post_id="p1")

    async def test_unrepairable_garbage_falls_back(self) -> None:
        llm = _FakeLLM("not json at all <<<>>>")
        action = await _selector(llm).select_action("me", _ctx())
        assert action == Action(type=ActionType.DO_NOTHING)


class TestTargetValidation:
    async def test_unknown_post_id_falls_back(self) -> None:
        feed = (_post("p1"),)
        llm = _FakeLLM(_payload(ActionType.LIKE_POST, target_post_id="ghost"))
        action = await _selector(llm).select_action("me", _ctx(feed=feed))
        assert action == Action(type=ActionType.DO_NOTHING)

    async def test_unknown_repost_target_falls_back(self) -> None:
        feed = (_post("p1"),)
        llm = _FakeLLM(_payload(ActionType.REPOST, target_post_id="ghost"))
        action = await _selector(llm).select_action("me", _ctx(feed=feed))
        assert action == Action(type=ActionType.DO_NOTHING)

    async def test_unknown_quote_target_falls_back(self) -> None:
        feed = (_post("p1"),)
        llm = _FakeLLM(_payload(ActionType.QUOTE_POST, target_post_id="ghost", content="hi"))
        action = await _selector(llm).select_action("me", _ctx(feed=feed))
        assert action == Action(type=ActionType.DO_NOTHING)

    async def test_unknown_follow_target_falls_back(self) -> None:
        feed = (_post("p1", author="alice"),)
        llm = _FakeLLM(_payload(ActionType.FOLLOW, target_agent_id="bob"))
        action = await _selector(llm).select_action("me", _ctx(feed=feed))
        assert action == Action(type=ActionType.DO_NOTHING)

    async def test_create_post_skips_target_validation(self) -> None:
        # CREATE_POST has no target → empty feed must not block it.
        llm = _FakeLLM(_payload(ActionType.CREATE_POST, content="fresh"))
        action = await _selector(llm).select_action("me", _ctx())
        assert action.type is ActionType.CREATE_POST
        assert action.content == "fresh"

    async def test_self_target_for_like_falls_back(self) -> None:
        # An agent must not like its own post — even if it appears in
        # the feed via a misconfigured FeedEngine.
        feed = (_post("p1", author="me"),)
        llm = _FakeLLM(_payload(ActionType.LIKE_POST, target_post_id="p1"))
        action = await _selector(llm).select_action("me", _ctx(feed=feed))
        assert action == Action(type=ActionType.DO_NOTHING)

    async def test_self_follow_falls_back(self) -> None:
        feed = (_post("p1", author="me"),)
        llm = _FakeLLM(_payload(ActionType.FOLLOW, target_agent_id="me"))
        action = await _selector(llm).select_action("me", _ctx(feed=feed))
        assert action == Action(type=ActionType.DO_NOTHING)


class TestPydanticValidation:
    async def test_invalid_action_type_falls_back(self) -> None:
        llm = _FakeLLM('{"type": "UNFOLLOW"}')
        action = await _selector(llm).select_action("me", _ctx())
        assert action == Action(type=ActionType.DO_NOTHING)

    async def test_create_post_without_content_falls_back(self) -> None:
        llm = _FakeLLM('{"type": "CREATE_POST"}')
        action = await _selector(llm).select_action("me", _ctx())
        assert action == Action(type=ActionType.DO_NOTHING)

    async def test_do_nothing_with_payload_falls_back(self) -> None:
        llm = _FakeLLM('{"type": "DO_NOTHING", "content": "leaked"}')
        action = await _selector(llm).select_action("me", _ctx())
        assert action == Action(type=ActionType.DO_NOTHING)


class TestPromptComposition:
    async def test_system_prompt_includes_persona_card(self) -> None:
        agent = _agent(
            "me",
            interests=("ai", "music"),
            persona_traits={"tone": "skeptical"},
            memory_summary="last round felt repetitive",
        )
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        await _selector(llm).select_action("me", _ctx(agent=agent))
        system = llm.calls[0][0]
        assert "me" in system
        assert "ai" in system
        assert "music" in system
        assert "skeptical" in system
        assert "repetitive" in system

    async def test_user_prompt_includes_feed_entries(self) -> None:
        feed = (
            _post("p-aaa", author="alice", content="ai is cool"),
            _post("p-bbb", author="bob", content="music too"),
        )
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        await _selector(llm).select_action("me", _ctx(feed=feed))
        user = llm.calls[0][1]
        assert "p-aaa" in user
        assert "p-bbb" in user
        assert "alice" in user
        assert "bob" in user

    async def test_user_prompt_includes_recent_actions(self) -> None:
        recent = (
            Action(type=ActionType.LIKE_POST, target_post_id="p-old"),
            Action(type=ActionType.DO_NOTHING),
        )
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        await _selector(llm).select_action("me", _ctx(recent_actions=recent))
        user = llm.calls[0][1]
        assert "LIKE_POST" in user
        assert "p-old" in user
        assert "DO_NOTHING" in user

    async def test_user_prompt_includes_round_and_counts(self) -> None:
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        await _selector(llm).select_action(
            "me", _ctx(round_num=42, follower_count=7, following_count=11)
        )
        user = llm.calls[0][1]
        assert "42" in user
        assert "7" in user
        assert "11" in user

    async def test_system_prompt_documents_action_schema(self) -> None:
        # All six action types must appear in the system prompt so the
        # LLM knows its full vocabulary.
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        await _selector(llm).select_action("me", _ctx())
        system = llm.calls[0][0]
        for at in ActionType:
            assert at.value in system


class TestPhase1PersonaSchema:
    """Phase 1 (dual-ontology) freezes ten persona keys; the prompt layer
    hoists the well-known keys to predictable positions and renders the
    behavior weights and the sensitive-topic list as explicit hints."""

    async def test_phase1_keys_surface_in_persona_card(self) -> None:
        agent = _agent(
            "me",
            persona_traits={
                "name": "이태우",
                "entity_type": "individual",
                "personality": "curious_skeptic",
                "speech_style": "casual",
                "background": "30대 직장인",
                "ideology": "moderate_left",
            },
        )
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        await _selector(llm).select_action("me", _ctx(agent=agent))
        system = llm.calls[0][0]
        for value in (
            "이태우",
            "individual",
            "curious_skeptic",
            "casual",
            "30대 직장인",
            "moderate_left",
        ):
            assert value in system

    async def test_behavior_tendency_renders_as_natural_language(self) -> None:
        agent = _agent(
            "me",
            persona_traits={
                "behavior_tendency": {
                    "post_rate": 0.7,
                    "reply_rate": 0.5,
                    "repost_rate": 0.1,
                    "controversy_affinity": 0.2,
                },
            },
        )
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        await _selector(llm).select_action("me", _ctx(agent=agent))
        system = llm.calls[0][0]
        assert "Behavior tendencies" in system
        assert "originate posts: 0.7" in system
        assert "reply or quote: 0.5" in system
        assert "repost: 0.1" in system
        assert "engage with controversy: 0.2" in system

    async def test_sensitive_topics_become_avoidance_hint(self) -> None:
        agent = _agent(
            "me",
            persona_traits={"sensitive_topics": ("종교", "낙태")},
        )
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        await _selector(llm).select_action("me", _ctx(agent=agent))
        system = llm.calls[0][0]
        assert "Avoid initiating posts on these sensitive topics" in system
        assert "종교" in system
        assert "낙태" in system

    async def test_unknown_traits_sink_to_extra_traits_bucket(self) -> None:
        agent = _agent(
            "me",
            persona_traits={"personality": "INTJ", "tone": "skeptical"},
        )
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        await _selector(llm).select_action("me", _ctx(agent=agent))
        system = llm.calls[0][0]
        # Phase 1 key is hoisted at top level, unknown key drops into
        # the extra_traits bucket without losing its value.
        assert '"personality": "INTJ"' in system
        assert "extra_traits" in system
        assert "skeptical" in system

    async def test_topics_field_uses_phase1_naming(self) -> None:
        # Phase 1 uses ``topics`` while ``Agent`` keeps ``interests`` —
        # the prompt renames it so the LLM sees the dual-ontology label.
        agent = _agent("me", interests=("ai", "music"))
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        await _selector(llm).select_action("me", _ctx(agent=agent))
        system = llm.calls[0][0]
        assert '"topics"' in system
        assert "ai" in system
        assert "music" in system


class TestFallbackInvariants:
    async def test_fallback_makes_no_extra_llm_calls(self) -> None:
        # Once a parsed response fails validation, there is no second
        # LLM round-trip — the fallback is local.
        llm = _FakeLLM(_payload(ActionType.LIKE_POST, target_post_id="ghost"))
        await _selector(llm, max_attempts=3).select_action("me", _ctx(feed=(_post("p1"),)))
        assert len(llm.calls) == 1

    async def test_select_action_never_raises(self) -> None:
        # A pathological LLM (raises forever, returns garbage forever)
        # must still produce an Action.
        llm = _FakeLLM(
            RuntimeError("a"),
            RuntimeError("b"),
            RuntimeError("c"),
        )
        action = await _selector(llm, max_attempts=3).select_action("me", _ctx())
        assert isinstance(action, Action)


class TestComposedFallbackChain:
    """All three fallback legs activate inside one ``select_action`` call.

    The unit-level fallback tests above exercise each leg in isolation;
    these tests prove the legs *compose* (transport retry hands off to
    json_repair, json_repair hands off to validation, validation hands
    off to ``DO_NOTHING``) without any short-circuit cross-talk.
    """

    async def test_retry_then_repair_then_validation_failure_collapses(self) -> None:
        # Leg 1: transport error -> AsyncRetrying retries.
        # Leg 2: malformed (single-quoted) JSON -> json_repair parses it.
        # Leg 3: parsed dict fails Action validation (CREATE_POST without
        #        content) -> DO_NOTHING fallback.
        llm = _FakeLLM(
            RuntimeError("transient transport error"),
            "{'type': 'CREATE_POST'}",
        )
        action = await _selector(llm, max_attempts=3).select_action("me", _ctx())
        assert action == Action(type=ActionType.DO_NOTHING)
        # Exactly two LLM round-trips: one failed transport + one returned.
        assert len(llm.calls) == 2

    async def test_retry_then_repair_then_target_validation_collapses(self) -> None:
        # Leg 1: transport error -> retry.
        # Leg 2: single-quoted JSON -> json_repair fixes it -> Action
        #        validates structurally...
        # Leg 3: ...but target_post_id is not in the feed -> DO_NOTHING.
        feed = (_post("p-real"),)
        llm = _FakeLLM(
            RuntimeError("transient"),
            "{'type': 'LIKE_POST', 'target_post_id': 'p-ghost'}",
        )
        action = await _selector(llm, max_attempts=3).select_action("me", _ctx(feed=feed))
        assert action == Action(type=ActionType.DO_NOTHING)
        assert len(llm.calls) == 2


def test_protocol_is_satisfied() -> None:
    selector = ActionSelector(llm=_FakeLLM(), model="test-model")
    assert isinstance(selector, ActionSelectorLike)
