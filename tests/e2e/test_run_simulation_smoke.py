"""``run_simulation`` smoke + 결정성 — Loader → RunBootstrap → JSONL.

issue #52 DoD 의 "sample fixture 기반 1 round 결정성 E2E (Loader → RunBootstrap
→ state diff)" 를 그대로 lock-in. 실 LLM / 실 embedder 없이 deterministic fake
로 닫는다 — CLI (#53) 가 실 sentence-transformers + LiteLLM 을 주입할 책임.

본 e2e 는 두 가지를 동시에 잡는다:

* **wiring 정합성**: OntologyLoader → StateStore → RoundManager 가 인자 미스
  매치 없이 한 번에 결선되어 JSONL 까지 떨어진다.
* **결정성**: 동일 입력 + 동일 seed → 동일 JSONL 라인 수 / 동일 액션 타입 시퀀스.
  타임스탬프는 ``datetime.now`` 이므로 비교 대상에서 제외 (RoundEvent 의 wire
  format 안정성은 EventLogger 단위 테스트가 따로 검증).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from litemiro.cli.validate import validate_file
from litemiro.integration.run import run_simulation
from litemiro.models import LLMResponse

if TYPE_CHECKING:
    from litemiro.interfaces import LLMClient

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_SAMPLE_A = _DATA_DIR / "sample_ontology_a.json"
_SAMPLE_B = _DATA_DIR / "sample_ontology_b.json"


class _FakeEmbedder:
    """결정적 임베더. SHA-256 다이제스트 → 8 차원 [0, 1) 벡터.

    실 sentence-transformers 의 의미 보존 대신 결정성만 보장 — TopicExtractor
    가 vocabulary 임베딩 캐시를 만들 때 zero-vector 등의 엣지 케이스를 피하기
    위해 충분히 분산된 값이 필요해 hash 기반으로 선택.
    """

    def embed(self, text: str) -> tuple[float, ...]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return tuple(b / 255.0 for b in digest[:8])


class _FakeLLM:
    """``LLMClient`` Protocol 만족. 모든 응답을 한 가지 invalid JSON 으로 — 따라서
    ActionSelector 는 모두 DO_NOTHING 으로 폴백, llm_meta.fallback_used=True 가 됨.

    DO_NOTHING 만으로 충분한 이유: 본 e2e 는 wiring 정합성 + 결정성을 잡는
    smoke 이고, 액션 분기별 부수효과는 RoundManager 단위 테스트가 이미 lock-in
    했다.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *, system: str, user: str, model: str) -> LLMResponse:
        self.calls += 1
        return LLMResponse(content="not json", prompt_tokens=5, completion_tokens=5)


def _llm_factory() -> LLMClient:
    return _FakeLLM()


async def _run(tmp_path: Path) -> tuple[Path, Path, int, int, bool]:
    """한 번의 시뮬레이션 → (event_log, checkpoint_dir, rounds_run, llm_calls, early_exit)."""
    event_log_path = tmp_path / "events.jsonl"
    checkpoint_dir = tmp_path / "checkpoints"
    llm = _FakeLLM()
    result = await run_simulation(
        ontology_a_path=_SAMPLE_A,
        ontology_b_path=_SAMPLE_B,
        llm_client=llm,
        embedder=_FakeEmbedder(),
        topic_vocabulary=("정치", "경제", "기술", "문화"),
        rounds=3,
        event_log_path=event_log_path,
        checkpoint_dir=checkpoint_dir,
        llm_model="fake-model",
    )
    return event_log_path, checkpoint_dir, result.rounds_run, llm.calls, result.early_exit


# ── tests ────────────────────────────────────────────────────────────


async def test_run_simulation_produces_jsonl_and_checkpoints(tmp_path: Path) -> None:
    event_log_path, checkpoint_dir, rounds_run, _llm_calls, early_exit = await _run(tmp_path)

    assert rounds_run == 3
    assert early_exit is False
    assert event_log_path.is_file()
    assert checkpoint_dir.is_dir()
    # 라운드 0/1/2 모두 체크포인트 — 단, StateStore._prune_old_checkpoints(keep=3)
    # 가 keep=3 이라 3 개 모두 유지된다.
    saved = sorted(p.name for p in checkpoint_dir.glob("checkpoint_round_*.json"))
    assert saved == [
        "checkpoint_round_0000.json",
        "checkpoint_round_0001.json",
        "checkpoint_round_0002.json",
    ]


async def test_run_simulation_jsonl_lines_match_llm_calls(tmp_path: Path) -> None:
    """활성 에이전트 한 명 당 한 LLM 호출, 한 RoundEvent 라인."""
    event_log_path, _, _, llm_calls, _ = await _run(tmp_path)

    lines = [
        line for line in event_log_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(lines) == llm_calls
    for line in lines:
        payload = json.loads(line)
        assert payload["action"]["type"] == "DO_NOTHING"  # fallback 폴백 경로
        assert payload["llm_meta"]["fallback_used"] is True


async def test_run_simulation_jsonl_passes_round_event_schema(tmp_path: Path) -> None:
    """contract Section 8.1 / Issue #25 DoD ③: 산출 JSONL 이 `round_event.schema.json`
    스키마를 모든 라인에서 통과한다. RunBootstrap 이 Phase 3 ingest 게이트와
    정합함을 lock-in 한다.
    """
    event_log_path, *_ = await _run(tmp_path)
    assert validate_file(event_log_path) == 0


async def test_run_simulation_is_deterministic_across_runs(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """동일 입력 + 동일 seed → 동일 라인 수 + 동일 (agent_id, action.type) 시퀀스.

    타임스탬프는 ``datetime.now(UTC)`` 라 두 실행에서 달라지므로 비교 대상에서
    제외한다 — JSONL wire 안정성은 EventLogger 단위 테스트가 별도 검증.
    """
    log_a, *_ = await _run(tmp_path_factory.mktemp("run-a"))
    log_b, *_ = await _run(tmp_path_factory.mktemp("run-b"))

    def _digest(path: Path) -> list[tuple[int, str, str]]:
        out: list[tuple[int, str, str]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            out.append((payload["round_num"], payload["agent_id"], payload["action"]["type"]))
        return out

    assert _digest(log_a) == _digest(log_b)


async def test_run_simulation_rejects_negative_rounds(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="rounds"):
        await run_simulation(
            ontology_a_path=_SAMPLE_A,
            ontology_b_path=_SAMPLE_B,
            llm_client=_FakeLLM(),
            embedder=_FakeEmbedder(),
            topic_vocabulary=("정치",),
            rounds=-1,
            event_log_path=tmp_path / "events.jsonl",
            checkpoint_dir=tmp_path / "checkpoints",
        )
