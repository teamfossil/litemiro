"""Plaza POST/GET 라우트 단위 테스트.

`PlazaRunner` 를 fake 로 갈아끼워 외부 의존(LLM/embedder) 없이 닫는다.
status 머신: pending → running → completed | failed.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from litemiro.api import db as _db
from litemiro.api.app import create_app
from litemiro.api.composer import ComposerOutcome
from litemiro.api.sample_fixtures import (
    DEFAULT_ONTOLOGY_A_PATH,
    DEFAULT_ONTOLOGY_B_PATH,
)
from litemiro.api.store import PlazaRecord, ProgressCallback, RunnerOutcome
from litemiro.phase1.models import Preset


def _write_ontology_a(path: Path, agent_specs: list[tuple[str, str, str, float]]) -> Path:
    """테스트용 ``ontology_a_persona.json`` 을 만든다.

    ``agent_specs`` 는 ``(agent_id, name, entity_type, ideology)`` 튜플. 라우트가
    실제 ``OntologyA.model_validate`` 를 거치므로 모든 필수 필드를 채워야 한다.
    """
    data = {
        "version": 1,
        "seed": 42,
        "agent_count": len(agent_specs),
        "preset": "quick",
        "source_document": "test-doc",
        "simulation_requirement": "test-req",
        "generated_at": "2026-05-26T00:00:00+00:00",
        "ontology": {"entity_types": [], "edge_types": []},
        "agents": {
            aid: {
                "agent_id": aid,
                "name": name,
                "entity_type": etype,
                "origin": "extracted",
                "derived_from": None,
                "skeleton": {},
                "ideology": ideology,
                "topics": [f"{aid}-topic"],
                "sensitive_topics": [],
                "personality": "",
                "speech_style": "",
                "background": "",
                "behavior_tendency": {
                    "post_rate": 0.5,
                    "reply_rate": 0.3,
                    "repost_rate": 0.2,
                    "controversy_affinity": 0.5,
                },
                "initial_following": [],
            }
            for (aid, name, etype, ideology) in agent_specs
        },
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


_RunnerCoro = Callable[..., Coroutine[Any, Any, RunnerOutcome]]


def _success_runner(
    rounds_to_report: int,
    *,
    tokens: int = 0,
    report_rounds_run: bool = False,
) -> _RunnerCoro:
    """``report_rounds_run=True`` 면 outcome 에 실 라운드 수를 같이 실어준다 —
    early-exit 시나리오 모킹용. 기본값(False) 은 기존 동작 유지."""

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
        if report_rounds_run:
            return RunnerOutcome(tokens_used=tokens, rounds_run=rounds_to_report)
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

    def test_omitted_paths_fall_back_to_sample_fixtures(self, tmp_path: Path) -> None:
        """두 경로 모두 생략 시 라우트가 repo dev fixture 로 채워야 한다.

        프론트 Seed 화면이 자료 업로드 UI 없이 항상 같은 sample 로 호출하는
        패턴 — 호출 측이 dummy path 를 박지 않게 한 게 핵심. runner 에 들어간
        경로가 ``DEFAULT_ONTOLOGY_*_PATH`` 와 일치하는지 직접 잡는다.
        """
        captured: dict[str, Path] = {}

        async def _capture_runner(
            *,
            plaza_id: str,
            ontology_a_path: Path,
            ontology_b_path: Path,
            rounds: int,
            event_log_path: Path,
            checkpoint_dir: Path,
            on_progress: ProgressCallback,
        ) -> RunnerOutcome:
            del plaza_id, event_log_path, checkpoint_dir
            captured["a"] = ontology_a_path
            captured["b"] = ontology_b_path
            for r in range(rounds):
                await asyncio.sleep(0)
                on_progress(rounds_done=r + 1)
            return RunnerOutcome()

        app = create_app(runner=_capture_runner, base_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.post("/api/plazas", json={"rounds": 1})
            assert resp.status_code == 202
            _wait_until(client, resp.json()["plaza_id"], terminal={"completed", "failed"})
        assert captured["a"] == DEFAULT_ONTOLOGY_A_PATH
        assert captured["b"] == DEFAULT_ONTOLOGY_B_PATH

    def test_explicit_path_overrides_default(self, tmp_path: Path) -> None:
        """한쪽만 명시한 경우, 명시한 쪽은 그대로 / 생략한 쪽만 default 로 폴백."""
        captured: dict[str, Path] = {}

        async def _capture_runner(
            *,
            plaza_id: str,
            ontology_a_path: Path,
            ontology_b_path: Path,
            rounds: int,
            event_log_path: Path,
            checkpoint_dir: Path,
            on_progress: ProgressCallback,
        ) -> RunnerOutcome:
            del plaza_id, event_log_path, checkpoint_dir
            captured["a"] = ontology_a_path
            captured["b"] = ontology_b_path
            for r in range(rounds):
                await asyncio.sleep(0)
                on_progress(rounds_done=r + 1)
            return RunnerOutcome()

        app = create_app(runner=_capture_runner, base_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.post(
                "/api/plazas",
                json={"ontology_a_path": "/tmp/custom-a.json", "rounds": 1},
            )
            assert resp.status_code == 202
            _wait_until(client, resp.json()["plaza_id"], terminal={"completed", "failed"})
        assert captured["a"] == Path("/tmp/custom-a.json")
        assert captured["b"] == DEFAULT_ONTOLOGY_B_PATH

    def test_empty_string_path_rejected(self, tmp_path: Path) -> None:
        """`""` 는 422 — 명시한 경로라면 길이 1 이상이어야 한다.

        omit 가능하다고 ``""`` 를 허용하면 default 폴백 의도와 헷갈리므로
        Field(min_length=1) 으로 명시적으로 막는다.
        """
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.post(
                "/api/plazas",
                json={"ontology_a_path": "", "rounds": 1},
            )
        assert resp.status_code == 422

    def test_default_fixture_resolves_to_real_agents(self, tmp_path: Path) -> None:
        """경로 omit → 라우트가 채운 default fixture 가 실제로 /agents 로 읽힌다.

        sample_ontology_a.json 의 agents (agent_001..) 가 그대로 노출되는지로
        패키지 fixture 가 batched export 경로와 정합한지 확인.
        """
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            created = client.post("/api/plazas", json={"rounds": 1}).json()
            plaza_id = created["plaza_id"]
            agents_resp = client.get(f"/api/plazas/{plaza_id}/agents")
        assert agents_resp.status_code == 200
        body = agents_resp.json()
        ids = {a["id"] for a in body["agents"]}
        # sample_ontology_a.json 은 agent_001 ~ agent_003. 정확히 일치.
        assert ids == {"agent_001", "agent_002", "agent_003"}


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

    def test_composing_status_visible_between_sim_and_completed(self, tmp_path: Path) -> None:
        """sim 종료 후 composer 호출 직전 status="composing" 으로 잠시 머무른다.

        composer 가 충분히 느리도록 sleep 을 걸고 폴링으로 그 윈도우를 잡는다.
        잡힌 record 의 ``rounds_done == rounds_total`` 이면 sim 은 끝났다는
        뜻 — 그 시점에 status 가 running 으로 남아 있으면 회귀.
        """

        async def _slow_composer(
            *, plaza_id: str, event_log_path: Path, preset: Preset
        ) -> ComposerOutcome:
            del plaza_id, event_log_path, preset
            await asyncio.sleep(0.3)
            return ComposerOutcome(markdown="# ok")

        app = create_app(
            runner=_success_runner(rounds_to_report=2),
            base_dir=tmp_path,
            composer=_slow_composer,
        )
        with TestClient(app) as client:
            created = client.post(
                "/api/plazas",
                json={
                    "ontology_a_path": "/tmp/a.json",
                    "ontology_b_path": "/tmp/b.json",
                    "rounds": 2,
                    "label": "compose-window",
                },
            ).json()
            plaza_id = created["plaza_id"]
            deadline = time.monotonic() + 2.0
            saw_composing = False
            while time.monotonic() < deadline:
                body = client.get(f"/api/plazas/{plaza_id}/status").json()
                if body["status"] == "composing":
                    saw_composing = True
                    assert body["rounds_done"] == body["rounds_total"]
                    break
                if body["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.01)
            body = _wait_until(client, plaza_id, terminal={"completed", "failed"})
        assert saw_composing, "composing 상태가 한 번도 관찰되지 않았다"
        assert body["status"] == "completed"

    def test_preset_round_trips_to_composer_call(self, tmp_path: Path) -> None:
        """``CreatePlazaRequest.preset`` 이 composer 인자까지 그대로 흘러가야 한다.

        스택 어디서 preset 을 떨궈도 보고서 호출 수가 조용히 quick 으로 떨어진다
        — 이건 비용/지연 회귀라 사용자가 늦게 알아챈다.
        """
        seen: dict[str, Preset] = {}

        async def _capturing_composer(
            *, plaza_id: str, event_log_path: Path, preset: Preset
        ) -> ComposerOutcome:
            del plaza_id, event_log_path
            seen["preset"] = preset
            return ComposerOutcome(markdown=None)

        app = create_app(
            runner=_success_runner(rounds_to_report=1),
            base_dir=tmp_path,
            composer=_capturing_composer,
        )
        with TestClient(app) as client:
            created = client.post(
                "/api/plazas",
                json={
                    "ontology_a_path": "/tmp/a.json",
                    "ontology_b_path": "/tmp/b.json",
                    "rounds": 1,
                    "preset": "standard",
                },
            ).json()
            _wait_until(client, created["plaza_id"], terminal={"completed", "failed"})
        assert seen["preset"] is Preset.STANDARD

    def test_preset_defaults_to_quick_when_omitted(self, tmp_path: Path) -> None:
        """preset 미지정 시 backend 가 quick 으로 채운다."""
        seen: dict[str, Preset] = {}

        async def _capturing_composer(
            *, plaza_id: str, event_log_path: Path, preset: Preset
        ) -> ComposerOutcome:
            del plaza_id, event_log_path
            seen["preset"] = preset
            return ComposerOutcome(markdown=None)

        app = create_app(
            runner=_success_runner(rounds_to_report=1),
            base_dir=tmp_path,
            composer=_capturing_composer,
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
            _wait_until(client, created["plaza_id"], terminal={"completed", "failed"})
        assert seen["preset"] is Preset.QUICK

    def test_rejects_unknown_preset(self, tmp_path: Path) -> None:
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.post(
                "/api/plazas",
                json={
                    "ontology_a_path": "/tmp/a.json",
                    "ontology_b_path": "/tmp/b.json",
                    "rounds": 1,
                    "preset": "bogus",
                },
            )
        assert resp.status_code == 422

    def test_early_exit_keeps_actual_rounds_done(self, tmp_path: Path) -> None:
        """outcome.rounds_run 이 요청 total 보다 작으면 그 값을 그대로 유지해야 한다.

        토큰 예산 소진 등으로 ``run_simulation`` 이 ``early_exit=True`` 로 끝낸
        경우, 상태/보고가 "전부 끝났다" 고 잘못 보고하면 안 된다.
        """
        # 10 라운드를 요청했지만 runner 는 3 라운드만 돌고 끝났다고 보고.
        runner = _success_runner(rounds_to_report=3, report_rounds_run=True)
        app = create_app(runner=runner, base_dir=tmp_path)
        with TestClient(app) as client:
            created = client.post(
                "/api/plazas",
                json={
                    "ontology_a_path": "/tmp/a.json",
                    "ontology_b_path": "/tmp/b.json",
                    "rounds": 10,
                    "label": "early-exit",
                },
            ).json()
            body = _wait_until(client, created["plaza_id"], terminal={"completed", "failed"})
        assert body["status"] == "completed"
        assert body["rounds_total"] == 10
        # 핵심: 요청 10 라운드로 강제 채우지 말고 실제 3 라운드로 남겨야 함.
        assert body["rounds_done"] == 3


class TestPersistence:
    """``base_dir/plazas.db`` 의 SQLite 영속화 — 프로세스 재시작 후 복원."""

    def test_completed_plaza_visible_after_restart(self, tmp_path: Path) -> None:
        """완료된 plaza 는 새 app 인스턴스에서도 /status 가 200 으로 떨어진다.

        같은 ``base_dir`` 로 ``create_app`` 을 두 번 호출 — 첫 번째에서 plaza
        하나 띄우고 완료까지 기다린 뒤 닫고, 두 번째에서 같은 plaza_id 의
        /status 가 그대로 보이는지.
        """
        runner = _success_runner(rounds_to_report=2)
        first_app = create_app(runner=runner, base_dir=tmp_path)
        with TestClient(first_app) as client:
            created = client.post(
                "/api/plazas",
                json={
                    "ontology_a_path": "/tmp/a.json",
                    "ontology_b_path": "/tmp/b.json",
                    "rounds": 2,
                    "label": "survivor",
                },
            ).json()
            plaza_id = created["plaza_id"]
            _wait_until(client, plaza_id, terminal={"completed", "failed"})

        # 새 app — 메모리 record 는 0 에서 시작. db 에서 hydrate 만 의지.
        second_app = create_app(runner=runner, base_dir=tmp_path)
        with TestClient(second_app) as client:
            resp = client.get(f"/api/plazas/{plaza_id}/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["rounds_total"] == 2
        assert body["rounds_done"] == 2
        assert body["label"] == "survivor"

    def test_interrupted_running_plaza_marked_failed_on_restart(self, tmp_path: Path) -> None:
        """첫 app shutdown 시점에 running 인 plaza 는 재기동 후 failed 로 보여야 한다.

        ``hanging_runner`` 가 영영 안 끝나도록 두고, TestClient 가 닫힐 때
        ``shutdown`` 이 task 를 취소한다. 그 시점에 db 의 마지막 status 는
        ``running``. 새 app 이 그걸 ``failed`` + 안내 메시지로 마킹해야 한다.
        """

        async def _hanging_runner(
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
            await asyncio.sleep(60)  # 절대 끝나지 않게 — cancel 받기 전까지.
            return RunnerOutcome()

        first_app = create_app(runner=_hanging_runner, base_dir=tmp_path)
        with TestClient(first_app) as client:
            created = client.post(
                "/api/plazas",
                json={
                    "ontology_a_path": "/tmp/a.json",
                    "ontology_b_path": "/tmp/b.json",
                    "rounds": 5,
                    "label": "interrupted",
                },
            ).json()
            plaza_id = created["plaza_id"]
            # status="running" 으로 commit 될 때까지 기다린다.
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                body = client.get(f"/api/plazas/{plaza_id}/status").json()
                if body["status"] == "running":
                    break
                time.sleep(0.01)
            else:
                raise AssertionError("plaza never reached running before shutdown")

        # TestClient context exit → shutdown → task cancel → 디스크에는 running 으로 남음.
        # 새 app 이 hydrate 하며 failed 로 마킹.
        second_app = create_app(runner=_hanging_runner, base_dir=tmp_path)
        with TestClient(second_app) as client:
            body = client.get(f"/api/plazas/{plaza_id}/status").json()
        assert body["status"] == "failed"
        assert body["error"] is not None
        assert "restart" in body["error"].lower()

        import sqlite3  # noqa: PLC0415 — 테스트 전용 직접 검증.

        conn = sqlite3.connect(str(tmp_path / "plazas.db"))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status, error FROM plazas WHERE plaza_id = ?",
            (plaza_id,),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["status"] == "failed"
        assert "restart" in row["error"].lower()

    def test_per_round_progress_persisted(self, tmp_path: Path) -> None:
        """``on_progress`` 가 부른 ``rounds_done`` 이 매 라운드 DB 에 영속돼야 한다.

        직접 db 를 읽어 row 의 ``rounds_done`` 이 최종값이랑 일치하는지 본다.
        progress 마다 commit 이 빠지면 재시작 시 라운드가 뒤로 후퇴한다.
        """
        runner = _success_runner(rounds_to_report=4)
        app = create_app(runner=runner, base_dir=tmp_path)
        with TestClient(app) as client:
            created = client.post(
                "/api/plazas",
                json={
                    "ontology_a_path": "/tmp/a.json",
                    "ontology_b_path": "/tmp/b.json",
                    "rounds": 4,
                },
            ).json()
            _wait_until(client, created["plaza_id"], terminal={"completed", "failed"})

        import sqlite3  # noqa: PLC0415 — 테스트 전용 직접 검증.

        conn = sqlite3.connect(str(tmp_path / "plazas.db"))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status, rounds_done, rounds_total FROM plazas WHERE plaza_id = ?",
            (created["plaza_id"],),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["status"] == "completed"
        assert row["rounds_done"] == 4
        assert row["rounds_total"] == 4


class TestGetAgents:
    """``GET /api/plazas/{id}/agents`` — Casting 화면용 앵커 리스트."""

    def test_returns_mapped_agents(self, tmp_path: Path) -> None:
        onto_a = _write_ontology_a(
            tmp_path / "ontology_a.json",
            [
                ("agent_001", "AI 기본법", "AIRegulationPolicy", 0.65),
                ("agent_002", "스타트업 협회", "IndustryGroup", 0.3),
                ("agent_003", "시민 연합", "CivicOrganization", 0.8),
            ],
        )
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            created = client.post(
                "/api/plazas",
                json={
                    "ontology_a_path": str(onto_a),
                    "ontology_b_path": "/tmp/b.json",
                    "rounds": 1,
                    "label": "casting",
                },
            ).json()
            plaza_id = created["plaza_id"]
            resp = client.get(f"/api/plazas/{plaza_id}/agents")
        assert resp.status_code == 200
        body = resp.json()
        assert body["plaza_id"] == plaza_id
        assert len(body["agents"]) == 3
        by_id = {a["id"]: a for a in body["agents"]}
        assert by_id["agent_001"]["name"] == "AI 기본법"
        assert by_id["agent_001"]["role"] == "AIRegulationPolicy"
        assert by_id["agent_001"]["ideology"] == 0.65
        assert by_id["agent_001"]["topics"] == ["agent_001-topic"]
        # avatar_seed 는 결정적 uint32. 라우트 helper 와 같은 알고리즘 (sha256[:4]).
        seed = by_id["agent_001"]["avatar_seed"]
        assert isinstance(seed, int)
        assert 0 <= seed <= 0xFFFFFFFF
        # raw avatar 필드는 빠진다 — 프론트가 seed 로 deterministic 생성.
        assert "avatar" not in by_id["agent_001"]
        # agent_id 가 다르면 seed 도 (충돌 가능성은 무시할 수준 — 2^32 분포).
        seeds = {a["id"]: a["avatar_seed"] for a in body["agents"]}
        assert len(set(seeds.values())) == 3

    def test_avatar_seed_deterministic_across_requests(self, tmp_path: Path) -> None:
        """같은 plaza 를 두 번 fetch 해도 seed 가 같아야 — reload 시 아바타 안 튀는 회귀."""
        onto_a = _write_ontology_a(
            tmp_path / "ontology_a.json",
            [("agent_alpha", "Alpha", "Researcher", 0.4)],
        )
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            created = client.post(
                "/api/plazas",
                json={
                    "ontology_a_path": str(onto_a),
                    "ontology_b_path": "/tmp/b.json",
                    "rounds": 1,
                },
            ).json()
            plaza_id = created["plaza_id"]
            first = client.get(f"/api/plazas/{plaza_id}/agents").json()
            second = client.get(f"/api/plazas/{plaza_id}/agents").json()
        assert first["agents"][0]["avatar_seed"] == second["agents"][0]["avatar_seed"]

    def test_available_before_sim_finishes(self, tmp_path: Path) -> None:
        """plaza 가 pending/running 이어도 ontology_a 만 있으면 200 으로 떨어진다.

        Casting 화면이 sim 시작 전부터 앵커 슬롯을 그리려면 이 보장이 필요하다.
        """
        onto_a = _write_ontology_a(tmp_path / "ontology_a.json", [("a", "n", "Role", 0.5)])

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
            await asyncio.sleep(0.3)
            return RunnerOutcome()

        app = create_app(runner=_slow_runner, base_dir=tmp_path)
        with TestClient(app) as client:
            created = client.post(
                "/api/plazas",
                json={
                    "ontology_a_path": str(onto_a),
                    "ontology_b_path": "/tmp/b.json",
                    "rounds": 1,
                },
            ).json()
            resp = client.get(f"/api/plazas/{created['plaza_id']}/agents")
        assert resp.status_code == 200
        assert len(resp.json()["agents"]) == 1

    def test_404_for_unknown_plaza(self, tmp_path: Path) -> None:
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.get("/api/plazas/does-not-exist/agents")
        assert resp.status_code == 404

    def test_404_when_ontology_file_missing(self, tmp_path: Path) -> None:
        """plaza 생성은 path 만 받고 존재 검증 안 하니, 파일이 없으면 404 로 떨어진다."""
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            created = client.post(
                "/api/plazas",
                json={
                    "ontology_a_path": "/tmp/definitely-not-here.json",
                    "ontology_b_path": "/tmp/b.json",
                    "rounds": 1,
                },
            ).json()
            resp = client.get(f"/api/plazas/{created['plaza_id']}/agents")
        assert resp.status_code == 404
        assert "ontology_a" in resp.json()["detail"]


def _follow_jsonl(*follows: tuple[int, str, str]) -> str:
    """(round_num, follower, followee) 튜플들을 events.jsonl 본문으로 직렬화."""
    lines = []
    for round_num, follower, followee in follows:
        lines.append(
            json.dumps(
                {
                    "round_num": round_num,
                    "timestamp": "2026-05-26T00:00:00+00:00",
                    "agent_id": follower,
                    "action": {"type": "FOLLOW", "target_agent_id": followee},
                }
            )
        )
    return "\n".join(lines) + "\n"


def _follow_writing_runner(follows: list[tuple[int, str, str]]) -> _RunnerCoro:
    """events.jsonl 에 FOLLOW 라인들을 흘려주고 종료하는 runner."""

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
        await asyncio.to_thread(
            event_log_path.write_text, _follow_jsonl(*follows), encoding="utf-8"
        )
        for r in range(rounds):
            await asyncio.sleep(0)
            on_progress(rounds_done=r + 1)
        return RunnerOutcome()

    return _run


class TestGetLayout:
    """``GET /api/plazas/{id}/layout`` — Plaza 부감 뷰 좌표."""

    def test_returns_mapped_layout(self, tmp_path: Path) -> None:
        onto_a = _write_ontology_a(
            tmp_path / "ontology_a.json",
            [
                ("a01", "A1", "Role", 0.5),
                ("a02", "A2", "Role", 0.5),
                ("a03", "A3", "Role", 0.5),
            ],
        )
        app = create_app(
            runner=_follow_writing_runner(
                [(0, "a01", "a02"), (0, "a03", "a02"), (1, "a01", "a03")]
            ),
            base_dir=tmp_path,
        )
        with TestClient(app) as client:
            created = client.post(
                "/api/plazas",
                json={
                    "ontology_a_path": str(onto_a),
                    "ontology_b_path": "/tmp/b.json",
                    "rounds": 2,
                    "label": "layout",
                },
            ).json()
            plaza_id = created["plaza_id"]
            _wait_until(client, plaza_id, terminal={"completed", "failed"})
            resp = client.get(f"/api/plazas/{plaza_id}/layout")
        assert resp.status_code == 200
        body = resp.json()
        assert body["plaza_id"] == plaza_id
        assert body["ready"] is True
        assert body["width"] == 1.0
        assert body["height"] == 1.0
        assert len(body["agents"]) == 3
        by_id = {a["id"]: a for a in body["agents"]}
        # 좌표 박스 안.
        for item in body["agents"]:
            assert 0.0 <= item["x"] <= 1.0
            assert 0.0 <= item["y"] <= 1.0
        # follower_count 는 raw 받은 follow 수.
        # a02 ← a01, a03 (=2); a03 ← a01 (=1); a01 = 0.
        assert by_id["a02"]["follower_count"] == 2
        assert by_id["a03"]["follower_count"] == 1
        assert by_id["a01"]["follower_count"] == 0
        # influence 는 plaza 내 max 로 정규화 (max=2 → a02=1.0, a03=0.5, a01=0.0).
        assert by_id["a02"]["influence"] == 1.0
        assert by_id["a03"]["influence"] == 0.5
        assert by_id["a01"]["influence"] == 0.0
        # avatar_seed: /agents 와 같은 uint32 — 두 응답이 같은 값.
        for item in body["agents"]:
            assert 0 <= item["avatar_seed"] <= 0xFFFFFFFF
        # ontology 메타가 그대로.
        assert by_id["a01"]["name"] == "A1"
        assert by_id["a01"]["role"] == "Role"

    def test_pending_returns_not_ready(self, tmp_path: Path) -> None:
        """pending / running 동안엔 200 + ``ready=false`` + ``agents=[]``.

        /agents 와 일관된 게이트 — 409 가 아님. 프론트는 ready 플래그로 부감 뷰
        빈 상태 / 채운 상태를 분기.
        """
        onto_a = _write_ontology_a(tmp_path / "ontology_a.json", [("a", "n", "R", 0.5)])

        async def _slow(
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
            await asyncio.sleep(0.3)
            return RunnerOutcome()

        app = create_app(runner=_slow, base_dir=tmp_path)
        with TestClient(app) as client:
            created = client.post(
                "/api/plazas",
                json={
                    "ontology_a_path": str(onto_a),
                    "ontology_b_path": "/tmp/b.json",
                    "rounds": 1,
                },
            ).json()
            resp = client.get(f"/api/plazas/{created['plaza_id']}/layout")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ready"] is False
        assert body["agents"] == []

    def test_works_without_event_log(self, tmp_path: Path) -> None:
        """events.jsonl 자체가 안 만들어진 (--fake) 케이스도 200, influence=0."""
        onto_a = _write_ontology_a(
            tmp_path / "ontology_a.json",
            [("a", "Alpha", "R", 0.5), ("b", "Beta", "R", 0.5)],
        )
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            created = client.post(
                "/api/plazas",
                json={
                    "ontology_a_path": str(onto_a),
                    "ontology_b_path": "/tmp/b.json",
                    "rounds": 1,
                },
            ).json()
            plaza_id = created["plaza_id"]
            _wait_until(client, plaza_id, terminal={"completed", "failed"})
            resp = client.get(f"/api/plazas/{plaza_id}/layout")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ready"] is True
        assert len(body["agents"]) == 2
        # max_follow=0 분기 → 전부 0.0 (정규화 분모 0 케이스).
        assert all(a["influence"] == 0.0 for a in body["agents"])
        assert all(a["follower_count"] == 0 for a in body["agents"])

    def test_deterministic_across_calls(self, tmp_path: Path) -> None:
        """plaza_id 시드 고정이라 리로드/폴링 시 좌표가 안 튀어야 한다."""
        onto_a = _write_ontology_a(
            tmp_path / "ontology_a.json",
            [(f"a{i:02d}", f"A{i}", "R", 0.5) for i in range(5)],
        )
        app = create_app(
            runner=_follow_writing_runner([(0, "a00", "a01"), (0, "a02", "a03")]),
            base_dir=tmp_path,
        )
        with TestClient(app) as client:
            created = client.post(
                "/api/plazas",
                json={
                    "ontology_a_path": str(onto_a),
                    "ontology_b_path": "/tmp/b.json",
                    "rounds": 1,
                },
            ).json()
            plaza_id = created["plaza_id"]
            _wait_until(client, plaza_id, terminal={"completed", "failed"})
            first = client.get(f"/api/plazas/{plaza_id}/layout").json()
            second = client.get(f"/api/plazas/{plaza_id}/layout").json()
        assert first == second

    def test_404_for_unknown_plaza(self, tmp_path: Path) -> None:
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.get("/api/plazas/does-not-exist/layout")
        assert resp.status_code == 404

    def test_404_when_ontology_missing(self, tmp_path: Path) -> None:
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            created = client.post(
                "/api/plazas",
                json={
                    "ontology_a_path": "/tmp/definitely-not-here.json",
                    "ontology_b_path": "/tmp/b.json",
                    "rounds": 1,
                },
            ).json()
            plaza_id = created["plaza_id"]
            _wait_until(client, plaza_id, terminal={"completed", "failed"})
            resp = client.get(f"/api/plazas/{plaza_id}/layout")
        assert resp.status_code == 404
        assert "ontology_a" in resp.json()["detail"]


class TestListPlazas:
    def test_empty_store_returns_empty_page(self, tmp_path: Path) -> None:
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.get("/api/plazas")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "plazas": [],
            "total": 0,
            "limit": 20,
            "offset": 0,
            "next_cursor": None,
        }

    def test_single_completed_plaza_visible(self, tmp_path: Path) -> None:
        app = create_app(runner=_success_runner(rounds_to_report=2), base_dir=tmp_path)
        with TestClient(app) as client:
            created = client.post(
                "/api/plazas",
                json={"rounds": 2, "label": "demo", "preset": "standard"},
            ).json()
            plaza_id = created["plaza_id"]
            _wait_until(client, plaza_id, terminal={"completed", "failed"})
            body = client.get("/api/plazas").json()
        assert body["total"] == 1
        assert body["limit"] == 20
        assert body["offset"] == 0
        assert len(body["plazas"]) == 1
        item = body["plazas"][0]
        assert item["plaza_id"] == plaza_id
        assert item["status"] == "completed"
        assert item["rounds_total"] == 2
        assert item["rounds_done"] == 2
        assert item["label"] == "demo"
        assert item["preset"] == "standard"
        # 작은 본문은 빼두기로 했다 — report_markdown 등이 누설되면 안 됨.
        assert "report_markdown" not in item
        # Pydantic 이 UTC datetime 을 ``...Z`` (또는 ``+00:00``) 으로 직렬화한다.
        # 파싱 가능 + tzinfo 가 UTC 인 것만 확인.
        assert datetime.fromisoformat(item["created_at"]).utcoffset() is not None
        assert datetime.fromisoformat(item["updated_at"]).utcoffset() is not None

    def test_ordered_newest_first(self, tmp_path: Path) -> None:
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            ids: list[str] = []
            for i in range(3):
                created = client.post(
                    "/api/plazas",
                    json={"rounds": 1, "label": f"p{i}"},
                ).json()
                ids.append(created["plaza_id"])
                _wait_until(client, created["plaza_id"], terminal={"completed", "failed"})
                # ``created_at`` 정밀도가 second 라 동일 second 에 박히면 정렬이
                # plaza_id 로 tie-break 된다. 결정적 검증을 위해 second 경계 넘김.
                time.sleep(1.05)
            body = client.get("/api/plazas").json()
        assert body["total"] == 3
        # 마지막에 만든 plaza 가 위.
        assert [p["plaza_id"] for p in body["plazas"]] == list(reversed(ids))
        assert [p["label"] for p in body["plazas"]] == ["p2", "p1", "p0"]

    def test_status_filter_applies_to_total(self, tmp_path: Path) -> None:
        # success + failure 섞어 만들고 ?status=failed 가 failed 만 / total=1.
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            ok = client.post("/api/plazas", json={"rounds": 1, "label": "ok"}).json()
            _wait_until(client, ok["plaza_id"], terminal={"completed", "failed"})
        # 같은 base_dir 로 새 app — runner 만 failing 으로 바꾼다.
        app2 = create_app(runner=_failing_runner, base_dir=tmp_path)
        with TestClient(app2) as client:
            bad = client.post("/api/plazas", json={"rounds": 1, "label": "bad"}).json()
            _wait_until(client, bad["plaza_id"], terminal={"completed", "failed"})
            all_body = client.get("/api/plazas").json()
            failed_body = client.get("/api/plazas?status=failed").json()
            completed_body = client.get("/api/plazas?status=completed").json()
        assert all_body["total"] == 2
        assert failed_body["total"] == 1
        assert {p["plaza_id"] for p in failed_body["plazas"]} == {bad["plaza_id"]}
        assert completed_body["total"] == 1
        assert {p["plaza_id"] for p in completed_body["plazas"]} == {ok["plaza_id"]}

    def test_limit_and_offset_paginate(self, tmp_path: Path) -> None:
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            for i in range(4):
                created = client.post(
                    "/api/plazas",
                    json={"rounds": 1, "label": f"p{i}"},
                ).json()
                _wait_until(client, created["plaza_id"], terminal={"completed", "failed"})
            page1 = client.get("/api/plazas?limit=2&offset=0").json()
            page2 = client.get("/api/plazas?limit=2&offset=2").json()
        assert page1["total"] == 4
        assert page2["total"] == 4
        assert page1["limit"] == 2
        assert page1["offset"] == 0
        assert page2["limit"] == 2
        assert page2["offset"] == 2
        assert len(page1["plazas"]) == 2
        assert len(page2["plazas"]) == 2
        seen = {p["plaza_id"] for p in page1["plazas"]} | {p["plaza_id"] for p in page2["plazas"]}
        assert len(seen) == 4  # 페이지가 겹치지 않음

    def test_same_second_tie_break_by_plaza_id_desc(self, tmp_path: Path) -> None:
        # ``created_at`` 초 단위 truncate → 같은 초에 만들어진 두 plaza 가 흔하다.
        # 2 차 정렬 키 ``plaza_id DESC`` 가 없으면 SQLite 가 동률 행 순서를 보장
        # 안 해서 ``LIMIT/OFFSET`` 페이지 경계에 걸린 plaza 가 누락/중복될 수
        # 있다 — db 경로 (SQLite) 와 in-memory 폴백 경로가 일관되게 ``plaza_id``
        # 큰 쪽이 위로 오는지 확인한다.
        conn = _db.connect(tmp_path / "plazas.db")
        try:
            shared = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
            # alphabetic 으로 ``id-b`` > ``id-a``. plaza_id DESC 면 b 가 먼저.
            for pid in ("id-a", "id-b"):
                record = PlazaRecord(
                    plaza_id=pid,
                    status="completed",
                    rounds_total=1,
                    rounds_done=1,
                    label=pid,
                    preset=Preset.QUICK,
                    created_at=shared,
                    updated_at=shared,
                )
                _db.upsert_record(conn, record)
            summaries, total, _ = _db.list_summary(conn, limit=10, offset=0)
        finally:
            conn.close()
        assert total == 2
        assert [s.plaza_id for s in summaries] == ["id-b", "id-a"]

    def test_rejects_unknown_status(self, tmp_path: Path) -> None:
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.get("/api/plazas?status=zombie")
        assert resp.status_code == 422

    def test_rejects_out_of_range_limit(self, tmp_path: Path) -> None:
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.get("/api/plazas?limit=999")
        assert resp.status_code == 422

    def test_list_persists_across_restart(self, tmp_path: Path) -> None:
        # ``TestPersistence`` 와 같은 패턴 — 같은 base_dir 의 두 번째 app 이
        # SQLite 에서 plaza 를 hydrate 해서 list 에 다시 보여야 한다.
        first_app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(first_app) as client:
            created = client.post("/api/plazas", json={"rounds": 1, "label": "alive"}).json()
            _wait_until(client, created["plaza_id"], terminal={"completed", "failed"})
        second_app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(second_app) as client:
            body = client.get("/api/plazas").json()
        assert body["total"] == 1
        assert body["plazas"][0]["plaza_id"] == created["plaza_id"]
        assert body["plazas"][0]["label"] == "alive"


class TestListPlazasCursor:
    """``GET /api/plazas?cursor=`` — keyset 페이징.

    offset 모드와 결과 row 순서가 동치인지 / next_cursor 가 끝에서 null 인지 /
    변조된 cursor 가 422 인지를 본다. 깊은 offset 의 비용을 줄이는 게 도입
    목적이라 row 수 = 5 정도로 작아도 의미 검증은 동치성에 있다.
    """

    def _seed_plazas(self, client: TestClient, n: int) -> list[str]:
        """``n`` 개 plaza 를 만들고 모두 terminal 까지 보낸 뒤 id 리스트 반환.

        ``created_at`` 이 초 단위 truncate 라 같은 초에 잡힌 plaza 가 섞일 수
        있다 — tie-break (``plaza_id DESC``) 가 인덱스/keyset 모두에 일관되게
        걸려 있어 순서는 결정적이다.
        """
        ids: list[str] = []
        for i in range(n):
            created = client.post("/api/plazas", json={"rounds": 1, "label": f"p{i}"}).json()
            plaza_id = created["plaza_id"]
            _wait_until(client, plaza_id, terminal={"completed", "failed"})
            ids.append(plaza_id)
        return ids

    def test_cursor_paginates_same_order_as_offset(self, tmp_path: Path) -> None:
        """cursor 모드 (첫 호출은 cursor 없이) 와 offset 모드가 동일 순서의 시퀀스."""
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            self._seed_plazas(client, 5)

            offset_body = client.get("/api/plazas?limit=100").json()
            offset_order = [p["plaza_id"] for p in offset_body["plazas"]]
            assert len(offset_order) == 5

            # 첫 호출은 cursor 없이 = offset 모드지만 응답의 next_cursor 로 keyset 진입.
            collected: list[str] = []
            cursor: str | None = None
            for _ in range(10):
                url = "/api/plazas?limit=2"
                if cursor is not None:
                    url += f"&cursor={cursor}"
                body = client.get(url).json()
                collected.extend(p["plaza_id"] for p in body["plazas"])
                cursor = body["next_cursor"]
                if cursor is None:
                    break
            else:
                raise AssertionError("cursor loop did not terminate within 10 pages")

        assert collected == offset_order

    def test_cursor_mode_ignores_offset_query(self, tmp_path: Path) -> None:
        """cursor 가 있으면 ``offset`` 은 무시 + 응답 ``offset`` 은 0 으로 정규화.

        실제 정렬은 ``(created_at DESC, plaza_id DESC)`` — 빠른 테스트에서 세
        plaza 의 ``created_at`` 이 같은 초로 떨어지면 plaza_id alphanum DESC
        가 결정해 insertion 순서가 무의미하다. 기대값은 limit 한 번에 다 받은
        응답을 truth 로 쓴다.
        """
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            self._seed_plazas(client, 3)
            truth = [p["plaza_id"] for p in client.get("/api/plazas?limit=100").json()["plazas"]]

            first = client.get("/api/plazas?limit=1").json()
            assert [p["plaza_id"] for p in first["plazas"]] == [truth[0]]
            cursor = first["next_cursor"]
            assert cursor is not None

            second = client.get(f"/api/plazas?limit=1&cursor={cursor}&offset=999").json()
            assert [p["plaza_id"] for p in second["plazas"]] == [truth[1]]
            assert second["offset"] == 0

    def test_last_page_has_null_next_cursor(self, tmp_path: Path) -> None:
        """마지막 페이지(부분 page) 면 ``next_cursor=null``."""
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            self._seed_plazas(client, 3)
            # 3 건을 limit=2 로 끊으면 page1 = 2건 (다음 있음), page2 = 1건 (끝).
            page1 = client.get("/api/plazas?limit=2").json()
            assert len(page1["plazas"]) == 2
            assert page1["next_cursor"] is not None
            page2 = client.get(f"/api/plazas?limit=2&cursor={page1['next_cursor']}").json()
            assert len(page2["plazas"]) == 1
            assert page2["next_cursor"] is None

    def test_exact_limit_then_empty_page_terminates(self, tmp_path: Path) -> None:
        """정확히 ``limit`` 만큼이고 그게 끝이면, 다음 호출은 빈 페이지 + null cursor.

        keyset 의 정석 — server 가 "끝" 을 미리 알 수 없는 케이스를 한 번 더
        호출로 닫는다. 이 동작이 없으면 클라가 마지막 페이지를 "다음이 더 있는"
        것으로 오해할 수 있다.
        """
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            self._seed_plazas(client, 2)
            page1 = client.get("/api/plazas?limit=2").json()
            assert len(page1["plazas"]) == 2
            assert page1["next_cursor"] is not None
            page2 = client.get(f"/api/plazas?limit=2&cursor={page1['next_cursor']}").json()
            assert page2["plazas"] == []
            assert page2["next_cursor"] is None

    def test_cursor_respects_status_filter(self, tmp_path: Path) -> None:
        """cursor + ``?status=`` 조합 — 필터 후 시퀀스만 페이지로 본다.

        같은 상태로 4건 seed → 2건씩 cursor 페이지 → 다음 페이지 → 빈 페이지로
        끝. 필터가 cursor 페이지 사이에 일관되게 걸려 row 가 누락/중복되지
        않는지가 핵심.
        """
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            self._seed_plazas(client, 4)
            page1 = client.get("/api/plazas?limit=2&status=completed").json()
            assert len(page1["plazas"]) == 2
            assert page1["total"] == 4
            cursor = page1["next_cursor"]
            assert cursor is not None
            page2 = client.get(f"/api/plazas?limit=2&cursor={cursor}&status=completed").json()
            assert len(page2["plazas"]) == 2
            assert page2["next_cursor"] is not None  # exact-limit 마지막 → 빈 page 추가.
            page3 = client.get(
                f"/api/plazas?limit=2&cursor={page2['next_cursor']}&status=completed"
            ).json()
            assert page3["plazas"] == []
            assert page3["next_cursor"] is None

            seen = [p["plaza_id"] for p in page1["plazas"]] + [
                p["plaza_id"] for p in page2["plazas"]
            ]
            assert len(set(seen)) == 4

    def test_invalid_cursor_returns_422(self, tmp_path: Path) -> None:
        """변조된 cursor 는 422 — silent 한 빈 페이지로 가지 않게."""
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            # base64 형식조차 깨진 값.
            resp = client.get("/api/plazas?cursor=!!!not-base64!!!")
            assert resp.status_code == 422

            # base64 는 맞지만 payload 가 ``|`` 없는 쓰레기.
            broken = base64.urlsafe_b64encode(b"garbage").rstrip(b"=").decode()
            resp2 = client.get(f"/api/plazas?cursor={broken}")
            assert resp2.status_code == 422


class TestDeletePlaza:
    """``DELETE /api/plazas/{id}`` — 메모리/SQLite/디스크 통째 정리."""

    def test_404_for_unknown_id(self, tmp_path: Path) -> None:
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.delete("/api/plazas/does-not-exist")
        assert resp.status_code == 404

    def test_completed_plaza_removed_from_list(self, tmp_path: Path) -> None:
        app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(app) as client:
            created = client.post("/api/plazas", json={"rounds": 1, "label": "doomed"}).json()
            plaza_id = created["plaza_id"]
            _wait_until(client, plaza_id, terminal={"completed", "failed"})
            del_resp = client.delete(f"/api/plazas/{plaza_id}")
            assert del_resp.status_code == 204
            assert del_resp.content == b""
            # 동일 id 다시 지우면 404 — 멱등성보다는 "삭제됨" 사실 통지가 우선.
            again = client.delete(f"/api/plazas/{plaza_id}")
            assert again.status_code == 404
            # /status 도 404.
            assert client.get(f"/api/plazas/{plaza_id}/status").status_code == 404
            # /api/plazas 목록에서도 빠짐.
            body = client.get("/api/plazas").json()
        assert body["total"] == 0
        assert body["plazas"] == []

    def test_disk_artifacts_removed(self, tmp_path: Path) -> None:
        """events.jsonl 이 들어가는 plaza_root 디렉토리도 통째로 사라진다.

        백엔드에 잘못 만든 plaza 가 디스크 공간을 계속 잡고 있으면 안 된다 —
        리테스트/잘못 만든 sim 정리의 핵심 동기.
        """
        app = create_app(
            runner=_follow_writing_runner([(0, "a", "b")]),
            base_dir=tmp_path,
        )
        onto_a = _write_ontology_a(
            tmp_path / "ontology_a.json",
            [("a", "A", "Role", 0.5), ("b", "B", "Role", 0.5)],
        )
        with TestClient(app) as client:
            created = client.post(
                "/api/plazas",
                json={"ontology_a_path": str(onto_a), "rounds": 1},
            ).json()
            plaza_id = created["plaza_id"]
            _wait_until(client, plaza_id, terminal={"completed", "failed"})
            plaza_root = tmp_path / plaza_id
            assert plaza_root.exists(), "precondition: plaza dir should exist after run"
            assert (plaza_root / "events.jsonl").exists()
            resp = client.delete(f"/api/plazas/{plaza_id}")
            assert resp.status_code == 204
        assert not plaza_root.exists()

    def test_persists_across_restart(self, tmp_path: Path) -> None:
        """삭제는 SQLite row 도 지운다 — 재기동해도 안 살아남는다."""
        first_app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(first_app) as client:
            created = client.post("/api/plazas", json={"rounds": 1}).json()
            plaza_id = created["plaza_id"]
            _wait_until(client, plaza_id, terminal={"completed", "failed"})
            assert client.delete(f"/api/plazas/{plaza_id}").status_code == 204
        # 같은 base_dir 의 두 번째 app — hydrate 해도 row 가 없으므로 list 가 비어 있어야 한다.
        second_app = create_app(runner=_success_runner(rounds_to_report=1), base_dir=tmp_path)
        with TestClient(second_app) as client:
            body = client.get("/api/plazas").json()
            assert body == {
                "plazas": [],
                "total": 0,
                "limit": 20,
                "offset": 0,
                "next_cursor": None,
            }
            assert client.get(f"/api/plazas/{plaza_id}/status").status_code == 404

    def test_running_plaza_cancelled_and_removed(self, tmp_path: Path) -> None:
        """running 인 plaza 도 DELETE 가 받는다 — task cancel + cleanup.

        잘못 만든 sim 을 멈추고 싶을 때 가장 흔한 use case. 409 로 막으면 사용자
        가 끝나기를 기다려야 하는 dead-end 가 된다.
        """

        async def _hanging_runner(
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
            await asyncio.sleep(60)  # cancel 받기 전까지 안 끝남.
            return RunnerOutcome()

        app = create_app(runner=_hanging_runner, base_dir=tmp_path)
        with TestClient(app) as client:
            created = client.post("/api/plazas", json={"rounds": 5, "label": "kill"}).json()
            plaza_id = created["plaza_id"]
            # running 으로 진입한 뒤 DELETE.
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                body = client.get(f"/api/plazas/{plaza_id}/status").json()
                if body["status"] == "running":
                    break
                time.sleep(0.01)
            else:
                raise AssertionError("plaza never reached running before delete")
            resp = client.delete(f"/api/plazas/{plaza_id}")
            assert resp.status_code == 204
            # 사라졌는지.
            assert client.get(f"/api/plazas/{plaza_id}/status").status_code == 404
            body = client.get("/api/plazas").json()
        assert body["total"] == 0

    @pytest.mark.asyncio
    async def test_sse_subscribers_receive_terminal_status_on_delete(self, tmp_path: Path) -> None:
        """DELETE 가 SSE 구독자에게 종결 status 를 한 번 쏴서 스트림을 닫는다.

        구독 중인 클라가 ``/events`` 큐에 keepalive 만 무한 반복하다 굳지 않도록,
        running plaza 의 DELETE 는 task cancel 이후 synthetic ``failed`` +
        ``error="deleted"`` 를 broadcast 한다. ``/events`` 라우트는 terminal
        status 를 보면 제너레이터를 닫으므로, 스트림이 정상 종료된 채 끝난다.
        """

        async def _hanging_runner(
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
            await asyncio.sleep(60)
            return RunnerOutcome()

        app = create_app(runner=_hanging_runner, base_dir=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://testserver") as client,
        ):
            created = await client.post("/api/plazas", json={"rounds": 5, "label": "sse-delete"})
            assert created.status_code == 202, created.text
            plaza_id = created.json()["plaza_id"]

            # running 진입까지 대기 — 안 그러면 초기 status="pending" 이 terminal 이 아니어서
            # 스트림이 큐만 보다가, DELETE 가 들어오기 전에 keepalive timeout 으로 굳는다.
            store = app.state.plaza_store
            for _ in range(100):
                record = await store.get(plaza_id)
                if record is not None and record.status == "running":
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("plaza never reached running before delete")

            # 스트림을 연 채로 다른 태스크에서 DELETE 를 쏜다. 스트림은 synthetic
            # terminal status 를 받고 스스로 닫혀야 한다.
            async def _delete_after_subscribe() -> httpx.Response:
                # /events 가 subscribe 를 마쳤을 시간을 짧게 양보. 너무 빨리 쏘면
                # _records.pop 이 subscribe 의 lock 획득보다 앞설 수 있다.
                await asyncio.sleep(0.1)
                return await client.delete(f"/api/plazas/{plaza_id}")

            delete_task = asyncio.create_task(_delete_after_subscribe())
            chunks: list[str] = []
            async with asyncio.timeout(3.0):
                async with client.stream("GET", f"/api/plazas/{plaza_id}/events") as resp:
                    assert resp.status_code == 200
                    async for chunk in resp.aiter_text():
                        chunks.append(chunk)
            del_resp = await delete_task
            assert del_resp.status_code == 204

        body = "".join(chunks)
        events = _parse_sse(body)
        # 마지막 event 가 synthetic terminal status — 스트림은 이걸 보고 닫혔다.
        assert events, "stream produced no parseable events"
        last_type, last_data = events[-1]
        assert last_type == "status"
        assert last_data["status"] == "failed"
        assert last_data["error"] == "deleted"


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
