"""``RealPlazaComposer`` 단위 — events.jsonl → Markdown 어댑터.

LLM 호출은 conftest 의 ``fake_llm`` fixture 로 닫는다. Phase 3 파이프라인
(`PatternAnalyzer` → `ReportComposer`) 자체의 회귀는 별도 단위 테스트가
보고 있어서 본 파일은 어댑터 표면만 본다 — 폴백 시 markdown=None / 토큰
회계 / events.jsonl 부재 폴백.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from litemiro.api.composer import RealPlazaComposer
from litemiro.interfaces import LLMClient
from litemiro.models import LLMResponse


def _make_event(round_num: int, agent_id: str, action_type: str, **action: Any) -> str:
    payload = {
        "round_num": round_num,
        "timestamp": datetime.now(UTC).isoformat(),
        "agent_id": agent_id,
        "action": {"type": action_type, **action},
    }
    return json.dumps(payload, sort_keys=True)


def _write_sample_events(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                _make_event(0, "agent_a", "CREATE_POST", content="hello world"),
                _make_event(0, "agent_b", "FOLLOW", target_agent_id="agent_a"),
                _make_event(1, "agent_a", "CREATE_POST", content="another one"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_composer_returns_markdown_with_fake_llm(
    tmp_path: Path, fake_llm: Callable[..., LLMClient]
) -> None:
    events = tmp_path / "events.jsonl"
    _write_sample_events(events)
    # quick preset → analyzer 1 콜 (overview) + composer 1 콜.
    llm = fake_llm(
        LLMResponse(content="analyzer overview", prompt_tokens=10, completion_tokens=20),
        LLMResponse(content="# Plaza 보고서\n본문.", prompt_tokens=30, completion_tokens=40),
    )
    composer = RealPlazaComposer(llm_client=llm)
    outcome = asyncio.run(composer(plaza_id="abc", event_log_path=events))
    assert outcome.markdown == "# Plaza 보고서\n본문."
    assert outcome.fallback_used is False
    # analyzer tokens (10+20) + composer tokens (30+40) = 100.
    assert outcome.tokens_used == 100


def test_composer_returns_none_when_events_missing(
    tmp_path: Path, fake_llm: Callable[..., LLMClient]
) -> None:
    """``--fake`` runner 가 events.jsonl 을 안 쓰는 경우 — LLM 안 부르고 None."""
    missing = tmp_path / "events.jsonl"
    llm = fake_llm()  # 응답 없음 — 호출되면 RuntimeError 가 난다.
    composer = RealPlazaComposer(llm_client=llm)
    outcome = asyncio.run(composer(plaza_id="abc", event_log_path=missing))
    assert outcome.markdown is None
    assert outcome.tokens_used == 0


def test_composer_falls_back_to_none_when_composer_dies(
    tmp_path: Path, fake_llm: Callable[..., LLMClient]
) -> None:
    """Composer 의 Opus + Qwen 폴백 모두 실패 시 markdown=None.

    PatternAnalyzer 는 stats-only 폴백을 자체적으로 가지고 있으므로 본 시나리오는
    analyzer 는 LLM 으로 통과시키되 composer 단계만 실패시킨다. tenacity 가
    primary 를 두 번 재시도하므로 응답 큐에 충분한 실패 응답을 미리 채워둔다.
    """

    class _FailingComposerLLM:
        """analyzer 호출 1 회는 정상, 그 뒤 모든 호출은 RuntimeError."""

        def __init__(self) -> None:
            self._analyzer_left = 1
            self.calls = 0

        async def complete(self, *, system: str, user: str, model: str) -> LLMResponse:
            del system, user
            self.calls += 1
            if self._analyzer_left > 0:
                self._analyzer_left -= 1
                return LLMResponse(content="analyzer ok", prompt_tokens=5, completion_tokens=5)
            raise RuntimeError(f"{model} dead")

    events = tmp_path / "events.jsonl"
    _write_sample_events(events)
    llm = _FailingComposerLLM()
    composer = RealPlazaComposer(llm_client=llm)
    outcome = asyncio.run(composer(plaza_id="abc", event_log_path=events))
    assert outcome.markdown is None
    # analyzer 의 10 토큰은 그대로 회계에 잡혀야 한다 (composer 단계만 실패).
    assert outcome.tokens_used == 10
    assert outcome.fallback_used is False
