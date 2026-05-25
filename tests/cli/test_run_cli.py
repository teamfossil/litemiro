"""``litemiro-run`` CLI 테스트 — issue #53 DoD lock-in.

세 영역:
* argv 파싱 (필수 인자 / 기본값)
* topic vocabulary 추출 결정성 (sample fixture)
* main 통합 — sample fixture + monkeypatch 한 fake LLM/Embedder 로 1 라운드+
  실행이 정상 종료하고 stdout 에 결과 요약이 떨어지는지 검증
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from litemiro.cli import run as run_cli
from litemiro.models import LLMResponse

if TYPE_CHECKING:
    from collections.abc import Iterable

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_SAMPLE_A = _DATA_DIR / "sample_ontology_a.json"
_SAMPLE_B = _DATA_DIR / "sample_ontology_b.json"


class _FakeEmbedder:
    def embed(self, text: str) -> tuple[float, ...]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return tuple(b / 255.0 for b in digest[:8])


class _FakeLLM:
    async def complete(self, *, system: str, user: str, model: str) -> LLMResponse:
        return LLMResponse(content="not json", prompt_tokens=5, completion_tokens=5)


def _argv_with_paths(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--ontology-a",
        str(_SAMPLE_A),
        "--ontology-b",
        str(_SAMPLE_B),
        "--output-dir",
        str(tmp_path),
        "--rounds",
        "3",
        *extra,
    ]


# ── argv 파싱 ────────────────────────────────────────────────────────


def test_parser_requires_ontology_a_and_b() -> None:
    parser = run_cli._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_parser_defaults_match_spec(tmp_path: Path) -> None:
    args = run_cli._build_parser().parse_args(_argv_with_paths(tmp_path))

    assert args.ontology_a == _SAMPLE_A
    assert args.ontology_b == _SAMPLE_B
    assert args.rounds == 3
    assert args.output_dir == tmp_path
    assert args.llm_model == "openrouter/qwen/qwen-plus"
    assert args.token_budget == 1_000_000
    assert args.semaphore_limit == 10
    assert args.batch_size == 20
    assert args.cooldown_seconds == pytest.approx(0.5)


def test_parser_accepts_custom_concurrency(tmp_path: Path) -> None:
    args = run_cli._build_parser().parse_args(
        _argv_with_paths(
            tmp_path,
            "--semaphore-limit",
            "2",
            "--batch-size",
            "1",
            "--cooldown-seconds",
            "0",
            "--token-budget",
            "5000",
        )
    )
    assert args.semaphore_limit == 2
    assert args.batch_size == 1
    assert args.cooldown_seconds == 0.0
    assert args.token_budget == 5000


# ── topic vocabulary ─────────────────────────────────────────────────


def test_topic_vocabulary_is_union_of_agent_topics_sorted() -> None:
    """sample fixture: agent_001=정치/경제, agent_002=기술/경제, agent_003=문화
    → union {경제, 기술, 문화, 정치}, 정렬.
    """
    vocab = run_cli._topic_vocabulary(
        ontology_a_path=_SAMPLE_A,
        ontology_b_path=_SAMPLE_B,
    )
    assert vocab == ("경제", "기술", "문화", "정치")


# ── main 통합 ────────────────────────────────────────────────────────


def _patch_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """``main`` 의 실 LiteLLMClient / STEmbedder 인스턴스화를 fake 로 대체.

    실 sentence-transformers / OpenRouter 키 없이도 통합이 돌아가도록.
    """
    monkeypatch.setattr(run_cli, "LiteLLMClient", _FakeLLM)
    monkeypatch.setattr(run_cli, "STEmbedder", _FakeEmbedder)


def _read_lines(path: Path) -> Iterable[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_main_returns_zero_on_success_and_prints_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_dependencies(monkeypatch)

    exit_code = run_cli.main(_argv_with_paths(tmp_path))

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Rounds run     : 3" in captured.out
    assert "Early exit     : False" in captured.out
    assert str(tmp_path / "events.jsonl") in captured.out
    assert str(tmp_path / "checkpoints") in captured.out


def test_main_produces_jsonl_and_checkpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dependencies(monkeypatch)

    exit_code = run_cli.main(_argv_with_paths(tmp_path))

    assert exit_code == 0
    event_log = tmp_path / "events.jsonl"
    checkpoint_dir = tmp_path / "checkpoints"
    assert event_log.is_file()
    assert checkpoint_dir.is_dir()
    # 활성 에이전트당 한 라인. sample fixture seed=42 에서 round 0+1+2 합산.
    assert len(list(_read_lines(event_log))) >= 1
    assert any(checkpoint_dir.glob("checkpoint_round_*.json"))


def test_main_returns_one_and_prints_error_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ontology JSON 이 없으면 OntologyLoader 가 ValueError → exit 1."""
    _patch_dependencies(monkeypatch)
    argv = [
        "--ontology-a",
        str(tmp_path / "missing_a.json"),
        "--ontology-b",
        str(tmp_path / "missing_b.json"),
        "--output-dir",
        str(tmp_path),
        "--rounds",
        "1",
    ]

    exit_code = run_cli.main(argv)

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Error" in captured.err
