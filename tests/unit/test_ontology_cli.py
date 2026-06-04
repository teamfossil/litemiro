"""``litemiro-ontology`` CLI behaviour."""

from __future__ import annotations

import pytest

from litemiro.cli import ontology


def test_main_loads_dotenv_before_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def fake_load_dotenv() -> bool:
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(ontology, "load_dotenv", fake_load_dotenv)

    with pytest.raises(SystemExit):
        ontology.main([])

    assert called


def test_main_rejects_invalid_profile_max_concurrency_env(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LITEMIRO_PHASE1_PROFILE_MAX_CONCURRENCY", "0")

    with pytest.raises(SystemExit):
        ontology.main(["--input", "doc.txt", "--requirement", "req"])

    assert "must be greater than 0" in capsys.readouterr().err
