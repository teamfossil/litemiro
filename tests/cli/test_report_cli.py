"""``litemiro-report`` CLI 테스트 — Phase 3 진입점 DoD lock-in.

세 영역:
* argv 파싱 (필수 인자 / 기본값 / preset 선택)
* ``_run`` 경계 직접 호출 — primary 성공 / fallback 진입 두 경로 모두 검증
* ``main`` 통합 — 작성된 events.jsonl + fake LLM 으로 Markdown 산출, 실패 경로
  (events 누락 / API 키 누락 / .env 로딩) 까지 확인
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from litemiro.cli import report as report_cli
from litemiro.models import Action, ActionType, LLMResponse, RoundEvent
from litemiro.phase1.models import Preset


class _FakeLLM:
    """모든 호출 성공 — Composer 가 primary 모델로 끝낸다."""

    async def complete(self, *, system: str, user: str, model: str) -> LLMResponse:
        return LLMResponse(
            content=f"# 보고서\n## {model}\n요약 문장.",
            prompt_tokens=7,
            completion_tokens=11,
        )


class _FailingPrimaryLLM:
    """``--composer-primary-model`` 호출 시만 raise → Composer 폴백 진입."""

    def __init__(self, primary_model: str) -> None:
        self._primary_model = primary_model

    async def complete(self, *, system: str, user: str, model: str) -> LLMResponse:
        if model == self._primary_model:
            raise RuntimeError("primary blew up")
        return LLMResponse(
            content="# 폴백 보고서\n폴백 한 줄.",
            prompt_tokens=3,
            completion_tokens=5,
        )


def _write_sample_jsonl(path: Path) -> Path:
    """결정성 sample events.jsonl — 4 카테고리가 모두 비어있지 않게 채운다."""
    base = datetime(2026, 5, 26, 0, 0, 0, tzinfo=UTC)
    events = [
        RoundEvent(
            round_num=0,
            timestamp=base,
            agent_id="agent_001",
            action=Action(type=ActionType.CREATE_POST, content="첫 글 — 주제 A"),
        ),
        RoundEvent(
            round_num=0,
            timestamp=base,
            agent_id="agent_002",
            action=Action(type=ActionType.FOLLOW, target_agent_id="agent_001"),
        ),
        RoundEvent(
            round_num=1,
            timestamp=base,
            agent_id="agent_002",
            action=Action(
                type=ActionType.QUOTE_POST,
                target_post_id="p1",
                content="인용 — 주제 B",
            ),
        ),
        RoundEvent(
            round_num=1,
            timestamp=base,
            agent_id="agent_003",
            action=Action(type=ActionType.DO_NOTHING),
        ),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(e.to_jsonl() for e in events) + "\n", encoding="utf-8")
    return path


def _argv_for(events: Path, output: Path, *extra: str) -> list[str]:
    return [
        "--events",
        str(events),
        "--output",
        str(output),
        *extra,
    ]


# ── argv 파싱 ────────────────────────────────────────────────────────


def test_parser_requires_events() -> None:
    with pytest.raises(SystemExit):
        report_cli._build_parser().parse_args([])


def test_parser_defaults_match_spec(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    args = report_cli._build_parser().parse_args(["--events", str(events)])

    assert args.events == events
    assert args.output is None
    assert args.preset is Preset.QUICK
    assert args.analyzer_model == "openrouter/qwen/qwen-plus"
    assert args.composer_primary_model == "openrouter/anthropic/claude-opus-4.7"
    assert args.composer_fallback_model == "openrouter/qwen/qwen-plus"


def test_parser_accepts_preset_and_custom_models(tmp_path: Path) -> None:
    args = report_cli._build_parser().parse_args(
        [
            "--events",
            str(tmp_path / "events.jsonl"),
            "--preset",
            "standard",
            "--analyzer-model",
            "openrouter/x/y",
            "--composer-primary-model",
            "openrouter/p/q",
            "--composer-fallback-model",
            "openrouter/a/b",
        ]
    )
    assert args.preset is Preset.STANDARD
    assert args.analyzer_model == "openrouter/x/y"
    assert args.composer_primary_model == "openrouter/p/q"
    assert args.composer_fallback_model == "openrouter/a/b"


def test_parser_rejects_unknown_preset(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        report_cli._build_parser().parse_args(
            ["--events", str(tmp_path / "e.jsonl"), "--preset", "deluxe"]
        )


def test_default_output_is_under_reports_with_timestamp() -> None:
    a = report_cli._default_output()
    b = report_cli._default_output()
    assert a.parent == Path("reports")
    assert a.suffix == ".md"
    assert a.name.startswith("report-")
    # 마이크로초 포함이라 같은 초 호출도 충돌하지 않음.
    assert a != b


# ── _run 경계 직접 호출 ─────────────────────────────────────────────


async def test_run_boundary_writes_markdown_with_primary_model(tmp_path: Path) -> None:
    events = _write_sample_jsonl(tmp_path / "events.jsonl")
    output = tmp_path / "out" / "report.md"
    args = report_cli._build_parser().parse_args(_argv_for(events, output))

    summary = await report_cli._run(args, llm_client=_FakeLLM())

    assert summary.output_path == output
    assert summary.preset is Preset.QUICK
    assert summary.composer_fallback_used is False
    assert summary.composer_model == args.composer_primary_model
    assert summary.composer_tokens == 7 + 11
    # quick 프리셋 → analyzer 1 회 호출 → tokens 도 동일하게 18.
    assert summary.analyzer_total_tokens == 18
    assert "보고서" in summary.markdown
    # 디스크 쓰기는 main 에서 별도로 — _run 단계에서는 파일이 없어야 한다.
    assert not output.exists()


async def test_run_boundary_falls_back_when_primary_raises(tmp_path: Path) -> None:
    events = _write_sample_jsonl(tmp_path / "events.jsonl")
    output = tmp_path / "report.md"
    args = report_cli._build_parser().parse_args(_argv_for(events, output))
    llm = _FailingPrimaryLLM(primary_model=args.composer_primary_model)

    summary = await report_cli._run(args, llm_client=llm)

    assert summary.composer_fallback_used is True
    assert summary.composer_model == args.composer_fallback_model
    assert "폴백" in summary.markdown


async def test_run_boundary_respects_preset_call_count(tmp_path: Path) -> None:
    """standard 프리셋 → 4 카테고리, 카테고리당 1 회 = analyzer 가 4 회 호출된다."""
    events = _write_sample_jsonl(tmp_path / "events.jsonl")
    output = tmp_path / "report.md"
    args = report_cli._build_parser().parse_args(
        _argv_for(events, output, "--preset", "standard")
    )

    summary = await report_cli._run(args, llm_client=_FakeLLM())

    # 4 카테고리, 카테고리당 18 토큰 호출 → 합계 72.
    assert summary.analyzer_total_tokens == 72


# ── main 통합 ────────────────────────────────────────────────────────


def _patch_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report_cli, "LiteLLMClient", _FakeLLM)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")


def test_main_returns_zero_and_prints_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_dependencies(monkeypatch)
    events = _write_sample_jsonl(tmp_path / "events.jsonl")
    output = tmp_path / "report.md"

    exit_code = report_cli.main(_argv_for(events, output))

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Preset            : quick" in captured.out
    assert f"Output            : {output}" in captured.out
    assert "Composer fallback : False" in captured.out
    assert output.is_file()


def test_main_writes_markdown_with_composer_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dependencies(monkeypatch)
    events = _write_sample_jsonl(tmp_path / "events.jsonl")
    output = tmp_path / "nested" / "report.md"

    exit_code = report_cli.main(_argv_for(events, output))

    assert exit_code == 0
    content = output.read_text(encoding="utf-8")
    # _FakeLLM 의 응답 ("# 보고서\n## {model}\n요약 문장.") 이 그대로 본문.
    assert content.startswith("# 보고서")


def test_main_returns_one_when_events_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_dependencies(monkeypatch)
    missing = tmp_path / "no_such_events.jsonl"
    output = tmp_path / "report.md"

    exit_code = report_cli.main(_argv_for(missing, output))

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Error" in captured.err
    assert not output.exists()


def test_main_returns_one_when_api_key_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(report_cli, "LiteLLMClient", _FakeLLM)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # cwd 에 .env 가 있으면 load_dotenv 가 채워버린다 — 빈 tmp_path 로 격리.
    monkeypatch.chdir(tmp_path)
    events = _write_sample_jsonl(tmp_path / "events.jsonl")
    output = tmp_path / "report.md"

    exit_code = report_cli.main(_argv_for(events, output))

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "OPENROUTER_API_KEY" in captured.err
    assert not output.exists()


def test_main_loads_dotenv_so_env_file_supplies_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``.env`` 의 ``OPENROUTER_API_KEY`` 가 pre-flight 게이트를 통과시킨다.

    셸 export 없이 ``.env`` 만 있는 사용자도 ``litemiro-report`` 가 동작해야 한다.
    """
    monkeypatch.setattr(report_cli, "LiteLLMClient", _FakeLLM)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "OPENROUTER_API_KEY=test-key-from-dotenv\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    events = _write_sample_jsonl(tmp_path / "events.jsonl")
    output = tmp_path / "report.md"

    exit_code = report_cli.main(_argv_for(events, output))

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Error: OPENROUTER_API_KEY" not in captured.err
    assert output.is_file()


def test_main_default_output_is_written_under_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--output`` 미지정 시 ``./reports/report-{ts}.md`` 가 cwd 기준 생성."""
    _patch_dependencies(monkeypatch)
    monkeypatch.chdir(tmp_path)
    events = _write_sample_jsonl(tmp_path / "events.jsonl")

    exit_code = report_cli.main(["--events", str(events)])

    assert exit_code == 0
    reports_dir = tmp_path / "reports"
    assert reports_dir.is_dir()
    written = list(reports_dir.glob("report-*.md"))
    assert len(written) == 1
    assert written[0].read_text(encoding="utf-8").startswith("# 보고서")
    captured = capsys.readouterr()
    assert "Output            : reports/report-" in captured.out


def test_sample_events_jsonl_is_well_formed(tmp_path: Path) -> None:
    """fixture 가 깨지면 다른 모든 통합 테스트가 의미 없어지므로 가드 — 직접 검증."""
    path = _write_sample_jsonl(tmp_path / "events.jsonl")
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 4
    # Aggregator 가 읽을 수 있어야 한다.
    for line in lines:
        RoundEvent.model_validate(line)
