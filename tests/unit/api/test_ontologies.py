"""``/api/ontologies`` + ``CreatePlazaRequest.ontology_id`` 단위 테스트.

``OntologyRunner`` 를 fake 로 갈아끼워 Phase 1 LLM 콜 없이 닫는다. fake 는
미리 만들어 둔 두 JSON 파일을 ``output_dir`` 에 떨궈 ``ontology_a_path``
/ ``ontology_b_path`` 가 비어있지 않게 한다 — plaza 가 그 경로를 그대로
ingest 한다.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from litemiro.api.app import create_app
from litemiro.api.ontology_store import (
    OntologyContentFilterBlockedError,
    OntologyProgressCallback,
    OntologyRunResult,
    is_content_filter_error,
)
from litemiro.api.store import ProgressCallback, RunnerOutcome
from litemiro.phase1.models import Preset


async def _noop_plaza_runner(
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
        await asyncio.sleep(0)
        on_progress(rounds_done=r + 1)
    return RunnerOutcome()


def _make_fake_ontology_files(output_dir: Path) -> tuple[Path, Path]:
    """``OntologyRunner`` 가 떨굴 두 JSON 의 미니멈 합리적 본문.

    plaza 라우트가 paths 를 검증하지 않고 그대로 PlazaStore 에 넘기므로 본문
    스키마는 라우트 단위 테스트에 영향이 없다 — 존재 여부만 체크.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    a = output_dir / "ontology_a_persona.json"
    b = output_dir / "ontology_b_memory.json"
    a.write_text(json.dumps({"ok": True}))
    b.write_text(json.dumps({"ok": True}))
    return a, b


def _success_ontology_runner(*, agent_count: int = 100):
    """성공 fake — output_dir 에 두 JSON 떨구고 ``OntologyRunResult`` 반환."""

    async def _run(
        *,
        document_path: Path,
        requirement: str,
        preset: Preset,
        output_dir: Path,
        on_progress: OntologyProgressCallback,
    ) -> OntologyRunResult:
        del document_path, requirement, preset
        # #126: 실 pipeline 의 step 시퀀스를 흉내내 store 가 row 에 active_step
        # 을 박는지 검증할 수 있게 한다.
        on_progress("step0_document", None)
        on_progress("step6_serialize", None)
        a, b = _make_fake_ontology_files(output_dir)
        return OntologyRunResult(
            ontology_a_path=a,
            ontology_b_path=b,
            agent_count=agent_count,
        )

    return _run


def _failing_ontology_runner():
    async def _run(
        *,
        document_path: Path,
        requirement: str,
        preset: Preset,
        output_dir: Path,
        on_progress: OntologyProgressCallback,
    ) -> OntologyRunResult:
        del document_path, requirement, preset, output_dir, on_progress
        raise RuntimeError("phase1 boom")

    return _run


def _content_filter_blocked_runner():
    """fallback chain 까지 모두 막힌 케이스를 시뮬레이션 — friendly msg 확인용."""

    async def _run(
        *,
        document_path: Path,
        requirement: str,
        preset: Preset,
        output_dir: Path,
        on_progress: OntologyProgressCallback,
    ) -> OntologyRunResult:
        del document_path, requirement, preset, output_dir, on_progress
        raise OntologyContentFilterBlockedError(
            "all models blocked by content filter: ['openrouter/qwen/qwen-plus']"
        )

    return _run


