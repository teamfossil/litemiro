from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from jsonschema import Draft7Validator, FormatChecker

from litemiro.eventlog import EventLogger
from litemiro.models import Action, ActionType, ContextSummary, LLMMeta, RoundEvent
from litemiro.schemas import round_event_schema


def _event(*, round_num: int = 0, agent_id: str = "a-001") -> RoundEvent:
    return RoundEvent(
        round_num=round_num,
        timestamp=datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC),
        agent_id=agent_id,
        action=Action(type=ActionType.DO_NOTHING),
        context_summary=ContextSummary(feed_size=0, follower_count=0, following_count=0),
        llm_meta=LLMMeta(model="qwen-plus", tokens_used=0, latency_ms=0.0, fallback_used=True),
    )


class TestConstruction:
    def test_creates_missing_parent_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "deep" / "nested" / "run.jsonl"
        EventLogger(target)
        assert target.parent.is_dir()

    def test_bare_filename_does_not_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ``Path("run.jsonl").parent`` is ``.``; mkdir should treat it
        # as a no-op rather than raising on cwd creation.
        monkeypatch.chdir(tmp_path)
        EventLogger(Path("run.jsonl"))
        assert (tmp_path / "run.jsonl").exists()


class TestSingleLog:
    async def test_writes_one_jsonl_line(self, tmp_path: Path) -> None:
        path = tmp_path / "run.jsonl"
        logger = EventLogger(path)
        try:
            await logger.log_event(_event())
        finally:
            await logger.aclose()

        text = path.read_text(encoding="utf-8")
        assert text.endswith("\n")
        lines = text.splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["agent_id"] == "a-001"

    async def test_line_matches_to_jsonl(self, tmp_path: Path) -> None:
        path = tmp_path / "run.jsonl"
        logger = EventLogger(path)
        event = _event()
        try:
            await logger.log_event(event)
        finally:
            await logger.aclose()
        assert path.read_text(encoding="utf-8") == event.to_jsonl() + "\n"


class TestMultipleLogs:
    async def test_preserves_call_order(self, tmp_path: Path) -> None:
        path = tmp_path / "run.jsonl"
        logger = EventLogger(path)
        try:
            for i in range(5):
                await logger.log_event(_event(round_num=i, agent_id=f"a-{i:03d}"))
        finally:
            await logger.aclose()

        lines = path.read_text(encoding="utf-8").splitlines()
        assert [json.loads(line)["agent_id"] for line in lines] == [f"a-{i:03d}" for i in range(5)]

    async def test_appends_to_existing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "run.jsonl"

        logger_a = EventLogger(path)
        try:
            await logger_a.log_event(_event(round_num=0, agent_id="a-001"))
        finally:
            await logger_a.aclose()

        logger_b = EventLogger(path)
        try:
            await logger_b.log_event(_event(round_num=1, agent_id="a-002"))
        finally:
            await logger_b.aclose()

        lines = path.read_text(encoding="utf-8").splitlines()
        assert [json.loads(line)["agent_id"] for line in lines] == ["a-001", "a-002"]


class TestConcurrency:
    async def test_concurrent_writes_produce_whole_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "run.jsonl"
        logger = EventLogger(path)
        try:
            await asyncio.gather(
                *(logger.log_event(_event(round_num=i, agent_id=f"a-{i:04d}")) for i in range(100))
            )
        finally:
            await logger.aclose()

        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 100
        # Every line must be a complete JSON object — interleaving would
        # produce parse failures or missing required fields.
        agent_ids: set[str] = set()
        for line in lines:
            event = json.loads(line)
            agent_ids.add(event["agent_id"])
        assert agent_ids == {f"a-{i:04d}" for i in range(100)}


class TestLifecycle:
    async def test_log_after_aclose_raises(self, tmp_path: Path) -> None:
        logger = EventLogger(tmp_path / "run.jsonl")
        await logger.aclose()
        with pytest.raises(RuntimeError, match="already closed"):
            await logger.log_event(_event())

    async def test_aclose_is_idempotent(self, tmp_path: Path) -> None:
        logger = EventLogger(tmp_path / "run.jsonl")
        await logger.aclose()
        await logger.aclose()  # must not raise


class TestSchemaParity:
    async def test_written_lines_satisfy_round_event_schema(self, tmp_path: Path) -> None:
        path = tmp_path / "run.jsonl"
        logger = EventLogger(path)
        try:
            for i, action_type in enumerate(ActionType):
                if action_type is ActionType.CREATE_POST:
                    action = Action(type=action_type, content="hello")
                elif action_type in {ActionType.LIKE_POST, ActionType.REPOST}:
                    action = Action(type=action_type, target_post_id="p-001")
                elif action_type is ActionType.QUOTE_POST:
                    action = Action(type=action_type, target_post_id="p-001", content="quote")
                elif action_type is ActionType.FOLLOW:
                    action = Action(type=action_type, target_agent_id="a-002")
                else:
                    action = Action(type=action_type)

                await logger.log_event(
                    RoundEvent(
                        round_num=i,
                        timestamp=datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC),
                        agent_id="a-001",
                        action=action,
                    )
                )
        finally:
            await logger.aclose()

        validator = Draft7Validator(round_event_schema(), format_checker=FormatChecker())
        for line in path.read_text(encoding="utf-8").splitlines():
            validator.validate(json.loads(line))
