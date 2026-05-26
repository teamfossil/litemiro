# Litemiro HTTP API 계약

Mirofish 시뮬레이션을 프론트엔드(Vite/React) 에서 띄우고 결과를 받기 위한
얇은 HTTP 표면. 단계별 PR 로 쌓고 본 문서가 최종 계약 사실판.

## 단계

| Step | PR 범위 | 엔드포인트 |
|------|---------|-----------|
| 1 | 골격 | `GET /api/health`, `POST /api/plazas`, `GET /api/plazas/{id}/status` |
| 2 | 보고서 | `GET /api/plazas/{id}/report` + 프론트 어댑터 |
| 3 | 라이브 | `GET /api/plazas/{id}/events` (SSE) |
| 4 | LLM 본문 | `/report` 에 `report_markdown` 합류 (ReportComposer) |
| 5 | `composing` + preset | 합성 구간 상태 + `CreatePlazaRequest.preset` |

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
  "label": "smoke-2026-05",
  "preset": "quick"
}
```

- `ontology_a_path` / `ontology_b_path`: Phase 1 산출. 서버 프로세스가 읽을 수 있어야 한다 (현재는 로컬 경로만).
- `rounds`: 1–200.
- `label`: 선택. 프론트가 plaza 목록에서 식별하기 위한 자유 문자열.
- `preset`: 선택. `"quick"` (기본) / `"standard"` / `"full"`. 보고서 합성 시 LLM 호출 수 (quick=1 / standard=4 / full=8) 를 결정 — 시뮬레이션 자체와는 직교. 알 수 없는 값은 422.
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
pending → running → composing → completed
                              ↘ failed
```

- `composing`: 시뮬레이션 라운드는 다 끝났고 (`rounds_done == rounds_total`) LLM 보고서를 합성 중인 구간 — terminal 아님. 프론트는 progress 100% + "보고서 합성중" 으로 표시. SSE 의 terminal 판정도 composing 을 끊지 않는다.
- `failed` 일 때만 `error` 가 non-null (`"<ExceptionType>: <message>"`). composing 단계에서 LLM 이 폴백까지 전부 실패해도 `completed` — 본문은 `null` 로 나간다 (`/report` 참고).
- `404`: 존재하지 않는 `plaza_id` (프로세스 재시작 후 in-memory 가 비었을 때 포함).

## `GET /api/plazas/{plaza_id}/report`

결정적 집계 (`DataAggregator.aggregate`) + LLM 본문 (`ReportComposer`). 집계
부분은 같은 events.jsonl 이면 항상 같은 응답. `report_markdown` 은 step 4
에서 합류 — preset 별 LLM 호출 수가 다르고 폴백 경로가 있어 본문은
재현 보장이 약하다.

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
  },
  "report_markdown": "## 라운드별 요약\n...",
  "report_fallback_used": false
}
```

- `report_markdown`: ReportComposer 가 만든 Markdown 본문. `null` 가능 — `status` 와 함께 읽어야 의미가 갈린다 (아래 표).
- `report_fallback_used`: Opus + Qwen 폴백까지 전부 실패해서 본문이 비었을 때만 `true`. `completed` 인데 markdown 이 `null` 이면 이 값으로 "폴백 실패" 와 "아직 합성중" 을 구분한다.
- `409`: 아직 `pending` / `running`. 부분 집계는 events.jsonl 마지막 라인이 잘려있을 수 있어 막는다. **`composing` 은 409 가 아니다** — sim 라운드가 끝났으니 events.jsonl 은 완전하다고 보고 200 으로 집계는 돌려준다 (markdown 은 아래 표 참고).
- `failed` 도 200 으로 응답 — partial-but-valid JSONL 은 디버그 가치 있음.
- events.jsonl 자체가 없으면 (`--fake` 모드 등) 모든 카운트가 0 인 빈 집계로 폴백.

`report_markdown` × `status` 조합:

| `status` | `report_markdown` | `report_fallback_used` | 의미 |
|----------|------------------|------------------------|------|
| `composing` | `null` | `false` | 합성 진행중 — 폴링 또는 SSE 로 `completed` 대기 |
| `completed` | `"..."` | `false` | 정상 완료 |
| `completed` | `null` | `true` | Opus + Qwen 둘 다 실패 — 통계만 렌더 |
| `failed` | `null` | `false` | 시뮬레이션 자체가 죽음 — partial 집계만 |

## `GET /api/plazas/{plaza_id}/events`

SSE 진행률 스트림. 프론트가 `/status` 를 폴링하지 않고 라운드 단위 progress
와 상태 머신 전환을 push 로 받는다.

응답 200 (`text/event-stream`). 두 종류의 이벤트:

```
event: status
data: {"status":"pending","rounds_done":0,"rounds_total":5,"error":null}

event: progress
data: {"rounds_done":1,"rounds_total":5}

event: status
data: {"status":"running","rounds_done":0,"rounds_total":5,"error":null}

event: progress
data: {"rounds_done":5,"rounds_total":5}

event: status
data: {"status":"composing","rounds_done":5,"rounds_total":5,"error":null}

event: status
data: {"status":"completed","rounds_done":5,"rounds_total":5,"error":null}
```

- 연결 즉시 현재 status 를 한 번 보낸다 (초기 sync).
- `event: progress`: 라운드 1건 종료. SSE 단독으로 progress bar 가 갱신된다.
- `event: status`: 상태 머신 전환 (pending→running, running→composing, composing→completed/failed). `composing` 도 정식 이벤트라 프론트는 sim 종료와 보고서 합성 시작을 분리해서 표시 가능.
- `status` 가 **terminal** (`completed` / `failed`) 이면 본 이벤트가 스트림의 마지막 — 서버가 응답을 닫는다. `composing` 은 terminal 아님.
- 연결 시점에 record 가 이미 terminal 이면 첫 status 이벤트 직후 스트림이 닫힌다.
- 큐가 한동안 비면 15 초마다 `: keepalive` SSE comment 가 나간다 (클라 `onmessage` 에는 안 잡힘 — proxy idle timeout 방지용).
- `404`: 존재하지 않는 `plaza_id`.
- 라운드 단위 액션 스트림 (events.jsonl 라이브 tail) 은 아직 미포함 — 동일 라우트에 추후 `event: action` 으로 얹는다.

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
- 그러나 plaza 메타데이터(상태, label, 토큰, preset, markdown) 자체는 현재 in-memory.
  프로세스 재시작 시 status/report/events 호출은 404. SQLite 영속화는 별도 작업으로 남겨둔다.
