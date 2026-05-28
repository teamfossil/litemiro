"""TDD spec for ``litemiro.action.selector.ActionSelector``.

Notion Section 3.3 / Section 3.4 only stipulate "ActionSelector → LLM → Action" and a
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

import collections
import json
from typing import Any

import pytest

from litemiro.action.selector import ActionSelector
from litemiro.interfaces import ActionSelectorLike, LLMClient
from litemiro.models import Action, ActionContext, ActionType, Agent, LLMResponse, Post
from litemiro.prompts.action_selector import compose_user


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
    following_ids: frozenset[str] = frozenset(),
) -> ActionContext:
    return ActionContext(
        agent=agent or _agent(),
        feed=feed,
        recent_actions=recent_actions,
        follower_count=follower_count,
        following_count=following_count,
        following_ids=following_ids,
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
    retry/fallback tests need. The queue accepts plain strings (token
    counts default to 0) or full ``LLMResponse`` instances when a test
    needs to assert on token usage.
    """

    def __init__(self, *responses: str | LLMResponse | BaseException) -> None:
        self._queue: list[str | LLMResponse | BaseException] = list(responses)
        self.calls: list[tuple[str, str, str]] = []

    async def complete(self, *, system: str, user: str, model: str) -> LLMResponse:
        self.calls.append((system, user, model))
        if not self._queue:
            raise AssertionError("FakeLLM exhausted: tests should pre-queue all responses")
        item = self._queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item if isinstance(item, LLMResponse) else LLMResponse(content=item)


def _selector(llm: LLMClient, *, max_attempts: int = 3) -> ActionSelector:
    return ActionSelector(llm=llm, model="test-model", max_attempts=max_attempts)


async def _act(
    llm: LLMClient,
    ctx: ActionContext | None = None,
    *,
    agent_id: str = "me",
    max_attempts: int = 3,
) -> Action:
    """Run ``select_action`` and return the bare ``Action``.

    Most tests assert on the action only — they do not care about the
    ``LLMMeta`` block. Tests that *do* need the full ``ActionResult``
    call ``ActionSelector.select_action`` directly.
    """
    result = await _selector(llm, max_attempts=max_attempts).select_action(
        agent_id, ctx if ctx is not None else _ctx()
    )
    return result.action


class TestHappyPath:
    async def test_returns_parsed_like_action(self) -> None:
        feed = (_post("p1"),)
        llm = _FakeLLM(_payload(ActionType.LIKE_POST, target_post_id="p1"))
        action = (await _selector(llm).select_action("me", _ctx(feed=feed))).action
        assert action == Action(type=ActionType.LIKE_POST, target_post_id="p1")

    async def test_returns_do_nothing_when_llm_says_so(self) -> None:
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        action = (await _selector(llm).select_action("me", _ctx())).action
        assert action == Action(type=ActionType.DO_NOTHING)

    async def test_passes_model_through_to_llm(self) -> None:
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        await _selector(llm).select_action("me", _ctx())
        assert llm.calls[0][2] == "test-model"


class TestActionTypes:
    """Cover every ``ActionType`` end-to-end."""

    async def test_create_post(self) -> None:
        llm = _FakeLLM(_payload(ActionType.CREATE_POST, content="my new post"))
        action = (await _selector(llm).select_action("me", _ctx())).action
        assert action == Action(type=ActionType.CREATE_POST, content="my new post")

    async def test_like_post(self) -> None:
        feed = (_post("p1"),)
        llm = _FakeLLM(_payload(ActionType.LIKE_POST, target_post_id="p1"))
        action = (await _selector(llm).select_action("me", _ctx(feed=feed))).action
        assert action.type is ActionType.LIKE_POST
        assert action.target_post_id == "p1"

    async def test_repost(self) -> None:
        feed = (_post("p1"),)
        llm = _FakeLLM(_payload(ActionType.REPOST, target_post_id="p1"))
        action = (await _selector(llm).select_action("me", _ctx(feed=feed))).action
        assert action.type is ActionType.REPOST
        assert action.target_post_id == "p1"

    async def test_quote_post(self) -> None:
        feed = (_post("p1"),)
        llm = _FakeLLM(_payload(ActionType.QUOTE_POST, target_post_id="p1", content="my quote"))
        action = (await _selector(llm).select_action("me", _ctx(feed=feed))).action
        assert action.type is ActionType.QUOTE_POST
        assert action.target_post_id == "p1"
        assert action.content == "my quote"

    async def test_follow(self) -> None:
        feed = (_post("p1", author="alice"),)
        llm = _FakeLLM(_payload(ActionType.FOLLOW, target_agent_id="alice"))
        action = (await _selector(llm).select_action("me", _ctx(feed=feed))).action
        assert action.type is ActionType.FOLLOW
        assert action.target_agent_id == "alice"

    async def test_do_nothing(self) -> None:
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        action = (await _selector(llm).select_action("me", _ctx())).action
        assert action.type is ActionType.DO_NOTHING


