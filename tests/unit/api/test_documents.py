"""``/api/documents`` 라우트 단위 테스트.

multipart 업로드, sqlite row 생성, 디스크 저장 + 검증 분기(크기/확장자/빈
파일)를 닫는다. ``DocumentStore`` 는 LLM 의존이 없어 별도 fake 없이 그대로
실서비스 코드 그대로 띄운다 — runner 만 noop 으로 둔다.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from litemiro.api.app import create_app
from litemiro.api.routes.documents import MAX_UPLOAD_BYTES
from litemiro.api.store import ProgressCallback, RunnerOutcome


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
        await asyncio.sleep(0)
        on_progress(rounds_done=r + 1)
    return RunnerOutcome()


class TestUploadDocument:
    def test_returns_201_with_metadata(self, tmp_path: Path) -> None:
        app = create_app(runner=_noop_runner, base_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.post(
                "/api/documents",
                files={"file": ("hello.txt", b"hello world", "text/plain")},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert isinstance(body["document_id"], str)
        assert len(body["document_id"]) >= 16
        assert body["filename"] == "hello.txt"
        assert body["mime_type"] == "text/plain"
        assert body["size_bytes"] == len(b"hello world")
        assert len(body["sha256"]) == 64

    def test_persists_file_to_disk(self, tmp_path: Path) -> None:
        app = create_app(runner=_noop_runner, base_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.post(
                "/api/documents",
                files={"file": ("note.txt", b"persisted bytes", "text/plain")},
            )
        assert resp.status_code == 201
        document_id = resp.json()["document_id"]
        storage = tmp_path / "documents" / f"{document_id}.txt"
        assert storage.exists()
        assert storage.read_bytes() == b"persisted bytes"

    def test_rejects_missing_filename(self, tmp_path: Path) -> None:
        app = create_app(runner=_noop_runner, base_dir=tmp_path)
        with TestClient(app) as client:
            # filename 비우면 multipart 가 빈 문자열을 보낸다.
            resp = client.post(
                "/api/documents",
                files={"file": ("", b"data", "text/plain")},
            )
        # FastAPI 가 빈 filename 을 422 (missing) 로 차단하거나 라우트의
        # 자체 422 가 잡거나 — 둘 중 어느 쪽이든 4xx 면 됨.
        assert resp.status_code in {400, 422}

    def test_rejects_disallowed_extension(self, tmp_path: Path) -> None:
        app = create_app(runner=_noop_runner, base_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.post(
                "/api/documents",
                files={"file": ("evil.exe", b"MZ\x90...", "application/octet-stream")},
            )
        assert resp.status_code == 422
        assert "unsupported extension" in resp.json()["detail"]

    def test_rejects_empty_file(self, tmp_path: Path) -> None:
        app = create_app(runner=_noop_runner, base_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.post(
                "/api/documents",
                files={"file": ("empty.txt", b"", "text/plain")},
            )
        assert resp.status_code == 422
        assert "empty file" in resp.json()["detail"]

    def test_rejects_too_large(self, tmp_path: Path) -> None:
        app = create_app(runner=_noop_runner, base_dir=tmp_path)
        oversized = b"a" * (MAX_UPLOAD_BYTES + 1)
        with TestClient(app) as client:
            resp = client.post(
                "/api/documents",
                files={"file": ("big.txt", oversized, "text/plain")},
            )
        assert resp.status_code == 413


class TestGetDocument:
    def test_404_for_unknown(self, tmp_path: Path) -> None:
        app = create_app(runner=_noop_runner, base_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.get("/api/documents/does-not-exist")
        assert resp.status_code == 404

    def test_round_trip(self, tmp_path: Path) -> None:
        app = create_app(runner=_noop_runner, base_dir=tmp_path)
        with TestClient(app) as client:
            up = client.post(
                "/api/documents",
                files={"file": ("doc.txt", b"hello", "text/plain")},
            )
            document_id = up.json()["document_id"]
            resp = client.get(f"/api/documents/{document_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["document_id"] == document_id
        assert body["filename"] == "doc.txt"
        assert body["size_bytes"] == 5


class TestListDocuments:
    def test_empty(self, tmp_path: Path) -> None:
        app = create_app(runner=_noop_runner, base_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.get("/api/documents")
        assert resp.status_code == 200
        assert resp.json() == {"documents": []}

    def test_lists_uploaded(self, tmp_path: Path) -> None:
        app = create_app(runner=_noop_runner, base_dir=tmp_path)
        with TestClient(app) as client:
            client.post(
                "/api/documents",
                files={"file": ("a.txt", b"aaa", "text/plain")},
            )
            client.post(
                "/api/documents",
                files={"file": ("b.txt", b"bb", "text/plain")},
            )
            resp = client.get("/api/documents")
        assert resp.status_code == 200
        names = {d["filename"] for d in resp.json()["documents"]}
        assert names == {"a.txt", "b.txt"}
