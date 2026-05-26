"""``litemiro-run`` CLI 테스트 — issue #53 DoD lock-in.

세 영역:
* argv 파싱 (필수 인자 / 기본값 / 커스텀 동시성 인자)
* `_run` 경계 직접 호출 (의존성 주입 lock-in)
* `main` 통합 — sample fixture + monkeypatch 한 fake LLM/Embedder 로 3 라운드
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


def test_parser_output_dir_default_is_none() -> None:
    """``--output-dir`` 미지정 시 argparse 가 None 을 돌려준다.

    실제 경로 (``runs/run-{ISO}/``) 는 ``_run`` 안에서 ``_default_output_dir``
    이 채운다 — 매 실행마다 새 timestamp 가 되도록.
    """
    args = run_cli._build_parser().parse_args(
        [
            "--ontology-a",
            str(_SAMPLE_A),
            "--ontology-b",
            str(_SAMPLE_B),
        ]
    )
    assert args.output_dir is None


def test_default_output_dir_is_under_runs_with_timestamp() -> None:
    """``_default_output_dir`` 는 ``runs/run-{timestamp}/`` 패턴 + 매 호출 다른 값."""
    a = run_cli._default_output_dir()
    b = run_cli._default_output_dir()
    assert a.parent == Path("runs")
    assert a.name.startswith("run-")
    assert b.name.startswith("run-")
    # 마이크로초 포함이라 같은 초에 호출돼도 달라야 한다.
    assert a != b


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


# ── _run 경계 직접 호출 ─────────────────────────────────────────────


async def test_run_boundary_accepts_injected_dependencies(tmp_path: Path) -> None:
    """``_run`` 의 docstring 약속 (의존성 주입 lock-in) 을 직접 호출로 검증.

    main 의 `LiteLLMClient` / `STEmbedder` 인스턴스화 단계를 우회한다.
    """
    args = run_cli._build_parser().parse_args(_argv_with_paths(tmp_path))
    result = await run_cli._run(args, llm_client=_FakeLLM(), embedder=_FakeEmbedder())

    assert result.rounds_run == 3
    assert result.early_exit is False
    assert result.event_log_path == tmp_path / "events.jsonl"
    assert result.checkpoint_dir == tmp_path / "checkpoints"


# ── main 통합 ────────────────────────────────────────────────────────


def _patch_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """``main`` 의 실 LiteLLMClient / STEmbedder 인스턴스화를 fake 로 대체.

    실 sentence-transformers / OpenRouter 키 없이도 통합이 돌아가도록.
    """
    monkeypatch.setattr(run_cli, "LiteLLMClient", _FakeLLM)
    monkeypatch.setattr(run_cli, "STEmbedder", _FakeEmbedder)
    # main 의 pre-flight 체크는 OPENROUTER_API_KEY 가 필요. 테스트에서는 더미값.
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")


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
    # sample fixture seed=42, post_rates 0.7/0.4/0.1 에서 round 0 / 2 는 활성
    # 0 명, round 1 만 agent_002 활성 → 정확히 1 라인. `>= 1` 로 두면 RNG 회귀
    # 가 라인 0 줄어들어도 못 잡으므로 정확값으로 pin.
    assert len(list(_read_lines(event_log))) == 1
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


def test_main_loads_dotenv_so_env_file_supplies_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``.env`` 의 ``OPENROUTER_API_KEY`` 가 main() 의 pre-flight 게이트를 통과시킨다.

    셸 export 없이 ``.env`` 만 있는 사용자도 ``litemiro-run`` 이 동작해야 한다.
    pre-flight 체크보다 ``load_dotenv()`` 가 먼저 호출되는 순서를 lock-in.
    """
    monkeypatch.setattr(run_cli, "LiteLLMClient", _FakeLLM)
    monkeypatch.setattr(run_cli, "STEmbedder", _FakeEmbedder)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    env_path = tmp_path / ".env"
    env_path.write_text("OPENROUTER_API_KEY=test-key-from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    exit_code = run_cli.main(_argv_with_paths(tmp_path))

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Error: OPENROUTER_API_KEY" not in captured.err
    assert "Rounds run     : 3" in captured.out


def test_main_returns_one_when_api_key_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """OPENROUTER_API_KEY 누락 시 LLM 호출 전에 빠른 실패.

    cwd 에 ``.env`` 가 있으면 ``load_dotenv()`` 가 채워버리므로 의도와 어긋남 —
    cwd 를 빈 tmp_path 로 옮겨 ``.env`` 부재 + env 변수 부재 조건을 보장한다.
    """
    monkeypatch.setattr(run_cli, "LiteLLMClient", _FakeLLM)
    monkeypatch.setattr(run_cli, "STEmbedder", _FakeEmbedder)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    exit_code = run_cli.main(_argv_with_paths(tmp_path))

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "OPENROUTER_API_KEY" in captured.err
    assert not (tmp_path / "events.jsonl").exists()
