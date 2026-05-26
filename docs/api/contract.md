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

- `ontology_a_path` / `ontology_b_path`: 선택. Phase 1 산출. 서버 프로세스가 읽을 수 있어야 한다 (현재는 로컬 경로만). **생략하면** repo 의 dev fixture (`tests/data/sample_ontology_a.json` / `sample_ontology_b.json`) 로 폴백 — 자료 업로드 UI 가 아직 없는 프론트 Seed 화면이 dummy path 를 매번 박지 않아도 호출할 수 있게 한다. 명시한다면 길이 1 이상의 절대 경로 (빈 문자열은 422). 어디까지나 dev 편의 폴백이므로, 배포 환경에서 fixture 가 없으면 후속 `/agents` 등에서 404 로 떨어진다.
- `rounds`: 1–200.
- `label`: 선택. 프론트가 plaza 목록에서 식별하기 위한 자유 문자열.
- `preset`: 선택. `"quick"` (기본) / `"standard"` / `"full"`. 보고서 합성 시 LLM 호출 수 (quick=1 / standard=4 / full=8) 를 결정 — 시뮬레이션 자체와는 직교. 알 수 없는 값은 422.
- 알 수 없는 필드는 422 거절 (`extra="forbid"`).

경로 둘 다 생략한 최소 호출 예시 (프론트 quick preset 카드 → POST):

```json
{ "rounds": 5, "preset": "quick" }
```

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
- `404`: DB 에도 존재하지 않는 `plaza_id`.

## `GET /api/plazas/{plaza_id}/agents`

Casting 화면이 슬롯에 띄울 앵커 리스트. plaza 에 묶인 `ontology_a_persona.json`
(Phase 1 산출) 의 `agents` 를 라우트가 직접 읽어 시각화에 의미 있는 필드만 추려
돌려준다. plaza 생성 시점에 ontology_a 가 이미 존재하므로 **pending / running**
에서도 200 으로 떨어진다 — sim 시작 전부터 앵커 슬롯 그릴 수 있다.

응답 200:

```json
{
  "plaza_id": "ab12cd34...",
  "agents": [
    { "id": "agent_001", "name": "AI 기본법", "role": "AIRegulationPolicy", "ideology": 0.65, "topics": ["AI 규제 기본 원칙"], "avatar_seed": 2853741920 },
    { "id": "agent_002", "name": "스타트업 협회", "role": "IndustryGroup", "ideology": 0.30, "topics": [...], "avatar_seed": 1937204815 }
  ]
}
```

- `id`: `AgentProfile.agent_id`.
- `role`: `AgentProfile.entity_type` raw 값 — 백엔드는 enum 으로 안 좁힌다 (새 카테고리 추가될 때마다 백엔드 패치하지 않으려고). 프론트가 아래 매핑 테이블로 `RoleId` 로 좁힌다.
- `ideology`: 0.0 ~ 1.0. **0.0 = 진보 / 1.0 = 보수** (Phase 1 ontology 추출 단계 의미). 0.5 근처는 중도/판단 보류.
- `topics`: `AgentProfile.topics`. 자유 문자열 리스트.
- `avatar_seed`: `sha256(agent_id)[:4]` 의 uint32. 같은 plaza/같은 agent 면 reload·재연결에서도 동일 — 프론트 deterministic avatar 가 안 튄다. 백엔드가 직접 계산하는 이유는 프론트 해시 알고리즘 변경/언어 차이로 시드 어긋나는 걸 막기 위해.
- `404`: 존재하지 않는 `plaza_id`, 또는 `ontology_a_path` 가 디스크에 없는 경우.
- `500`: 파일이 있지만 스키마 파싱 실패 — Phase 1 산출이 손상된 경우.

`role` → 프론트 `RoleId` 매핑 (SSoT — 본 문서 기준):

| Phase 1 `entity_type` | 프론트 `RoleId` | 의미 |
|----------------------|----------------|------|
| `AIRegulationPolicy` | `policy` | AI 규제/정책 문서·법안 |
| `Government` | `policy` | 정부 부처·공공기관 |
| `IndustryGroup` | `industry` | 산업 협회·연합 |
| `Company` | `industry` | 개별 기업 |
| `Researcher` | `expert` | 학계·연구 기관·전문가 |
| `CivicGroup` | `civic` | 시민단체·NGO |
| `Media` | `media` | 언론사·매체 |
| (그 외) | `other` | 신규 카테고리 — 백엔드 변경 없이 프론트 fallback 으로 흡수 |

