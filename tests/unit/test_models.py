"""Validator-rule and behaviour tests for ``litemiro.models``."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from litemiro.models import (
    Action,
    ActionContext,
    ActionType,
    Agent,
    ContextSummary,
    LLMMeta,
    Post,
    RoundEvent,
)


class TestActionPayloadConsistency:
    def test_create_post_requires_content(self) -> None:
        with pytest.raises(ValidationError, match="CREATE_POST"):
            Action(type=ActionType.CREATE_POST)
        with pytest.raises(ValidationError, match="CREATE_POST"):
            Action(type=ActionType.CREATE_POST, content="")

    def test_create_post_valid(self) -> None:
        a = Action(type=ActionType.CREATE_POST, content="hi")
        assert a.content == "hi"
        assert a.target_post_id is None

    def test_like_post_requires_target(self) -> None:
        with pytest.raises(ValidationError, match="LIKE_POST"):
            Action(type=ActionType.LIKE_POST)

    def test_repost_requires_target(self) -> None:
        with pytest.raises(ValidationError, match="REPOST"):
            Action(type=ActionType.REPOST)

    def test_quote_post_requires_target_and_content(self) -> None:
        with pytest.raises(ValidationError, match="QUOTE_POST"):
            Action(type=ActionType.QUOTE_POST, target_post_id="p-1")
        with pytest.raises(ValidationError, match="QUOTE_POST"):
            Action(type=ActionType.QUOTE_POST, content="agree")

    def test_quote_post_valid(self) -> None:
        a = Action(type=ActionType.QUOTE_POST, target_post_id="p-1", content="agree")
        assert a.target_post_id == "p-1"
        assert a.content == "agree"

    def test_follow_requires_target_agent(self) -> None:
        with pytest.raises(ValidationError, match="FOLLOW"):
            Action(type=ActionType.FOLLOW)

    def test_do_nothing_must_have_no_payload(self) -> None:
        with pytest.raises(ValidationError, match="DO_NOTHING"):
            Action(type=ActionType.DO_NOTHING, target_post_id="p-1")
        with pytest.raises(ValidationError, match="DO_NOTHING"):
            Action(type=ActionType.DO_NOTHING, target_agent_id="a-2")
        with pytest.raises(ValidationError, match="DO_NOTHING"):
            Action(type=ActionType.DO_NOTHING, content="x")

    def test_do_nothing_valid_when_empty(self) -> None:
        a = Action(type=ActionType.DO_NOTHING)
        assert a.type is ActionType.DO_NOTHING

    def test_action_is_frozen(self) -> None:
        a = Action(type=ActionType.DO_NOTHING)
        with pytest.raises(ValidationError):
            a.type = ActionType.LIKE_POST

    def test_unknown_action_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Action.model_validate({"type": "UNFOLLOW"})

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Action.model_validate({"type": "DO_NOTHING", "extra": 1})

    def test_create_post_rejects_targets(self) -> None:
        with pytest.raises(ValidationError, match="CREATE_POST"):
            Action(type=ActionType.CREATE_POST, content="hi", target_post_id="p-1")
        with pytest.raises(ValidationError, match="CREATE_POST"):
            Action(type=ActionType.CREATE_POST, content="hi", target_agent_id="a-2")

    def test_like_post_rejects_extras(self) -> None:
        with pytest.raises(ValidationError, match="LIKE_POST"):
            Action(type=ActionType.LIKE_POST, target_post_id="p-1", content="x")
        with pytest.raises(ValidationError, match="LIKE_POST"):
            Action(type=ActionType.LIKE_POST, target_post_id="p-1", target_agent_id="a-2")

    def test_repost_rejects_extras(self) -> None:
        with pytest.raises(ValidationError, match="REPOST"):
            Action(type=ActionType.REPOST, target_post_id="p-1", content="x")
        with pytest.raises(ValidationError, match="REPOST"):
            Action(type=ActionType.REPOST, target_post_id="p-1", target_agent_id="a-2")

    def test_quote_post_rejects_target_agent(self) -> None:
        with pytest.raises(ValidationError, match="QUOTE_POST"):
            Action(
                type=ActionType.QUOTE_POST,
                target_post_id="p-1",
                content="x",
                target_agent_id="a-2",
            )

    def test_follow_rejects_extras(self) -> None:
        with pytest.raises(ValidationError, match="FOLLOW"):
            Action(type=ActionType.FOLLOW, target_agent_id="a-2", target_post_id="p-1")
        with pytest.raises(ValidationError, match="FOLLOW"):
            Action(type=ActionType.FOLLOW, target_agent_id="a-2", content="x")

    def test_forbidden_field_empty_string_still_rejected(self) -> None:
        # `""` on a forbidden field counts as "carried" — only None is
        # absent. Keeps the validator's contract symmetric with the
        # JSON Schema, which rejects "" via the `"type": "null"` pin.
        with pytest.raises(ValidationError, match="LIKE_POST"):
            Action(type=ActionType.LIKE_POST, target_post_id="p-1", content="")
        with pytest.raises(ValidationError, match="FOLLOW"):
            Action(type=ActionType.FOLLOW, target_agent_id="a-2", target_post_id="")
        with pytest.raises(ValidationError, match="DO_NOTHING"):
            Action(type=ActionType.DO_NOTHING, content="")


class TestPostHotScore:
    def test_zero_engagement_zero_score(self) -> None:
        p = Post(post_id="p", author_id="a", content="x", created_round=0)
        assert p.hot_score(0) == 0.0

    def test_formula_matches_design_doc(self) -> None:
        p = Post(
            post_id="p",
            author_id="a",
            content="x",
            created_round=0,
            likes=1,
            reposts=2,
            quotes=3,
        )
        # weighted = 1 + 2*2 + 3*3 = 14, age = 1, denom = 2^1.5
        expected = 14 / (2**1.5)
        assert abs(p.hot_score(1) - expected) < 1e-9

    def test_decays_with_age(self) -> None:
        p = Post(post_id="p", author_id="a", content="x", created_round=0, likes=10)
        assert p.hot_score(1) > p.hot_score(10)

    def test_rejects_round_before_creation(self) -> None:
        p = Post(post_id="p", author_id="a", content="x", created_round=5)
        with pytest.raises(ValueError, match="precedes"):
            p.hot_score(3)

    def test_negative_engagement_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Post(post_id="p", author_id="a", content="x", created_round=0, likes=-1)

    def test_negative_round_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Post(post_id="p", author_id="a", content="x", created_round=-1)


class TestRoundEvent:
    def test_naive_timestamp_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            RoundEvent(
                round_num=0,
                timestamp=datetime(2026, 4, 1, 10, 0),  # naive — must reject
                agent_id="a-1",
                action=Action(type=ActionType.DO_NOTHING),
            )

    def test_to_jsonl_is_one_line_sorted(self) -> None:
        ts = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)
        e = RoundEvent(
            round_num=0,
            timestamp=ts,
            agent_id="a-1",
            action=Action(type=ActionType.CREATE_POST, content="hi"),
        )
        line = e.to_jsonl()
        assert "\n" not in line
        # sort_keys=True puts "action" before "agent_id" before "round_num"
        assert line.startswith('{"action":')

    def test_extra_fields_allowed_on_root(self) -> None:
        ts = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)
        e = RoundEvent.model_validate(
            {
                "round_num": 0,
                "timestamp": ts.isoformat(),
                "agent_id": "a-1",
                "action": {"type": "DO_NOTHING"},
                "experiment_tag": "ablation-1",
            }
        )
        assert e.model_dump()["experiment_tag"] == "ablation-1"

    def test_negative_round_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RoundEvent(
                round_num=-1,
                timestamp=datetime(2026, 4, 1, 10, 0, tzinfo=UTC),
                agent_id="a-1",
                action=Action(type=ActionType.DO_NOTHING),
            )


def test_action_context_construction() -> None:
    a = Agent(agent_id="a-1")
    ctx = ActionContext(agent=a, round_num=3)
    assert ctx.feed == ()
    assert ctx.recent_actions == ()
    assert ctx.follower_count == 0


def test_context_summary_frozen() -> None:
    cs = ContextSummary(feed_size=0, follower_count=0, following_count=0)
    with pytest.raises(ValidationError):
        cs.feed_size = 1


def test_llm_meta_defaults_fallback_false() -> None:
    m = LLMMeta(model="qwen-plus", tokens_used=10, latency_ms=10.0)
    assert m.fallback_used is False


def test_agent_activation_rate_bounds() -> None:
    with pytest.raises(ValidationError):
        Agent(agent_id="a-1", activation_rate=1.5)
    with pytest.raises(ValidationError):
        Agent(agent_id="a-1", activation_rate=-0.1)