def _wait_ontology(
    client: TestClient,
    ontology_id: str,
    *,
    terminal: set[str],
    timeout: float = 2.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/ontologies/{ontology_id}")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] in terminal:
            return body  # type: ignore[no-any-return]
        time.sleep(0.01)
    raise AssertionError(f"ontology {ontology_id} did not reach {terminal} in {timeout}s")


def _upload_doc(client: TestClient, *, name: str = "src.txt", body: bytes = b"hi") -> str:
    resp = client.post("/api/documents", files={"file": (name, body, "text/plain")})
    assert resp.status_code == 201
    return resp.json()["document_id"]  # type: ignore[no-any-return]


class TestCreateOntology:
    def test_returns_202_pending(self, tmp_path: Path) -> None:
        app = create_app(
            runner=_noop_plaza_runner,
            base_dir=tmp_path,
            ontology_runner=_success_ontology_runner(),
        )
        with TestClient(app) as client:
            document_id = _upload_doc(client)
            resp = client.post(
                "/api/ontologies",
                json={
                    "document_id": document_id,
                    "requirement": "테스트 요구사항",
                    "preset": "quick",
                },
            )
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] in {"pending", "running", "completed"}
        assert body["document_id"] == document_id
        assert body["requirement"] == "테스트 요구사항"
        assert isinstance(body["ontology_id"], str)
        assert len(body["ontology_id"]) >= 16

    def test_404_for_unknown_document(self, tmp_path: Path) -> None:
        app = create_app(
            runner=_noop_plaza_runner,
            base_dir=tmp_path,
            ontology_runner=_success_ontology_runner(),
        )
        with TestClient(app) as client:
            resp = client.post(
                "/api/ontologies",
                json={
                    "document_id": "does-not-exist",
                    "requirement": "x",
                    "preset": "quick",
                },
            )
        assert resp.status_code == 404

    def test_503_when_runner_not_configured(self, tmp_path: Path) -> None:
        app = create_app(runner=_noop_plaza_runner, base_dir=tmp_path)  # no ontology_runner
        with TestClient(app) as client:
            resp = client.post(
                "/api/ontologies",
                json={"document_id": "x", "requirement": "y", "preset": "quick"},
            )
        # 라우터 자체가 등록되지 않으므로 404. (ontology_store 없으면 라우터를
        # include 하지 않는 게 create_app 의 계약 — fake 서버의 동작.)
        assert resp.status_code == 404

    def test_reaches_completed_with_agent_count(self, tmp_path: Path) -> None:
        app = create_app(
            runner=_noop_plaza_runner,
            base_dir=tmp_path,
            ontology_runner=_success_ontology_runner(agent_count=7),
        )
        with TestClient(app) as client:
            document_id = _upload_doc(client)
            created = client.post(
                "/api/ontologies",
                json={
                    "document_id": document_id,
                    "requirement": "x",
                    "preset": "quick",
                },
            ).json()
            ontology_id = created["ontology_id"]
            body = _wait_ontology(client, ontology_id, terminal={"completed", "failed"})
        assert body["status"] == "completed"
        assert body["ready"] is True
        assert body["agent_count"] == 7
        assert body["error"] is None

    def test_failed_runner_marks_failed(self, tmp_path: Path) -> None:
        app = create_app(
            runner=_noop_plaza_runner,
            base_dir=tmp_path,
            ontology_runner=_failing_ontology_runner(),
        )
        with TestClient(app) as client:
            document_id = _upload_doc(client)
            created = client.post(
                "/api/ontologies",
                json={
                    "document_id": document_id,
                    "requirement": "x",
                    "preset": "quick",
                },
            ).json()
            ontology_id = created["ontology_id"]
            body = _wait_ontology(client, ontology_id, terminal={"completed", "failed"})
        assert body["status"] == "failed"
        assert body["ready"] is False
        assert body["error"] == "phase1 boom"

    def test_content_filter_blocked_marks_failed_with_friendly_message(
        self, tmp_path: Path
    ) -> None:
        # #121 의 UX 손실 회피 — generic "인격 생성 실패" 대신 원인 + 다음 행동을 노출.
        app = create_app(
            runner=_noop_plaza_runner,
            base_dir=tmp_path,
            ontology_runner=_content_filter_blocked_runner(),
        )
        with TestClient(app) as client:
            document_id = _upload_doc(client)
            created = client.post(
                "/api/ontologies",
                json={
                    "document_id": document_id,
                    "requirement": "x",
                    "preset": "quick",
                },
            ).json()
            body = _wait_ontology(client, created["ontology_id"], terminal={"completed", "failed"})
        assert body["status"] == "failed"
        assert body["ready"] is False
        assert body["error"] is not None
        assert "콘텐츠 필터" in body["error"]
        assert "다른 자료" in body["error"]


