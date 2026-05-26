# Litemiro HTTP API 계약

Mirofish 시뮬레이션을 프론트엔드(Vite/React) 에서 띄우고 결과를 받기 위한
얇은 HTTP 표면. 4 단계에 걸쳐 단계별 PR 로 쌓는다.

## 단계

| Step | PR 범위 | 엔드포인트 |
|------|---------|-----------|
| 1 | 골격 | `GET /api/health`, `POST /api/plazas`, `GET /api/plazas/{id}/status` |
| 2 | 보고서 | `GET /api/plazas/{id}/report` + 프론트 어댑터 |
| 3 | 라이브 | `GET /api/plazas/{id}/live` (SSE) |
| 4 | 구조화 | Phase 3 ReportComposer JSON 출력 정렬 |

본 문서는 step 1 만 자세히 다룬다. 이후 단계는 자체 PR 에서 본 문서를 늘려간다.

## 공통

- Base prefix: `/api`
- Content-Type: `application/json`
- 에러 형식: FastAPI 기본 (`{ "detail": "..." }`)
- CORS: 기본 `http://localhost:5173` (Vite 개발 서버). `litemiro-api --cors-origin` 으로 추가 가능.

## `GET /api/health`

서버 살아 있는지 + 패키지 버전 확인.

응답 200:

```json
{ "status": "ok", "version": "0.1.0" }
```

## `POST /api/plazas`

시뮬레이션 1 건을 백그라운드로 띄운다. 즉시 202 와 `plaza_id` 반환.

요청 본문:

```json
{
  "ontology_a_path": "/abs/path/to/ontology_a.json",
  "ontology_b_path": "/abs/path/to/ontology_b.json",
  "rounds": 5,
  "label": "smoke-2026-05"
}
```

- `ontology_a_path` / `ontology_b_path`: Phase 1 산출. 서버 프로세스가 읽을 수 있어야 한다 (현재는 로컬 경로만).
- `rounds`: 1–200.
- `label`: 선택. 프론트가 plaza 목록에서 식별하기 위한 자유 문자열.
- 알 수 없는 필드는 422 거절 (`extra="forbid"`).

응답 202:

```json
{ "plaza_id": "ab12cd34...", "status": "pending" }
```

`status` 는 즉시 응답 시점에 `pending` 일 수도 `running` 일 수도 있다 — 폴링으로 확인.

## `GET /api/plazas/{plaza_id}/status`

응답 200:

```json
{
  "plaza_id": "ab12cd34...",
  "status": "running",
  "rounds_total": 5,
  "rounds_done": 2,
  "label": "smoke-2026-05",
  "error": null
}
```

`status` 상태 머신:

```
pending → running → completed
                  ↘ failed
```

- `failed` 일 때만 `error` 가 non-null (`"<ExceptionType>: <message>"`).
- `404`: 존재하지 않는 `plaza_id` (프로세스 재시작 후 in-memory 가 비었을 때 포함).

## 실행

```bash
pip install -e ".[api]"
litemiro-api --host 127.0.0.1 --port 8765
```

step 1 의 runner 는 즉시 완료되는 placeholder. step 2 PR 에서 실
`run_simulation` 으로 교체.

## 영속화

step 1 은 in-memory. 프로세스가 죽으면 모든 plaza 가 사라진다.
step 3 (SSE) 와 함께 SQLite 영속화를 도입할지 결정.
