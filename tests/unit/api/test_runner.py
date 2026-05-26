"""``RealPlazaRunner`` 어댑터 — run_simulation 으로 인자가 그대로 흘러가는지만 본다.

실 LLM / embedder 는 무겁기 때문에 ``run_simulation`` 자체를 monkeypatch
해서 라이트하게 닫는다. 시뮬레이션 로직 검증은 ``tests/e2e`` 책임.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from litemiro.api import runner as runner_mod
from litemiro.api.runner import RealPlazaRunner
from litemiro.core._types import SimulationResult

if TYPE_CHECKING:
    from litemiro.interfaces import EmbedderLike, LLMClient


class _DummyLLM:
    pass


class _DummyEmbedder:
    pass


@pytest.mark.asyncio
async def test_real_runner_forwards_args_and_returns_outcome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_run_simulation(**kwargs: Any) -> SimulationResult:
        captured.update(kwargs)
        return SimulationResult(
            rounds_run=4,
            early_exit=False,
            event_log_path=kwargs["event_log_path"],
            checkpoint_dir=kwargs["checkpoint_dir"],
            tokens_used=1234,
        )

    monkeypatch.setattr(runner_mod, "run_simulation", _fake_run_simulation)

    llm: LLMClient = _DummyLLM()  # type: ignore[assignment]
    embedder: EmbedderLike = _DummyEmbedder()  # type: ignore[assignment]
    real = RealPlazaRunner(
        llm_client=llm,
        embedder=embedder,
        llm_model="dummy/model",
        token_budget=42,
        semaphore_limit=3,
        batch_size=5,
        cooldown_seconds=0.1,
    )

    progress_calls: list[int] = []

    def _on_progress(*, rounds_done: int) -> None:
        progress_calls.append(rounds_done)

    outcome = await real(
        plaza_id="abc",
        ontology_a_path=tmp_path / "a.json",
        ontology_b_path=tmp_path / "b.json",
        rounds=4,
        event_log_path=tmp_path / "events.jsonl",
        checkpoint_dir=tmp_path / "checkpoints",
        on_progress=_on_progress,
    )

    assert outcome.tokens_used == 1234
    assert progress_calls == [4]
    assert captured["llm_client"] is llm
    assert captured["embedder"] is embedder
    assert captured["llm_model"] == "dummy/model"
    assert captured["token_budget"] == 42
    assert captured["semaphore_limit"] == 3
    assert captured["batch_size"] == 5
    assert captured["cooldown_seconds"] == 0.1
    assert captured["rounds"] == 4
    assert captured["event_log_path"] == tmp_path / "events.jsonl"
    assert captured["checkpoint_dir"] == tmp_path / "checkpoints"
