"""Plaza POST/GET 라우트 단위 테스트.

`PlazaRunner` 를 fake 로 갈아끼워 외부 의존(LLM/embedder) 없이 닫는다.
status 머신: pending → running → completed | failed.
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
from litemiro.api.composer import ComposerOutcome
from litemiro.api.store import ProgressCallback, RunnerOutcome
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
        # avatar 필드 부재 확인 (프론트가 generate 한다).
        assert "avatar" not in by_id["agent_001"]

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