class TestRetry:
    async def test_retries_on_transient_failure_then_succeeds(self) -> None:
        feed = (_post("p1"),)
        llm = _FakeLLM(
            RuntimeError("connection reset"),
            RuntimeError("connection reset"),
            _payload(ActionType.LIKE_POST, target_post_id="p1"),
        )
        action = (await _selector(llm, max_attempts=3).select_action("me", _ctx(feed=feed))).action
        assert action.type is ActionType.LIKE_POST
        assert len(llm.calls) == 3

    async def test_exhausted_retries_returns_do_nothing(self) -> None:
        llm = _FakeLLM(
            RuntimeError("boom"),
            RuntimeError("boom"),
            RuntimeError("boom"),
        )
        action = (await _selector(llm, max_attempts=3).select_action("me", _ctx())).action
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
        action = (await _selector(llm).select_action("me", _ctx(feed=feed))).action
        assert action == Action(type=ActionType.LIKE_POST, target_post_id="p1")

    async def test_repairs_python_style_quotes(self) -> None:
        feed = (_post("p1"),)
        llm = _FakeLLM("{'type': 'LIKE_POST', 'target_post_id': 'p1'}")
        action = (await _selector(llm).select_action("me", _ctx(feed=feed))).action
        assert action == Action(type=ActionType.LIKE_POST, target_post_id="p1")

    async def test_unrepairable_garbage_falls_back(self) -> None:
        llm = _FakeLLM("not json at all <<<>>>")
        action = (await _selector(llm).select_action("me", _ctx())).action
        assert action == Action(type=ActionType.DO_NOTHING)


class TestTargetValidation:
    async def test_unknown_post_id_falls_back(self) -> None:
        feed = (_post("p1"),)
        llm = _FakeLLM(_payload(ActionType.LIKE_POST, target_post_id="ghost"))
        action = (await _selector(llm).select_action("me", _ctx(feed=feed))).action
        assert action == Action(type=ActionType.DO_NOTHING)

    async def test_unknown_repost_target_falls_back(self) -> None:
        feed = (_post("p1"),)
        llm = _FakeLLM(_payload(ActionType.REPOST, target_post_id="ghost"))
        action = (await _selector(llm).select_action("me", _ctx(feed=feed))).action
        assert action == Action(type=ActionType.DO_NOTHING)

    async def test_unknown_quote_target_falls_back(self) -> None:
        feed = (_post("p1"),)
        llm = _FakeLLM(_payload(ActionType.QUOTE_POST, target_post_id="ghost", content="hi"))
        action = (await _selector(llm).select_action("me", _ctx(feed=feed))).action
        assert action == Action(type=ActionType.DO_NOTHING)

    async def test_unknown_follow_target_falls_back(self) -> None:
        feed = (_post("p1", author="alice"),)
        llm = _FakeLLM(_payload(ActionType.FOLLOW, target_agent_id="bob"))
        action = (await _selector(llm).select_action("me", _ctx(feed=feed))).action
        assert action == Action(type=ActionType.DO_NOTHING)

    async def test_create_post_skips_target_validation(self) -> None:
        # CREATE_POST has no target → empty feed must not block it.
        llm = _FakeLLM(_payload(ActionType.CREATE_POST, content="fresh"))
        action = (await _selector(llm).select_action("me", _ctx())).action
        assert action.type is ActionType.CREATE_POST
        assert action.content == "fresh"

    async def test_self_target_for_like_falls_back(self) -> None:
        # An agent must not like its own post — even if it appears in
        # the feed via a misconfigured FeedEngine.
        feed = (_post("p1", author="me"),)
        llm = _FakeLLM(_payload(ActionType.LIKE_POST, target_post_id="p1"))
        action = (await _selector(llm).select_action("me", _ctx(feed=feed))).action
        assert action == Action(type=ActionType.DO_NOTHING)

    async def test_self_follow_falls_back(self) -> None:
        feed = (_post("p1", author="me"),)
        llm = _FakeLLM(_payload(ActionType.FOLLOW, target_agent_id="me"))
        action = (await _selector(llm).select_action("me", _ctx(feed=feed))).action
        assert action == Action(type=ActionType.DO_NOTHING)

    async def test_already_followed_target_falls_back(self) -> None:
        # 이미 follow 중인 author 를 다시 FOLLOW → 신규 엣지가 아니므로 fallback.
        feed = (_post("p1", author="alice"),)
        llm = _FakeLLM(_payload(ActionType.FOLLOW, target_agent_id="alice"))
        ctx = _ctx(feed=feed, following_ids=frozenset({"alice"}))
        action = (await _selector(llm).select_action("me", ctx)).action
        assert action == Action(type=ActionType.DO_NOTHING)