새 `entity_type` 이 Phase 1 에서 도입되면 본 표만 추가하면 된다 — 백엔드 응답 형식은 그대로.

## `GET /api/plazas/{plaza_id}/layout`

Plaza 부감 뷰 화면이 노드를 배치할 때 쓸 좌표 + 영향력. plaza 의 events.jsonl
에서 FOLLOW 엣지를 모아 Fruchterman-Reingold force-directed layout 을 돌린다
(numpy, networkx 불필요). 시드는 `plaza_id` 해시 — 같은 plaza 면 폴링/리로드
어디서 불러도 좌표가 안 튄다.

`/agents` 와 같은 게이팅 — pending / running 에도 **200** 으로 떨어진다. 단
events.jsonl 이 아직 안정적이지 않으므로 `ready: false` + `agents: []` 로
응답해 프론트가 "아직 부감 데이터 없음" UI 를 그릴 수 있게 한다.

응답 200:

```json
{
  "plaza_id": "ab12cd34...",
  "ready": true,
  "width": 1.0,
  "height": 1.0,
  "agents": [
    {
      "id": "agent_001", "name": "AI 기본법", "role": "AIRegulationPolicy",
      "x": 0.42, "y": 0.71,
      "influence": 1.0, "follower_count": 5,
      "avatar_seed": 2853741920
    },
    {
      "id": "agent_002", "name": "스타트업 협회", "role": "IndustryGroup",
      "x": 0.13, "y": 0.55,
      "influence": 0.4, "follower_count": 2,
      "avatar_seed": 1937204815
    }
  ]
}
```

- `ready`: `true` 면 sim 라운드 끝나 events.jsonl 이 안정적 (composing /
  completed / failed). `false` 면 `agents=[]` — pending / running 인 동안만
  떨어진다. 프론트는 `ready` 로 부감 뷰 빈 상태 / 채워진 상태를 분기.
- `x` / `y`: `[0.0, 1.0]` 정규화 좌표. 프론트가 캔버스 크기 곱해 그린다.
- `follower_count`: events.jsonl 의 FOLLOW 이벤트에서 해당 agent 가 받은
  follow 수 (절대값).
- `influence`: 같은 plaza 내 `follower_count` 최댓값으로 정규화한 `[0.0, 1.0]`
  값. 최댓값을 가진 노드는 1.0, 아무도 안 따른 노드는 0.0. 노드 크기/색
  매핑에 그대로 곱해 쓰면 된다. 모든 agent 의 `follower_count` 가 0 이면
  전부 0.0.
- `avatar_seed`: `/agents` 와 동일한 uint32. 같은 agent 면 두 응답이 같은 값.
- `width` / `height`: 좌표 박스. 현재 항상 1.0 x 1.0 — 향후 비정방 화면에
  맞춰 늘릴 여지.
- `404`: 존재하지 않는 `plaza_id`, 또는 `ontology_a_path` 가 디스크에 없는 경우.
- `500`: ontology_a 가 있지만 스키마 파싱 실패.
- events.jsonl 자체가 없으면 (`--fake` 모드 등) 엣지 0 으로 계산 — `ready`
  는 record status 기준이라 ontology_a 만 있으면 그래도 `true` 로 떨어진다.

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

SSE 진행률 스트림. 프론트가 `/status` 를 폴링하지 않고 라운드 단위 progress,
상태 머신 전환, 그리고 events.jsonl 의 라이브 액션을 push 로 받는다.

응답 200 (`text/event-stream`). 네 종류의 이벤트:

```
event: status
data: {"status":"running","rounds_done":2,"rounds_total":5,"error":null}

event: actions_snapshot
data: {"actions":[{"round_num":0,"agent_id":"a1","type":"CREATE_POST","target_post_id":null,"target_agent_id":null,"content":"hello","timestamp":"2026-05-26T12:34:56.789012+00:00"}, ...]}

event: action
data: {"round_num":3,"agent_id":"a2","type":"FOLLOW","target_post_id":null,"target_agent_id":"a1","content":null,"timestamp":"2026-05-26T12:34:58.123456+00:00"}

event: progress
data: {"rounds_done":3,"rounds_total":5}

event: status
data: {"status":"composing","rounds_done":5,"rounds_total":5,"error":null}

event: status
data: {"status":"completed","rounds_done":5,"rounds_total":5,"error":null}
```

