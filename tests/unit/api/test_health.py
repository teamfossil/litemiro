"""``GET /api/health`` 단위 테스트."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from litemiro.api.app import create_app
from litemiro.api.store import ProgressCallback


async def _noop_runner(
    *,
    plaza_id: str,
    ontology_a_path: Path,
    ontology_b_path: Path,
    rounds: int,
    on_progress: ProgressCallback,
) -> None:
    del plaza_id, ontology_a_path, ontology_b_path, rounds, on_progress


def test_health_returns_ok_with_version() -> None:
    app = create_app(runner=_noop_runner)
    with TestClient(app) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["version"], str)
    assert body["version"]
