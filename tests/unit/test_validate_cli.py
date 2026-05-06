"""``litemiro-validate`` CLI behaviour."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from litemiro.cli.validate import main, validate_file

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "round_event_sample.jsonl"


def test_sample_jsonl_validates_clean(capsys: pytest.CaptureFixture[str]) -> None:
    rc = validate_file(SAMPLE)
    captured = capsys.readouterr()
    assert rc == 0
    assert "lines valid" in captured.err


def test_invalid_line_returns_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        json.dumps(
            {
                "round_num": 0,
                "timestamp": "2026-04-01T10:00:00+00:00",
                "agent_id": "a",
                "action": {"type": "CREATE_POST"},  # missing content
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rc = validate_file(bad)
    captured = capsys.readouterr()
    assert rc == 1
    assert "line 1" in captured.err


def test_main_returns_zero_for_sample() -> None:
    assert main(["--jsonl", str(SAMPLE)]) == 0


def test_invalid_json_raises_systemexit(tmp_path: Path) -> None:
    broken = tmp_path / "broken.jsonl"
    broken.write_text("not json\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="line 1"):
        validate_file(broken)


def test_non_object_top_level_raises_systemexit(tmp_path: Path) -> None:
    arr = tmp_path / "array.jsonl"
    arr.write_text("[1, 2, 3]\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="line 1"):
        validate_file(arr)


def test_blank_lines_are_skipped(tmp_path: Path) -> None:
    blanks = tmp_path / "blanks.jsonl"
    blanks.write_text("\n\n\n", encoding="utf-8")
    assert validate_file(blanks) == 0


def test_external_schema_path_supported(tmp_path: Path) -> None:
    """The ``--schema`` flag should accept a custom Draft 7 schema."""
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "required": ["x"],
                "properties": {"x": {"type": "integer"}},
            }
        ),
        encoding="utf-8",
    )
    data_path = tmp_path / "data.jsonl"
    data_path.write_text(json.dumps({"x": 1}) + "\n", encoding="utf-8")
    assert main(["--jsonl", str(data_path), "--schema", str(schema_path)]) == 0
