"""``GET /api/plazas/{id}/report`` 단위 테스트.

events.jsonl 을 직접 써넣는 fake runner 로 닫는다 — 실 simulation 의 결정성
검증은 e2e 책임, 본 단위는 ``build_report`` 매퍼와 404/409 흐름만 본다.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from litemiro.api.app import create_app
from litemiro.api.composer import ComposerOutcome
from litemiro.api.store import ProgressCallback, RunnerOutcome


def _make_event(round_num: int, agent_id: str, action_type: str, **action: Any) -> str:
    payload = {
        "round_num": round_num,
        "timestamp": datetime.now(UTC).isoformat(),
        "agent_id": agent_id,
        "action": {"type": action_type, **action},
    }
    return json.dumps(payload, sort_keys=True)


def _writing_runner(lines: list[str]) -> Any:
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
        del plaza_id, ontology_a_path, ontology_b_path, checkpoint_dir
        # sub-millisecond write — sync 가 깔끔하다.
        event_log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")  # noqa: ASYNC240
        on_progress(rounds_done=rounds)
        return RunnerOutcome(tokens_used=777)

    return _run


def _wait_completed(client: TestClient, plaza_id: str, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/plazas/{plaza_id}/status")
        if resp.json()["status"] == "completed":
            return
        time.sleep(0.01)
    raise AssertionError(f"plaza {plaza_id} did not complete in {timeout}s")


def test_report_returns_aggregation_for_completed_plaza(tmp_path: Path) -> None:
    lines = [
        _make_event(0, "agent_a", "CREATE_POST", content="hello world"),
        _make_event(0, "agent_b", "FOLLOW", target_agent_id="agent_a"),
        _make_event(1, "agent_a", "CREATE_POST", content="another post here"),
    ]
    app = create_app(runner=_writing_runner(lines), base_dir=tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/plazas",
            json={
                "ontology_a_path": "/tmp/a.json",
                "ontology_b_path": "/tmp/b.json",
                "rounds": 2,
                "label": "report-test",
            },
        ).json()
        _wait_completed(client, created["plaza_id"])
        resp = client.get(f"/api/plazas/{created['plaza_id']}/report")
    assert resp.status_code == 200
    body = resp.json()
    assert body["plaza_id"] == created["plaza_id"]
    assert body["label"] == "report-test"
    assert body["status"] == "completed"
    assert body["tokens_used"] == 777
    assert body["n_events"] == 3
    assert body["n_agents"] == 2
    assert body["n_rounds"] == 2
    actions = body["categories"]["action_distribution"]["counts"]
    assert actions["CREATE_POST"] == 2
    assert actions["FOLLOW"] == 1
    assert body["categories"]["network_metrics"]["n_follow_events"] == 1


def test_report_falls_back_to_empty_when_no_jsonl(tmp_path: Path) -> None:
    """``--fake`` 모드처럼 runner 가 events.jsonl 을 안 쓰는 경우 — 500 대신 빈 집계."""

    async def _noop_runner(
        *,
        plaza_id: str,
        ontology_a_path: Path,
        ontology_b_path: Path,
        rounds: int,
        event_log_path: Path,
        checkpoint_dir: Path,
        on_progress: ProgressCallback,
    ) -> RunnerOutcome:
        del plaza_id, ontology_a_path, ontology_b_path
        del event_log_path, checkpoint_dir
        for r in range(rounds):
            on_progress(rounds_done=r + 1)
        return RunnerOutcome()

    app = create_app(runner=_noop_runner, base_dir=tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/plazas",
            json={
                "ontology_a_path": "/tmp/a.json",
                "ontology_b_path": "/tmp/b.json",
                "rounds": 1,
            },
        ).json()
        _wait_completed(client, created["plaza_id"])
        resp = client.get(f"/api/plazas/{created['plaza_id']}/report")
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_events"] == 0
    assert body["n_agents"] == 0
    assert body["categories"]["action_distribution"]["total"] == 1  # divisor fallback


def test_report_404_for_unknown_plaza(tmp_path: Path) -> None:
    app = create_app(runner=_writing_runner([]), base_dir=tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/plazas/does-not-exist/report")
    assert resp.status_code == 404


def _stub_composer(markdown: str | None, *, tokens: int = 0, fallback: bool = False) -> Any:
    """events.jsonl 은 무시하고 미리 정한 outcome 만 돌려주는 fake composer."""

    async def _compose(*, plaza_id: str, event_log_path: Path) -> ComposerOutcome:
        del plaza_id, event_log_path
        return ComposerOutcome(markdown=markdown, tokens_used=tokens, fallback_used=fallback)

    return _compose


def test_report_includes_markdown_when_composer_provided(tmp_path: Path) -> None:
    """step 4 — composer 가 붙어 있으면 /report 에 markdown + 회계 노출."""
    lines = [_make_event(0, "agent_a", "CREATE_POST", content="hello")]
    app = create_app(
        runner=_writing_runner(lines),
        base_dir=tmp_path,
        composer=_stub_composer("# 보고서\n본문.", tokens=123, fallback=True),
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/plazas",
            json={
                "ontology_a_path": "/tmp/a.json",
                "ontology_b_path": "/tmp/b.json",
                "rounds": 1,
                "label": "with-composer",
            },
        ).json()
        _wait_completed(client, created["plaza_id"])
        resp = client.get(f"/api/plazas/{created['plaza_id']}/report")
    assert resp.status_code == 200
    body = resp.json()
    assert body["report_markdown"] == "# 보고서\n본문."
    assert body["report_fallback_used"] is True
    # runner 토큰 (777) + composer 토큰 (123) = 900.
    assert body["tokens_used"] == 900


def test_report_keeps_markdown_null_when_composer_dies(tmp_path: Path) -> None:
    """composer 가 markdown=None 으로 돌려줘도 /report 는 200 + 통계만 응답."""
    lines = [_make_event(0, "agent_a", "CREATE_POST", content="hello")]
    app = create_app(
        runner=_writing_runner(lines),
        base_dir=tmp_path,
        composer=_stub_composer(None, tokens=42),
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/plazas",
            json={
                "ontology_a_path": "/tmp/a.json",
                "ontology_b_path": "/tmp/b.json",
                "rounds": 1,
            },
        ).json()
        _wait_completed(client, created["plaza_id"])
        resp = client.get(f"/api/plazas/{created['plaza_id']}/report")
    assert resp.status_code == 200
    body = resp.json()
    assert body["report_markdown"] is None
    assert body["report_fallback_used"] is False
    assert body["n_events"] == 1
    assert body["tokens_used"] == 777 + 42


def test_report_409_while_running(tmp_path: Path) -> None:
    async def _slow_runner(
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
        await asyncio.sleep(0.5)
        return RunnerOutcome()

    app = create_app(runner=_slow_runner, base_dir=tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/plazas",
            json={
                "ontology_a_path": "/tmp/a.json",
                "ontology_b_path": "/tmp/b.json",
                "rounds": 1,
            },
        ).json()
        resp = client.get(f"/api/plazas/{created['plaza_id']}/report")
    assert resp.status_code == 409