class TestPydanticValidation:
    async def test_invalid_action_type_falls_back(self) -> None:
        llm = _FakeLLM('{"type": "UNFOLLOW"}')
        action = (await _selector(llm).select_action("me", _ctx())).action
        assert action == Action(type=ActionType.DO_NOTHING)

    async def test_create_post_without_content_falls_back(self) -> None:
        llm = _FakeLLM('{"type": "CREATE_POST"}')
        action = (await _selector(llm).select_action("me", _ctx())).action
        assert action == Action(type=ActionType.DO_NOTHING)

    async def test_do_nothing_with_payload_falls_back(self) -> None:
        llm = _FakeLLM('{"type": "DO_NOTHING", "content": "leaked"}')
        action = (await _selector(llm).select_action("me", _ctx())).action
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

    async def test_system_prompt_anti_quote_spam_cues_present(self) -> None:
        # QUOTE_POST 쏠림 (LLM 이 라운드마다 quote-reply 만 고름) 회귀 가드.
        # 한 번 손본 뒤 누가 무심코 cue 를 빼버리면 분포가 다시 무너지는 게
        # 비싸서 (15 라운드 풀 sim 한 번이 ~$1), 핵심 표현 두 가지를
        # 텍스트로 못 박는다 — LIKE 가 routine agreement 의 답이라는 점과
        # QUOTE 의 self-check 게이트.
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        await _selector(llm).select_action("me", _ctx())
        system = llm.calls[0][0]
        assert "Most agreement should be a LIKE" in system
        assert "would a stranger reading my added text learn something" in system

    async def test_system_prompt_keeps_follow_alive(self) -> None:
        # FOLLOW 가 0 건으로 죽지 않도록 — "shape your network" 와
        # follow_rate 와의 연결을 명시한 cue 가 빠지면 LLM 이 FOLLOW 를
        # 완전히 무시한다 (v2 prompt 에서 관측). 사람 손이 cue 를 빼면
        # 재현되니 텍스트로 잠가둔다.
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        await _selector(llm).select_action("me", _ctx())
        system = llm.calls[0][0]
        assert "Skipping FOLLOW entirely contradicts your follow_rate" in system

    async def test_system_prompt_quote_streak_guard_present(self) -> None:
        # #124: streak count 가드 cue ("recent actions 가 QUOTE_POST 2회 이상이면
        # LIKE / REPOST 로 default") 가 사라지면 v1 의 QUOTE 57% 쏠림이 재발한다.
        # anti_quote_spam_cues 와 별도로 분리 — 어느 cue 가 빠졌는지 stack 에서
        # 즉시 식별 가능하도록.
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        await _selector(llm).select_action("me", _ctx())
        system = llm.calls[0][0]
        assert "if two or more of your recent actions were QUOTE_POST" in system

    async def test_user_prompt_renders_quote_post_action_literal(self) -> None:
        # #124: streak 가드가 카운트 가능하려면 recent_actions 의 QUOTE_POST 가
        # user prompt 에 그 literal 그대로 노출돼야. _recent_block 의 출력 포맷
        # ("<ActionType> target_post_id=<id>") 도 같이 잠근다 — cue 와 노출 포맷
        # 두 쪽 다 살아있어야 LLM 이 streak 을 셀 수 있음.
        recent = (Action(type=ActionType.QUOTE_POST, target_post_id="p-q1", content="hi"),)
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        await _selector(llm).select_action("me", _ctx(recent_actions=recent))
        user = llm.calls[0][1]
        assert "QUOTE_POST target_post_id=p-q1" in user


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

    async def test_behavior_tendency_renders_reaction_mix(self) -> None:
        # behavior_tendency 가 있으면 reaction 분기 가중을 LIKE/REPOST/QUOTE share
        # 로 렌더한다. like_rate : repost_rate : controversy_affinity 정규화.
        # originate 축 (post/follow) 은 prompt 가 아니라 게이트가 다루므로 여기 안 뜬다.
        agent = _agent(
            "me",
            persona_traits={
                "behavior_tendency": {
                    "post_rate": 0.7,
                    "reply_rate": 0.5,
                    "repost_rate": 0.1,
                    "like_rate": 0.6,
                    "follow_rate": 0.4,
                    "controversy_affinity": 0.2,
                },
            },
        )
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        await _selector(llm).select_action("me", _ctx(agent=agent))
        system = llm.calls[0][0]
        # like:repost:contro = 0.6:0.1:0.2, total 0.9 → 67% / 11% / 22%
        assert "When you react to a feed post" in system
        assert "LIKE 67% / REPOST 11% / QUOTE 22%" in system
        assert "normalized from like_rate / repost_rate / controversy_affinity" in system

    async def test_originate_and_umbrella_cues_absent(self) -> None:
        # originate 축은 ActionSelector 게이트가 전담 — 옛 post_rate 직교 cue 와
        # reply_rate umbrella 산수 (음수 QUOTE remainder 버그의 출처) 가 prompt 에
        # 남으면 안 된다.
        agent = _agent(
            "me",
            persona_traits={
                "behavior_tendency": {
                    "post_rate": 0.5,
                    "reply_rate": 0.3,
                    "repost_rate": 0.2,
                    "like_rate": 0.6,
                    "follow_rate": 0.2,
                    "controversy_affinity": 0.5,
                },
            },
        )
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        await _selector(llm).select_action("me", _ctx(agent=agent))
        system = llm.calls[0][0]
        assert "post_rate is a separate axis" not in system
        assert "reply_rate is the total reaction probability" not in system
        assert "remainder" not in system

    async def test_reaction_mix_well_defined_when_like_repost_exceed_reply(self) -> None:
        # 옛 산수 (QUOTE = reply - like - repost) 는 like+repost > reply 인 Phase 1
        # 페르소나 97% 에서 QUOTE 에 음수를 배정했다. 정규화 share 는 항상 [0,100].
        agent = _agent(
            "me",
            persona_traits={
                "behavior_tendency": {
                    "reply_rate": 0.3,
                    "repost_rate": 0.8,
                    "like_rate": 0.9,
                    "controversy_affinity": 0.1,
                },
            },
        )
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        await _selector(llm).select_action("me", _ctx(agent=agent))
        system = llm.calls[0][0]
        # like:repost:contro = 0.9:0.8:0.1, total 1.8 → 50% / 44% / 6% (모두 양수)
        assert "LIKE 50% / REPOST 44% / QUOTE 6%" in system

    async def test_like_rate_falls_back_in_reaction_mix(self) -> None:
        # 구버전 Phase 1 ontology (#10 이전) 가 like_rate 를 빠뜨리면 LIKE 가중치가
        # 사라져 QUOTE 로 쏠린다. fallback 0.4 가 정규화에 들어가 신호가 살아있어야.
        agent = _agent(
            "me",
            persona_traits={
                "behavior_tendency": {
                    "reply_rate": 0.3,
                    "repost_rate": 0.2,
                    "controversy_affinity": 0.5,
                },
            },
        )
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        await _selector(llm).select_action("me", _ctx(agent=agent))
        system = llm.calls[0][0]
        # like(fallback 0.4):repost 0.2:contro 0.5, total 1.1 → 36% / 18% / 45%
        assert "LIKE 36% / REPOST 18% / QUOTE 45%" in system

    async def test_behavior_hint_skipped_when_tendency_absent(self) -> None:
        # behavior_tendency 객체 자체가 없으면 hint 가 출력되지 않는다 —
        # follow_rate/like_rate 폴백이 다른 키 누락 패스 동작을 깨뜨리지 않는지 검증.
        agent = _agent("me", persona_traits={"personality": "INTJ"})
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        await _selector(llm).select_action("me", _ctx(agent=agent))
        system = llm.calls[0][0]
        assert "Behavior tendencies" not in system

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
        action = (await _selector(llm, max_attempts=3).select_action("me", _ctx())).action
        assert isinstance(action, Action)

    @pytest.mark.parametrize("hook", ["compose_system", "compose_user"])
    async def test_prompt_composition_failure_collapses_to_do_nothing(
        self, monkeypatch: pytest.MonkeyPatch, hook: str
    ) -> None:
        # ``select_action`` guards prompt assembly inside the same
        # try-block as the LLM call: if ``compose_system`` /
        # ``compose_user`` raises, the call collapses to
        # ``DO_NOTHING(fallback_used=True)`` *before* any LLM round-trip.
        # ``test_select_action_never_raises`` only exercises the
        # LLM-raises leg — this is the composition leg.
        def _boom(*args: Any, **kwargs: Any) -> str:
            raise RuntimeError(f"{hook} failed")

        monkeypatch.setattr(f"litemiro.action.selector.{hook}", _boom)
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        result = await _selector(llm).select_action("me", _ctx())
        assert result.action == Action(type=ActionType.DO_NOTHING)
        assert result.llm_meta.fallback_used is True
        assert result.llm_meta.tokens_used == 0
        assert llm.calls == []  # composition failed before the LLM call


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
        action = (await _selector(llm, max_attempts=3).select_action("me", _ctx())).action
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
        action = (await _selector(llm, max_attempts=3).select_action("me", _ctx(feed=feed))).action
        assert action == Action(type=ActionType.DO_NOTHING)
        assert len(llm.calls) == 2


