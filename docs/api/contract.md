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

step 1·2 는 본 문서에 정리. step 3·4 는 자체 PR 에서 추가한다.

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

## `GET /api/plazas/{plaza_id}/report`

완료된 plaza 의 결정적 집계 (`DataAggregator.aggregate`). LLM 분석 없음 —
같은 events.jsonl 은 항상 같은 응답. LLM 인사이트는 step 4 에서 추가.

응답 200:

```json
{
  "plaza_id": "ab12cd34...",
  "label": "smoke-2026-05",
  "status": "completed",
  "rounds_total": 5,
  "rounds_done": 5,
  "tokens_used": 12345,
  "n_events": 87,
  "n_agents": 12,
  "n_rounds": 5,
  "categories": {
    "action_distribution": { "counts": { "CREATE_POST": 30, ... }, "ratios": {...}, "total": 87, "top_active_agents": [...] },
    "network_metrics": { "n_follow_events": 8, "top_followed": [...], "top_followers": [...] },
    "topic_flow": { "n_posts": 35, "posts_per_round": [...], "top_posters": [...], "samples": [...] },
    "time_series": { "rounds": [0,1,2,3,4], "series": [...] }
  },
  "qa_metrics": {
    "action_entropy_normalized": 0.78,
    "follow_clustering_coefficient": 0.12,
    "content_word_entropy_normalized": 0.65
  }
}
```

- `409`: 아직 `pending` / `running`. 부분 집계는 events.jsonl 마지막 라인이 잘려있을 수 있어 막는다.
- `failed` 도 200 으로 응답 — partial-but-valid JSONL 은 디버그 가치 있음.
- events.jsonl 자체가 없으면 (`--fake` 모드 등) 모든 카운트가 0 인 빈 집계로 폴백.

## 실행

```bash
pip install -e ".[api]"
litemiro-api --host 127.0.0.1 --port 8765 --data-dir ./runs/api
```

- `--data-dir`: plaza 별 `{plaza_id}/events.jsonl` + `checkpoints/` 가 쌓일 루트. 기본 `./runs/api`.
- `--fake`: LLM 키 없이 dummy runner 로 기동 — 프론트 라우팅/폴링 검증용.
- 실 모드는 `.env` 의 `OPENROUTER_API_KEY` 필요. `LiteLLMClient` + `STEmbedder` 를 프로세스당 한 번만 로드해 모든 plaza 가 공유.

## 영속화

- events.jsonl + checkpoints/ 는 `--data-dir` 에 디스크 영속.
- 그러나 plaza 메타데이터(상태, label, 토큰) 자체는 step 2 까지 in-memory.
  프로세스 재시작 시 status/report 호출은 404. step 3 (SSE) 와 같이 SQLite 영속화 검토.
