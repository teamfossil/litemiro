"""SSE 진행률 스트림 단위 테스트.

``PlazaRunner`` 를 fake 로 갈아끼워 외부 의존 없이 닫는다. SSE 자체는
TestClient 의 ``stream`` 컨텍스트로 받는다 — chunked transfer.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from litemiro.api.app import create_app
from litemiro.api.store import ProgressCallback, RunnerOutcome

_RunnerCoro = Callable[..., Coroutine[Any, Any, RunnerOutcome]]


def _success_runner(rounds_to_report: int, *, startup_delay: float = 0.05) -> _RunnerCoro:
    """라운드마다 ``on_progress`` 를 호출하고 끝낸다.

    ``startup_delay`` 는 라우트가 ``subscribe`` 할 시간을 주기 위해 진입 직후
    한 번 양보한다 — 없으면 fake 가 sleep(0) 만으로 한 틱 안에 끝나서 SSE
    구독 시점엔 이미 completed 인 race 가 매번 발생.
    """

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
        if startup_delay > 0:
            await asyncio.sleep(startup_delay)
        for r in range(rounds_to_report):
            await asyncio.sleep(0)
            on_progress(rounds_done=r + 1)
        return RunnerOutcome(tokens_used=42, rounds_run=rounds_to_report)

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
    raise RuntimeError("kaboom")


def _parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    """SSE wire 포맷 → ``(event, data)`` 리스트. comment(``: ...``) 와 빈 줄은 무시."""
    events: list[tuple[str, dict[str, Any]]] = []
    for chunk in text.split("\n\n"):
        if not chunk.strip() or chunk.lstrip().startswith(":"):
            continue
        name: str | None = None
        payload: str | None = None
        for line in chunk.split("\n"):
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                payload = line[len("data: ") :]
        if name is not None and payload is not None:
            events.append((name, json.loads(payload)))
    return events


def _create_plaza(client: TestClient, *, rounds: int = 3) -> str:
    resp = client.post(
        "/api/plazas",
        json={
            "ontology_a_path": "/tmp/a.json",
            "ontology_b_path": "/tmp/b.json",
            "rounds": rounds,
            "label": "sse-smoke",
        },
    )
    assert resp.status_code == 202
    return resp.json()["plaza_id"]  # type: ignore[no-any-return]


def test_unknown_plaza_returns_404(tmp_path: Path) -> None:
    app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/plazas/does-not-exist/events")
    assert resp.status_code == 404


def test_stream_yields_progress_and_terminates_on_completed(tmp_path: Path) -> None:
    """progress 가 라운드마다 들어오고 마지막 status="completed" 로 끝난다."""
    app = create_app(runner=_success_runner(rounds_to_report=3), base_dir=tmp_path)
    with TestClient(app) as client:
        plaza_id = _create_plaza(client, rounds=3)
        with client.stream("GET", f"/api/plazas/{plaza_id}/events") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            body = resp.read().decode("utf-8")

    events = _parse_sse(body)
    # 최소: 초기 status (또는 직후 status) + 진행 progress + terminal status.
    types = [e[0] for e in events]
    assert "status" in types
    # 마지막은 항상 terminal status.
    assert events[-1][0] == "status"
    assert events[-1][1]["status"] == "completed"
    assert events[-1][1]["rounds_total"] == 3
    # progress 이벤트는 라운드 수만큼 (또는 그 이하 — race 로 첫 progress 가
    # 초기 status 이전에 발생했을 수 있다. 최소 1 건은 보여야 한다).
    progress_events = [e for e in events if e[0] == "progress"]
    assert len(progress_events) >= 1
    # 모든 progress 페이로드가 rounds_total 을 들고 있다.
    for _, data in progress_events:
        assert data["rounds_total"] == 3
        assert 1 <= data["rounds_done"] <= 3


def test_stream_surfaces_failure_status_with_error(tmp_path: Path) -> None:
    """runner 가 예외로 죽으면 마지막 status="failed" + error 메시지가 와야 한다."""
    app = create_app(runner=_failing_runner, base_dir=tmp_path)
    with TestClient(app) as client:
        plaza_id = _create_plaza(client, rounds=2)
        with client.stream("GET", f"/api/plazas/{plaza_id}/events") as resp:
            assert resp.status_code == 200
            body = resp.read().decode("utf-8")

    events = _parse_sse(body)
    assert events[-1][0] == "status"
    assert events[-1][1]["status"] == "failed"
    assert "kaboom" in (events[-1][1].get("error") or "")


def test_stream_returns_immediately_when_already_completed(tmp_path: Path) -> None:
    """이미 끝난 plaza 를 구독해도 초기 status 하나만 받고 즉시 종료해야 한다.

    무한 대기로 hang 되면 SSE 관리 비용이 폭발한다 — 가장 비싼 회귀.
    """
    app = create_app(runner=_success_runner(rounds_to_report=2), base_dir=tmp_path)
    with TestClient(app) as client:
        plaza_id = _create_plaza(client, rounds=2)
        # 완료 보장 — /status 폴링은 기존 라우트.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            body = client.get(f"/api/plazas/{plaza_id}/status").json()
            if body["status"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        assert body["status"] == "completed"

        with client.stream("GET", f"/api/plazas/{plaza_id}/events") as resp:
            assert resp.status_code == 200
            text = resp.read().decode("utf-8")

    events = _parse_sse(text)
    # 초기 status 하나 — 곧장 종료. 추가 progress 이벤트는 없어야 한다.
    assert len(events) == 1
    assert events[0][0] == "status"
    assert events[0][1]["status"] == "completed"