class TestGetOntology:
    def test_404_for_unknown(self, tmp_path: Path) -> None:
        app = create_app(
            runner=_noop_plaza_runner,
            base_dir=tmp_path,
            ontology_runner=_success_ontology_runner(),
        )
        with TestClient(app) as client:
            resp = client.get("/api/ontologies/missing")
        assert resp.status_code == 404


class TestPlazaWithOntologyId:
    """``CreatePlazaRequest.ontology_id`` 우선 경로 + 검증 분기."""

    def test_uses_ontology_paths(self, tmp_path: Path) -> None:
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

        app = create_app(
            runner=_capture_runner,
            base_dir=tmp_path,
            ontology_runner=_success_ontology_runner(),
        )
        with TestClient(app) as client:
            document_id = _upload_doc(client)
            ontology_id = client.post(
                "/api/ontologies",
                json={
                    "document_id": document_id,
                    "requirement": "x",
                    "preset": "quick",
                },
            ).json()["ontology_id"]
            _wait_ontology(client, ontology_id, terminal={"completed"})
            resp = client.post(
                "/api/plazas",
                json={"ontology_id": ontology_id, "rounds": 1},
            )
            assert resp.status_code == 202
            plaza_id = resp.json()["plaza_id"]
            # plaza 시뮬이 끝날 때까지 잠깐 대기 — runner 가 captured 에 경로
            # 채울 시간을 준다.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and "a" not in captured:
                resp = client.get(f"/api/plazas/{plaza_id}/status")
                if resp.json()["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.01)

        expected_dir = tmp_path / "ontologies" / ontology_id
        assert captured["a"] == expected_dir / "ontology_a_persona.json"
        assert captured["b"] == expected_dir / "ontology_b_memory.json"

    def test_404_when_ontology_missing(self, tmp_path: Path) -> None:
        app = create_app(
            runner=_noop_plaza_runner,
            base_dir=tmp_path,
            ontology_runner=_success_ontology_runner(),
        )
        with TestClient(app) as client:
            resp = client.post(
                "/api/plazas",
                json={"ontology_id": "does-not-exist", "rounds": 1},
            )
        assert resp.status_code == 404

    def test_409_when_ontology_not_completed(self, tmp_path: Path) -> None:
        # runner 가 영원히 안 끝나도록 sleep — pending/running 단계에서 plaza 시도.
        async def _slow_runner(
            *,
            document_path: Path,
            requirement: str,
            preset: Preset,
            output_dir: Path,
            on_progress: OntologyProgressCallback,
        ) -> OntologyRunResult:
            del document_path, requirement, preset, output_dir, on_progress
            await asyncio.sleep(60)  # 테스트는 이 전에 끝낸다.
            raise AssertionError("should not reach")

        app = create_app(
            runner=_noop_plaza_runner,
            base_dir=tmp_path,
            ontology_runner=_slow_runner,
        )
        with TestClient(app) as client:
            document_id = _upload_doc(client)
            ontology_id = client.post(
                "/api/ontologies",
                json={
                    "document_id": document_id,
                    "requirement": "x",
                    "preset": "quick",
                },
            ).json()["ontology_id"]
            resp = client.post(
                "/api/plazas",
                json={"ontology_id": ontology_id, "rounds": 1},
            )
        assert resp.status_code == 409

    def test_503_when_ontology_store_not_configured(self, tmp_path: Path) -> None:
        # ontology_runner 가 None 인 fake 모드 — POST /api/plazas 가 ontology_id
        # 를 명시한 경우 503 으로 거절돼야 한다.
        app = create_app(runner=_noop_plaza_runner, base_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.post(
                "/api/plazas",
                json={"ontology_id": "anything", "rounds": 1},
            )
        assert resp.status_code == 503


class TestProgressExposure:
    """#126: ``active_step`` / ``fallback_model`` 이 polling 응답에 흘러 나오는지.

    프론트는 ``GET /api/ontologies/{id}`` 를 폴링해 "Step 4 / 7 (페르소나 생성)"
    같은 진행 표시를 띄운다. runner 의 on_progress 콜백이 row 로 전파되는
    경로가 깨지면 status 만 바뀌고 어디서 멈춰있는지 알 수 없다.
    """

    def test_completed_response_carries_last_step(self, tmp_path: Path) -> None:
        app = create_app(
            runner=_noop_plaza_runner,
            base_dir=tmp_path,
            ontology_runner=_success_ontology_runner(agent_count=3),
        )
        with TestClient(app) as client:
            document_id = _upload_doc(client)
            ontology_id = client.post(
                "/api/ontologies",
                json={"document_id": document_id, "requirement": "x", "preset": "quick"},
            ).json()["ontology_id"]
            body = _wait_ontology(client, ontology_id, terminal={"completed", "failed"})
        assert body["status"] == "completed"
        # _success_ontology_runner 가 마지막에 흘린 step.
        assert body["active_step"] == "step6_serialize"
        # primary 사용 → fallback_model None.
        assert body["fallback_model"] is None

    def test_fallback_runner_reports_model(self, tmp_path: Path) -> None:
        """fallback chain 진입을 흉내내는 runner — 두 번째 모델로 전환됨을 신호."""

        async def _fallback_runner(
            *,
            document_path: Path,
            requirement: str,
            preset: Preset,
            output_dir: Path,
            on_progress: OntologyProgressCallback,
        ) -> OntologyRunResult:
            del document_path, requirement, preset
            on_progress("step0_document", "openrouter/openai/gpt-4o-mini")
            on_progress("step6_serialize", "openrouter/openai/gpt-4o-mini")
            a, b = _make_fake_ontology_files(output_dir)
            return OntologyRunResult(ontology_a_path=a, ontology_b_path=b, agent_count=5)

        app = create_app(
            runner=_noop_plaza_runner,
            base_dir=tmp_path,
            ontology_runner=_fallback_runner,
        )
        with TestClient(app) as client:
            document_id = _upload_doc(client)
            ontology_id = client.post(
                "/api/ontologies",
                json={"document_id": document_id, "requirement": "x", "preset": "quick"},
            ).json()["ontology_id"]
            body = _wait_ontology(client, ontology_id, terminal={"completed", "failed"})
        assert body["status"] == "completed"
        assert body["fallback_model"] == "openrouter/openai/gpt-4o-mini"


class TestIsContentFilterError:
    """``is_content_filter_error`` 의 substring 매칭. LiteLLM 이 provider raw 에러를
    그대로 wrapping 해 던지므로 메시지 식별이 유일한 분류 수단이다 — 식별자
    누락 시 fallback chain 이 발동 안 한다."""

    def test_detects_qwen_data_inspection_failed(self) -> None:
        exc = RuntimeError(
            'OpenrouterException - {"error":{"message":"Provider returned error",'
            '"code":400,"metadata":{"raw":"{\\"error\\":{'
            '\\"message\\":\\"Input data may contain inappropriate content...\\",'
            '\\"type\\":\\"data_inspection_failed\\",'
            '\\"code\\":\\"data_inspection_failed\\"}}"}}'
        )
        assert is_content_filter_error(exc) is True

    def test_detects_inappropriate_content_phrase(self) -> None:
        exc = RuntimeError("Input data may contain Inappropriate content")
        assert is_content_filter_error(exc) is True

    def test_detects_openai_content_policy_violation(self) -> None:
        exc = RuntimeError("BadRequestError: content_policy_violation")
        assert is_content_filter_error(exc) is True

    def test_ignores_unrelated_errors(self) -> None:
        assert is_content_filter_error(RuntimeError("rate limit exceeded")) is False
        assert is_content_filter_error(TimeoutError("read timeout")) is False
        assert is_content_filter_error(ValueError("bad json")) is False
