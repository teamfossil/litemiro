"""Plaza POST/GET 라우트 단위 테스트.

`PlazaRunner` 를 fake 로 갈아끼워 외부 의존(LLM/embedder) 없이 닫는다.
status 머신: pending → running → completed | failed.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from litemiro.api.app import create_app
from litemiro.api.store import ProgressCallback, RunnerOutcome

_RunnerCoro = Callable[..., Coroutine[Any, Any, RunnerOutcome]]


def _success_runner(rounds_to_report: int, *, tokens: int = 0) -> _RunnerCoro:
    async def _run(
        *,
        plaza_id: str,
        ontology_a_path: Path,
        ontology_b_path: Path,
        rounds: int,
        event_log_path: Path,
        checkpoint_dir: Path,
        on_progress: ProgressCallback,
    ) -> RunnerOutcome:
        del plaza_id, ontology_a_path, ontology_b_path, rounds
        del event_log_path, checkpoint_dir
        for r in range(rounds_to_report):
            await asyncio.sleep(0)
            on_progress(rounds_done=r + 1)
        return RunnerOutcome(tokens_used=tokens)

    return _run


async def _failing_runner(
    *,
    plaza_id: str,
    ontology_a_path: Path,
    ontology_b_path: Path,
    rounds: int,
    event_log_path: Path,
    checkpoint_dir: Path,
    on_progress: ProgressCallback,
) -> RunnerOutcome:
    del plaza_id, ontology_a_path, ontology_b_path, rounds
    del event_log_path, checkpoint_dir, on_progress
    raise RuntimeError("boom")


def _wait_until(
    client: TestClient,
    plaza_id: str,
    *,
    terminal: set[str],
    timeout: float = 2.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/plazas/{plaza_id}/status")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] in terminal:
            return body  # type: ignore[no-any-return]
        time.sleep(0.01)
    raise AssertionError(f"plaza {plaza_id} did not reach {terminal} in {timeout}s")


class TestCreatePlaza:
    def test_returns_202_and_plaza_id(self, tmp_path: Path) -> None:
        app = create_app(runner=_success_runner(rounds_to_report=3), base_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.post(
                "/api/plazas",
                json={
                    "ontology_a_path": "/tmp/a.json",
                    "ontology_b_path": "/tmp/b.json",
                    "rounds": 3,
                    "label": "smoke",
                },
            )
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] in {"pending", "running", "completed"}
        assert isinstance(body["plaza_id"], str)
        assert len(body["plaza_id"]) >= 16

    def test_rejects_invalid_rounds(self, tmp_path: Path) -> None:
        app = create_app(runner=_success_runner(rounds_to_report=0), base_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.post(
                "/api/plazas",
                json={
                    "ontology_a_path": "/tmp/a.json",
                    "ontology_b_path": "/tmp/b.json",
                    "rounds": 0,
                },
            )
        assert resp.status_code == 422

    def test_rejects_unknown_field(self, tmp_path: Path) -> None:
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.post(
                "/api/plazas",
                json={
                    "ontology_a_path": "/tmp/a.json",
                    "ontology_b_path": "/tmp/b.json",
                    "rounds": 1,
                    "rogue": "nope",
                },
            )
        assert resp.status_code == 422


class TestGetStatus:
    def test_404_for_unknown_id(self, tmp_path: Path) -> None:
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.get("/api/plazas/does-not-exist/status")
        assert resp.status_code == 404

    def test_reaches_completed_with_progress(self, tmp_path: Path) -> None:
        app = create_app(runner=_success_runner(rounds_to_report=3), base_dir=tmp_path)
        with TestClient(app) as client:
            created = client.post(
                "/api/plazas",
                json={
                    "ontology_a_path": "/tmp/a.json",
                    "ontology_b_path": "/tmp/b.json",
                    "rounds": 3,
                    "label": "demo",
                },
            ).json()
            body = _wait_until(client, created["plaza_id"], terminal={"completed", "failed"})
        assert body["status"] == "completed"
        assert body["rounds_total"] == 3
        assert body["rounds_done"] == 3
        assert body["label"] == "demo"
        assert body["error"] is None

    def test_failure_surfaces_error(self, tmp_path: Path) -> None:
        app = create_app(runner=_failing_runner, base_dir=tmp_path)
        with TestClient(app) as client:
            created = client.post(
                "/api/plazas",
                json={
                    "ontology_a_path": "/tmp/a.json",
                    "ontology_b_path": "/tmp/b.json",
                    "rounds": 2,
                },
            ).json()
            body = _wait_until(client, created["plaza_id"], terminal={"completed", "failed"})
        assert body["status"] == "failed"
        assert body["error"] is not None
        assert "boom" in body["error"]
        assert body["rounds_done"] == 0
