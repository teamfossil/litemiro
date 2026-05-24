"""Pydantic ↔ JSON Schema parity.

If a Pydantic model can build a value that the JSON Schema rejects (or
vice-versa), Phase 2's emitted JSONL would clear our validators but fail
the CI gate or Phase 3's ingest. These tests guard the round-trip.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from jsonschema import Draft7Validator, FormatChecker

from litemiro.models import Action, ActionType, ContextSummary, LLMMeta, RoundEvent
from litemiro.schemas import round_event_schema


@pytest.fixture(scope="module")
def validator() -> Draft7Validator:
    schema = round_event_schema()
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema, format_checker=FormatChecker())


def _build_event(action: Action, **extra: Any) -> dict[str, Any]:
    """Build a RoundEvent and round-trip through ``to_jsonl``.

    Using the real wire-format path (model → JSONL line → dict) catches
    bugs where ``model_dump`` and ``to_jsonl`` diverge.
    """
    event = RoundEvent(
        round_num=0,
        timestamp=datetime(2026, 4, 1, 10, 0, tzinfo=UTC),
        agent_id="a-1",
        action=action,
        **extra,
    )
    payload: dict[str, Any] = json.loads(event.to_jsonl())
    return payload


@pytest.mark.parametrize(
    "action",
    [
        Action(type=ActionType.CREATE_POST, content="hi"),
        Action(type=ActionType.LIKE_POST, target_post_id="p-1"),
        Action(type=ActionType.REPOST, target_post_id="p-1"),
        Action(type=ActionType.QUOTE_POST, target_post_id="p-1", content="agree"),
        Action(type=ActionType.FOLLOW, target_agent_id="a-2"),
        Action(type=ActionType.DO_NOTHING),
    ],
    ids=lambda a: a.type.value,
)
def test_pydantic_dump_validates(validator: Draft7Validator, action: Action) -> None:
    errs = sorted(
        validator.iter_errors(_build_event(action)),
        key=lambda e: list(e.absolute_path),
    )
    assert errs == [], "; ".join(e.message for e in errs)


def test_full_event_with_optional_blocks(validator: Draft7Validator) -> None:
    payload = _build_event(
        Action(type=ActionType.CREATE_POST, content="hi"),
        context_summary=ContextSummary(feed_size=0, follower_count=0, following_count=0),
        llm_meta=LLMMeta(model="qwen-plus", tokens_used=10, latency_ms=10.0),
    )
    errs = list(validator.iter_errors(payload))
    assert errs == [], "; ".join(e.message for e in errs)


def test_schema_rejects_unknown_action_type(validator: Draft7Validator) -> None:
    payload: dict[str, Any] = {
        "round_num": 0,
        "timestamp": "2026-04-01T10:00:00+00:00",
        "agent_id": "a-1",
        "action": {"type": "UNFOLLOW"},
    }
    assert list(validator.iter_errors(payload))


def test_schema_rejects_bad_timestamp_format(validator: Draft7Validator) -> None:
    payload: dict[str, Any] = {
        "round_num": 0,
        "timestamp": "not-a-date",
        "agent_id": "a-1",
        "action": {"type": "DO_NOTHING"},
    }
    assert list(validator.iter_errors(payload))


def test_schema_enforces_create_post_content(validator: Draft7Validator) -> None:
    payload: dict[str, Any] = {
        "round_num": 0,
        "timestamp": "2026-04-01T10:00:00+00:00",
        "agent_id": "a-1",
        "action": {"type": "CREATE_POST"},
    }
    assert list(validator.iter_errors(payload))


def test_schema_enforces_quote_post_both_fields(validator: Draft7Validator) -> None:
    payload: dict[str, Any] = {
        "round_num": 0,
        "timestamp": "2026-04-01T10:00:00+00:00",
        "agent_id": "a-1",
        "action": {"type": "QUOTE_POST", "target_post_id": "p-1"},
    }
    assert list(validator.iter_errors(payload))


def test_schema_rejects_extra_keys_inside_action(validator: Draft7Validator) -> None:
    payload: dict[str, Any] = {
        "round_num": 0,
        "timestamp": "2026-04-01T10:00:00+00:00",
        "agent_id": "a-1",
        "action": {"type": "DO_NOTHING", "boom": True},
    }
    assert list(validator.iter_errors(payload))


@pytest.mark.parametrize(
    "action_payload",
    [
        {"type": "CREATE_POST", "content": "hi", "target_post_id": "p-1"},
        {"type": "CREATE_POST", "content": "hi", "target_agent_id": "a-2"},
        {"type": "LIKE_POST", "target_post_id": "p-1", "content": "x"},
        {"type": "LIKE_POST", "target_post_id": "p-1", "target_agent_id": "a-2"},
        {"type": "REPOST", "target_post_id": "p-1", "content": "x"},
        {"type": "REPOST", "target_post_id": "p-1", "target_agent_id": "a-2"},
        {
            "type": "QUOTE_POST",
            "target_post_id": "p-1",
            "content": "x",
            "target_agent_id": "a-2",
        },
        {"type": "FOLLOW", "target_agent_id": "a-2", "target_post_id": "p-1"},
        {"type": "FOLLOW", "target_agent_id": "a-2", "content": "x"},
        {"type": "DO_NOTHING", "target_post_id": "p-1"},
        {"type": "DO_NOTHING", "target_agent_id": "a-2"},
        {"type": "DO_NOTHING", "content": "x"},
    ],
    ids=lambda p: p["type"] + "+" + ",".join(sorted(k for k in p if k != "type")),
)
def test_schema_rejects_forbidden_fields(
    validator: Draft7Validator, action_payload: dict[str, Any]
) -> None:
    payload: dict[str, Any] = {
        "round_num": 0,
        "timestamp": "2026-04-01T10:00:00+00:00",
        "agent_id": "a-1",
        "action": action_payload,
    }
    assert list(validator.iter_errors(payload)), (
        f"schema accepted forbidden-field combination: {action_payload}"
    )


def test_schema_self_check() -> None:
    """The bundled schema must itself be a valid Draft 7 document."""
    Draft7Validator.check_schema(round_event_schema())