class TestLLMMetaTracking:
    """``ActionResult.llm_meta`` is the round runner's only window into
    LLM accounting (model name, token usage, latency, fallback flag).

    The contract: tokens come from ``LLMResponse``; latency is wall-clock
    around the LLM call (and fallback work); ``fallback_used`` is ``True``
    on every leg of the safety net (transport exhaustion, JSON parse
    failure, validation failure, target validation failure) and ``False``
    on the happy path.
    """

    async def test_happy_path_records_model_and_tokens(self) -> None:
        feed = (_post("p1"),)
        llm = _FakeLLM(
            LLMResponse(
                content=_payload(ActionType.LIKE_POST, target_post_id="p1"),
                prompt_tokens=120,
                completion_tokens=37,
            )
        )
        result = await _selector(llm).select_action("me", _ctx(feed=feed))
        assert result.action.type is ActionType.LIKE_POST
        assert result.llm_meta.model == "test-model"
        assert result.llm_meta.tokens_used == 157
        assert result.llm_meta.fallback_used is False
        assert result.llm_meta.latency_ms >= 0.0

    async def test_zero_token_usage_propagates(self) -> None:
        # Fakes / local backends that don't populate usage leave the
        # counts at zero; ActionSelector must not fabricate numbers.
        llm = _FakeLLM(_payload(ActionType.DO_NOTHING))
        result = await _selector(llm).select_action("me", _ctx())
        assert result.llm_meta.tokens_used == 0
        assert result.llm_meta.fallback_used is False

    async def test_retry_exhaustion_flags_fallback(self) -> None:
        llm = _FakeLLM(
            RuntimeError("a"),
            RuntimeError("b"),
            RuntimeError("c"),
        )
        result = await _selector(llm, max_attempts=3).select_action("me", _ctx())
        assert result.action == Action(type=ActionType.DO_NOTHING)
        assert result.llm_meta.fallback_used is True
        # No successful response → tokens_used stays at zero.
        assert result.llm_meta.tokens_used == 0

    async def test_unparseable_json_flags_fallback_but_keeps_tokens(self) -> None:
        # The LLM call succeeded (token usage is real spend) but the
        # response was unusable. The fallback flag must flip while the
        # token counters stay truthful.
        llm = _FakeLLM(
            LLMResponse(content="not json <<<>>>", prompt_tokens=80, completion_tokens=12),
        )
        result = await _selector(llm).select_action("me", _ctx())
        assert result.action == Action(type=ActionType.DO_NOTHING)
        assert result.llm_meta.fallback_used is True
        assert result.llm_meta.tokens_used == 92

    async def test_validation_failure_flags_fallback(self) -> None:
        llm = _FakeLLM(
            LLMResponse(
                content='{"type": "CREATE_POST"}',
                prompt_tokens=50,
                completion_tokens=8,
            ),
        )
        result = await _selector(llm).select_action("me", _ctx())
        assert result.action == Action(type=ActionType.DO_NOTHING)
        assert result.llm_meta.fallback_used is True
        assert result.llm_meta.tokens_used == 58

    async def test_target_validation_failure_flags_fallback(self) -> None:
        feed = (_post("p1"),)
        llm = _FakeLLM(
            LLMResponse(
                content=_payload(ActionType.LIKE_POST, target_post_id="ghost"),
                prompt_tokens=60,
                completion_tokens=14,
            ),
        )
        result = await _selector(llm).select_action("me", _ctx(feed=feed))
        assert result.action == Action(type=ActionType.DO_NOTHING)
        assert result.llm_meta.fallback_used is True
        assert result.llm_meta.tokens_used == 74