- 연결 즉시 현재 status 를 한 번 보낸다 (초기 sync).
- 그 직후 `event: actions_snapshot` 이 한 번 떨어진다 — 재연결/탭 전환 후 빈
  부감 뷰로 시작하지 않게 events.jsonl 의 최근 40 건을 한 번에 흘려준다.
  `data.actions` 는 element 가 `event: action` 과 동일한 shape, 시간 오름차순
  배열. 액션이 0 건이거나 events.jsonl 이 아직 없으면 본 이벤트는 생략된다.
- `event: progress`: 라운드 1건 종료. SSE 단독으로 progress bar 가 갱신된다.
- `event: status`: 상태 머신 전환 (pending→running, running→composing, composing→completed/failed). `composing` 도 정식 이벤트라 프론트는 sim 종료와 보고서 합성 시작을 분리해서 표시 가능.
- `event: action`: events.jsonl 의 한 줄 = agent 1 명의 액션 1 건. 부감 뷰가 노드 깜빡임 / 엣지 추가 / 새 포스트 토스트 등에 쓴다. `type` 은 `ActionType` enum (`CREATE_POST` / `LIKE_POST` / `REPOST` / `QUOTE_POST` / `FOLLOW`). 타입별로 `target_post_id` / `target_agent_id` / `content` 중 의미 있는 필드만 채워지고 나머지는 `null`. **`DO_NOTHING` 은 SSE 단계에서 컷** — events.jsonl 에는 그대로 남아 집계/재현성은 유지되지만, 스트림은 의미 있는 액션만 전달한다 (`actions_snapshot` 도 동일 필터).
- 액션은 events.jsonl 폴링 tail (50 ms 간격) 로 흘려 보낸다 — runner 의 라운드 끝 flush 와 SSE push 사이 latency 가 라운드 wall-clock 에 비해 무시 가능. 서버는 terminal status emit 직전 마지막 drain 을 한 번 더 돌려 마지막 라인 누락을 막는다.
- events.jsonl 의 라인 순서는 **runner 가 호출 순서 그대로 append 한 결과**. 같은 시드 / 같은 환경이면 같은 순서. SSE `event: action` 도 같은 순서로 흘러나오고 `actions_snapshot` 의 배열 순서도 동일. 라운드 안에서 여러 agent 의 액션은 runner 의 agent 처리 순서로 줄지어 들어간다.
- `status` 가 **terminal** (`completed` / `failed`) 이면 본 이벤트가 스트림의 마지막 — 서버가 응답을 닫는다. `composing` 은 terminal 아님.
- 연결 시점에 record 가 이미 terminal 이면 첫 status 이벤트 + `actions_snapshot` (있으면) 직후 스트림이 닫힌다 — 과거 액션을 받을 마지막 기회.
- 큐가 한동안 비면 15 초마다 `: keepalive` SSE comment 가 나간다 (클라 `onmessage` 에는 안 잡힘 — proxy idle timeout 방지용).
- `404`: 존재하지 않는 `plaza_id`.

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
- plaza 메타데이터(상태, label, 토큰, preset, markdown 등) 는 `--data-dir/plazas.db`
  (SQLite, WAL) 에 영속. 프로세스 재시작 후에도 같은 plaza_id 로 `/status` /
  `/report` / `/events` 가 디스크 산출물을 다시 바라본다.
- 라운드 단위 progress 까지 영속 — `on_progress` 콜백마다 upsert 1회. 따라서
  재시작 시 `rounds_done` 은 마지막 commit 된 라운드 그대로.
- 재시작 시점에 마지막 commit 된 status 가 `pending` / `running` / `composing`
  인 row 는 도중에 죽은 것으로 보고 `failed` + `error="process restarted while
  <prev>"` 로 강제 마킹한다 — checkpoint 기반 자동 재개는 별도 작업.
- 비 영속 (프로세스 lifetime 한정): asyncio Task, SSE subscriber 큐, `DataAggregator`
  결과 캐시 (events.jsonl 로 lazy 재집계).