class TestAuthorsBlockSample:
    """#142: _authors_block 의 각 author 에 sample post snippet 첨부.

    PR #140 가 author 의 등장 수 + follow 여부를 노출했지만 ideology 정보
    가 사적이라 LLM 이 stance 정합도를 판단하기 위한 신호가 부족했고,
    실제 15-라운드 시뮬에서 follower-followee ideology diff 0.2~0.5
    중간 mismatch 가 22% 발생. 각 author 의 첫 등장 post 본문을 같이
    노출해 stance 추론 단서를 제공한다.
    """

    def test_each_author_line_followed_by_sample(self) -> None:
        p1 = _post("p1", author="alice", content="ai will transform productivity")
        p2 = _post("p2", author="bob", content="regulation must come first")
        p3 = _post("p3", author="alice", content="another post by alice")
        rendered = compose_user(_ctx(feed=(p1, p2, p3)))
        assert "Authors in your feed" in rendered
        # alice 2 posts, bob 1 post. sample 은 각 author 의 첫 등장 (= hot
        # order 최상위) post 의 content prefix.
        assert "@alice (2 posts) — not yet followed" in rendered
        assert "sample: ai will transform productivity" in rendered
        assert "@bob (1 posts) — not yet followed" in rendered
        assert "sample: regulation must come first" in rendered

    def test_sample_truncates_at_eighty_chars(self) -> None:
        long = "x" * 200
        rendered = compose_user(_ctx(feed=(_post("p1", author="alice", content=long),)))
        assert "sample: " + ("x" * 80) in rendered
        # 81 글자는 잘려야 함.
        assert "sample: " + ("x" * 81) not in rendered

    def test_already_following_tag_preserved(self) -> None:
        ctx = ActionContext(
            agent=_agent(),
            feed=(_post("p1", author="alice", content="hi"),),
            following_ids=frozenset({"alice"}),
            round_num=1,
        )
        rendered = compose_user(ctx)
        assert "@alice (1 posts) — already following" in rendered
        assert "sample: hi" in rendered

    def test_self_authored_posts_excluded(self) -> None:
        # 본인 post 는 author 섹션에서 빠져야 follow 후보 노이즈가 안 생긴다.
        feed = (_post("p1", author="me", content="my own post"),)
        rendered = compose_user(_ctx(feed=feed))
        assert "Authors in your feed" not in rendered


class TestBehaviorGate:
    """``global_seed`` enables the originate-axis probability gate: CREATE_POST
    and FOLLOW are drawn from post_rate / follow_rate rather than left to the
    LLM, and the reaction branch drops them from the allowed set. With no seed
    (the unit-test default) the gate is off and the pre-gate contract holds.
    """

    def _agent_bt(self, **rates: float) -> Agent:
        base = {
            "post_rate": 0.5,
            "reply_rate": 0.3,
            "repost_rate": 0.2,
            "like_rate": 0.4,
            "follow_rate": 0.2,
            "controversy_affinity": 0.5,
        }
        base.update(rates)
        return _agent("me", persona_traits={"behavior_tendency": base})

    def _sel(self, llm: LLMClient, *, seed: int = 7) -> ActionSelector:
        return ActionSelector(llm=llm, model="test-model", global_seed=seed)

    # ── gate decision (white-box on the probability split) ──────────────

    def test_gate_off_without_seed(self) -> None:
        sel = ActionSelector(llm=_FakeLLM(), model="test-model")  # no global_seed
        ctx = _ctx(agent=self._agent_bt(), feed=(_post("p1"),))
        assert sel._gate("me", ctx) == (None, False)

    def test_gate_off_without_behavior_tendency(self) -> None:
        ctx = _ctx(agent=_agent("me", persona_traits={"tone": "x"}), feed=(_post("p1"),))
        assert self._sel(_FakeLLM())._gate("me", ctx) == (None, False)

    def test_cold_start_forces_create_post(self) -> None:
        ctx = _ctx(agent=self._agent_bt(), feed=())
        assert self._sel(_FakeLLM())._gate("me", ctx) == (ActionType.CREATE_POST, False)

    def test_sole_post_weight_forces_create_post(self) -> None:
        # 단독 nonzero 가중 → 그 family 확정 (정규화 분모가 그 하나뿐).
        ctx = _ctx(
            agent=self._agent_bt(post_rate=1.0, reply_rate=0.0, follow_rate=0.0),
            feed=(_post("p1"),),
        )
        assert self._sel(_FakeLLM())._gate("me", ctx)[0] is ActionType.CREATE_POST

    def test_sole_follow_weight_forces_follow(self) -> None:
        ctx = _ctx(
            agent=self._agent_bt(post_rate=0.0, reply_rate=0.0, follow_rate=1.0),
            feed=(_post("p1", author="alice"),),
        )
        assert self._sel(_FakeLLM())._gate("me", ctx)[0] is ActionType.FOLLOW

    def test_sole_reply_weight_forces_reaction(self) -> None:
        ctx = _ctx(
            agent=self._agent_bt(post_rate=0.0, reply_rate=1.0, follow_rate=0.0),
            feed=(_post("p1"),),
        )
        assert self._sel(_FakeLLM())._gate("me", ctx) == (None, True)

    def test_reply_rate_drives_reaction_share(self) -> None:
        # reply_rate 가 family 가중에 들어가므로 (순차 게이트의 회귀 수정) reply 가
        # 지배적이면 reaction 이 다수가 된다 — LIKE 위축의 근본 해소.
        agent = self._agent_bt(post_rate=0.1, reply_rate=0.8, follow_rate=0.1)
        sel = self._sel(_FakeLLM(), seed=1)
        feed = (_post("p1", author="alice"),)
        fams: collections.Counter[str] = collections.Counter()
        for r in range(200):
            forced, react = sel._gate("me", _ctx(agent=agent, feed=feed, round_num=r))
            fams["REACTION" if react else forced.value] += 1  # type: ignore[union-attr]
        assert fams["REACTION"] > fams["CREATE_POST"]
        assert fams["REACTION"] > fams["FOLLOW"]

    def test_follow_skipped_when_only_self_authored_feed(self) -> None:
        # 유일한 feed author 가 자기 자신이면 FOLLOW 강제 불가 → reaction 분기.
        ctx = _ctx(
            agent=self._agent_bt(post_rate=0.0, follow_rate=1.0),
            feed=(_post("p1", author="me"),),
        )
        assert self._sel(_FakeLLM())._gate("me", ctx) == (None, True)

    def test_follow_skipped_when_all_feed_authors_already_followed(self) -> None:
        # feed 의 non-self author 가 전부 이미 follow 대상이면 FOLLOW family 가
        # 후보에서 빠진다 — 새로 follow 할 사람이 없는데 FOLLOW 를 강제하지 않는다.
        ctx = _ctx(
            agent=self._agent_bt(post_rate=0.0, reply_rate=0.0, follow_rate=1.0),
            feed=(_post("p1", author="alice"),),
            following_ids=frozenset({"alice"}),
        )
        assert self._sel(_FakeLLM())._gate("me", ctx) == (None, True)

    def test_follow_candidate_excludes_already_followed_author(self) -> None:
        # alice 는 이미 follow, bob 은 아직 — bob 덕에 FOLLOW 후보는 살아있다.
        ctx = _ctx(
            agent=self._agent_bt(post_rate=0.0, reply_rate=0.0, follow_rate=1.0),
            feed=(_post("p1", author="alice"), _post("p2", author="bob")),
            following_ids=frozenset({"alice"}),
        )
        assert self._sel(_FakeLLM())._gate("me", ctx)[0] is ActionType.FOLLOW

    def test_gate_deterministic_in_seed_agent_round(self) -> None:
        ctx = _ctx(
            agent=self._agent_bt(post_rate=0.5, follow_rate=0.5), feed=(_post("p1"),), round_num=3
        )
        assert self._sel(_FakeLLM(), seed=99)._gate("me", ctx) == self._sel(
            _FakeLLM(), seed=99
        )._gate("me", ctx)

    def test_gate_handles_missing_originate_rates(self) -> None:
        # post_rate / follow_rate 키가 없어도 (구버전 ontology) fallback 으로 동작, 예외 없음.
        agent = _agent("me", persona_traits={"behavior_tendency": {"like_rate": 0.4}})
        forced, _ = self._sel(_FakeLLM())._gate("me", _ctx(agent=agent, feed=(_post("p1"),)))
        assert forced in (ActionType.CREATE_POST, ActionType.FOLLOW, None)

    # ── prompt narrowing + violation fallback (through select_action) ───

    async def test_forced_create_post_narrows_prompt(self) -> None:
        llm = _FakeLLM(_payload(ActionType.CREATE_POST, content="새 화제를 던진다"))
        ctx = _ctx(
            agent=self._agent_bt(post_rate=1.0, reply_rate=0.0, follow_rate=0.0),
            feed=(_post("p1"),),
        )
        result = await self._sel(llm).select_action("me", ctx)
        assert result.action.type is ActionType.CREATE_POST
        assert "ORIGINATING new content" in llm.calls[0][0]

    async def test_react_only_prompt_excludes_originate_from_allowed(self) -> None:
        llm = _FakeLLM(_payload(ActionType.LIKE_POST, target_post_id="p1"))
        ctx = _ctx(agent=self._agent_bt(post_rate=0.0, follow_rate=0.0), feed=(_post("p1"),))
        result = await self._sel(llm).select_action("me", ctx)
        system = llm.calls[0][0]
        assert result.action.type is ActionType.LIKE_POST
        assert "are NOT options now" in system
        assert "Allowed ActionType values: LIKE_POST, REPOST, QUOTE_POST, DO_NOTHING" in system

    async def test_forced_family_violation_falls_back(self) -> None:
        # 게이트가 CREATE_POST 를 강제했는데 LLM 이 다른 타입을 내면 fallback 으로 관측.
        llm = _FakeLLM(_payload(ActionType.LIKE_POST, target_post_id="p1"))
        ctx = _ctx(
            agent=self._agent_bt(post_rate=1.0, reply_rate=0.0, follow_rate=0.0),
            feed=(_post("p1"),),
        )
        result = await self._sel(llm).select_action("me", ctx)
        assert result.action.type is ActionType.DO_NOTHING
        assert result.llm_meta.fallback_used is True

    async def test_react_only_rejects_create_post(self) -> None:
        llm = _FakeLLM(_payload(ActionType.CREATE_POST, content="게이트를 무시한 글"))
        ctx = _ctx(agent=self._agent_bt(post_rate=0.0, follow_rate=0.0), feed=(_post("p1"),))
        result = await self._sel(llm).select_action("me", ctx)
        assert result.action.type is ActionType.DO_NOTHING
        assert result.llm_meta.fallback_used is True


def test_protocol_is_satisfied() -> None:
    selector = ActionSelector(llm=_FakeLLM(), model="test-model")
    assert isinstance(selector, ActionSelectorLike)
