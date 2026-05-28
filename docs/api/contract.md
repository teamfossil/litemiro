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
| 6 | 목록 | `GET /api/plazas` — 최신순 plaza 카드 리스트 + 페이지·필터 |
| 7 | 자료 + 온톨로지 | `POST /api/documents`, `POST /api/ontologies` + plaza `ontology_id` 연결 |

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
  "ontology_id": "ab12cd34...",
  "ontology_a_path": "/abs/path/to/ontology_a.json",
  "ontology_b_path": "/abs/path/to/ontology_b.json",
  "rounds": 5,
  "label": "smoke-2026-05",
  "preset": "quick"
}
```

- `ontology_id`: 선택. `POST /api/ontologies` 로 만든 Phase 1 산출물의 식별자. 명시하면 `ontology_a_path/b_path` 와 dev fixture 폴백을 **모두 무시**하고 그 ontology 의 두 JSON 경로를 그대로 사용한다 — 사용자 PDF 가 실제 시뮬에 반영되는 정공 경로. 상세는 아래 분기:
  - 해당 ontology 가 없으면 404.
  - status 가 `completed` 가 아니면 409 (아직 합성중이거나 실패).
  - `ontology_store` 가 서버에 안 붙어 있으면 (`--fake` 등) 503.
- `ontology_a_path` / `ontology_b_path`: 선택. Phase 1 산출. 서버 프로세스가 읽을 수 있어야 한다 (현재는 로컬 경로만). **생략하면** repo 의 dev fixture (`tests/data/sample_ontology_a.json` / `sample_ontology_b.json`) 로 폴백 — 자료 업로드 UI 가 아직 없는 프론트 Seed 화면이 dummy path 를 매번 박지 않아도 호출할 수 있게 한다. 명시한다면 길이 1 이상의 절대 경로 (빈 문자열은 422). 어디까지나 dev 편의 폴백이므로, 배포 환경에서 fixture 가 없으면 후속 `/agents` 등에서 404 로 떨어진다. `ontology_id` 가 함께 들어오면 무시된다.
- `rounds`: 1–200.
- `label`: 선택. 프론트가 plaza 목록에서 식별하기 위한 자유 문자열.
- `preset`: 선택. `"quick"` (기본) / `"standard"` / `"full"`. 두 가지를 한꺼번에 결정한다 — (a) 보고서 합성 시 LLM 호출 수 (quick=1 / standard=4 / full=8), (b) `POST /api/ontologies` 로 만든 ontology 의 agent 수 (quick=100 / standard=300 / full=500). plaza 의 preset 과 ontology 의 preset 은 서로 다른 값이어도 무방 — 백엔드가 일치 검사를 강제하지 않는다. 알 수 없는 값은 422.
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

## `GET /api/plazas`

지금까지 만든 plaza 의 카드 리스트. 프론트 이력 화면 / "plaza 다시 열기" 가
이걸로 목록을 그린다. `report_markdown` 같은 큰 본문은 의도적으로 빠져 있어
카드 행 단위로 KB 를 끌어오는 일이 없다 — 상세는 `/{id}/report` 로.

쿼리 파라미터:

- `limit`: 한 페이지 행 수. 기본 `20`, 1–100. 범위 밖은 422.
- `offset`: 페이지 시작 인덱스. 기본 `0`. 음수는 422. `cursor` 와 같이 보내면
  서버가 무시 (응답의 `offset` 도 `0` 으로 정규화).
- `cursor`: 선택. opaque 문자열 — 이전 응답의 `next_cursor` 를 그대로 다시
  넣는다. keyset 페이징이라 깊은 `offset` 비용을 피한다. 변조된 값은 422
  (silent 한 빈 페이지로 가지 않게).
- `status`: 선택. `pending` / `running` / `composing` / `completed` / `failed`
  중 하나. 지정하면 그 상태인 행만, 그리고 `total` 도 그 필터를 적용한 후의
  전체 개수를 반환 — "총 N건" 위젯이 페이지 수와 일치한다. 알 수 없는 값은
  422.

정렬은 `created_at DESC, plaza_id DESC` — 최신 plaza 가 위, 같은 second 안의
행은 `plaza_id` 로 결정적 tie-break. SQLite 에는 `(created_at DESC, plaza_id
DESC)` 와 `(status)` 인덱스가 걸려 있어 큰 목록도 빠르다.

응답 200:

```json
{
  "plazas": [
    {
      "plaza_id": "ab12cd34...",
      "status": "completed",
      "rounds_total": 5,
      "rounds_done": 5,
      "label": "smoke-2026-05",
      "error": null,
      "preset": "quick",
      "tokens_used": 1234,
      "created_at": "2026-05-26T07:08:40Z",
      "updated_at": "2026-05-26T07:09:12Z"
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0,
  "next_cursor": null
}
```

`created_at` / `updated_at` 은 UTC ISO-8601 (second 정밀도). plaza store 가
SQLite 영속화라 프로세스 재시작 후에도 같은 목록이 그대로 나온다.

`next_cursor` 는 다음 페이지가 있을 가능성이 있으면 opaque 문자열, 마지막
페이지면 `null`. infinite scroll 같은 경우 첫 호출은 cursor 없이 시작하고,
이후 응답의 `next_cursor` 를 다음 호출의 `?cursor=` 에 그대로 박는다. 한
페이지가 정확히 `limit` 만큼 차고 그게 끝이면 다음 호출이 빈 페이지 + `null`
cursor 로 끝을 통지한다 (keyset 정석 — 서버가 미리 "끝" 을 알 수 없는 경우의
한 번 더 round-trip).

## `DELETE /api/plazas/{plaza_id}`

plaza 한 건을 메모리·SQLite·디스크 산출물 (events.jsonl + checkpoints/) 까지
통째로 정리한다. 잘못 만들었거나 끝까지 기다리고 싶지 않은 sim 을 즉시 치우는
용도.

상태에 관계없이 받는다 — `pending` / `running` / `composing` 도 cancel 후
정리된다 (409 로 막지 않는다). 단, 한 번 정리된 plaza_id 의 두 번째 DELETE
는 404.

응답:

- `204 No Content`: 삭제 성공. body 없음.
- `404 Not Found`: 그 `plaza_id` 가 (이미 지워졌거나) 없는 경우.

같은 plaza 의 `/status` / `/report` / `/agents` / `/layout` 은 DELETE 직후
부터 404. `GET /api/plazas` 목록에서도 빠진다. SQLite row 도 같이 지워지므로
프로세스 재시작 후에도 살아나지 않는다.

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
    { "id": "agent_001", "name": "AI 기본법", "role": "AIRegulationPolicy", "ideology": 0.65, "topics": ["AI 규제 기본 원칙"], "base_influence": 0.43, "avatar_seed": 2853741920 },
    { "id": "agent_002", "name": "스타트업 협회", "role": "IndustryGroup", "ideology": 0.30, "topics": [...], "base_influence": 0.58, "avatar_seed": 1937204815 }
  ]
}
```

- `id`: `AgentProfile.agent_id`.
- `role`: `AgentProfile.entity_type` raw 값 — 백엔드는 enum 으로 안 좁힌다 (새 카테고리 추가될 때마다 백엔드 패치하지 않으려고). 프론트가 아래 매핑 테이블로 `RoleId` 로 좁힌다.
- `ideology`: 0.0 ~ 1.0. **0.0 = 진보 / 1.0 = 보수** (Phase 1 ontology 추출 단계 의미). 0.5 근처는 중도/판단 보류.
- `topics`: `AgentProfile.topics`. 자유 문자열 리스트.
- `base_influence`: 0.0 ~ 1.0. `behavior_tendency` 가중합으로 산출한 prior 영향력. Phase 2 가 도는 동안의 engagement-weighted `/layout` `influence` 와 달리 sim 결과와 무관하고 ontology 만으로 결정되는 "광장 진입 전" 정적 기대치. Casting 화면이 "주역" 같은 노출 우선순위 / Badge 분기에 쓴다. 가중치 (합 = 1.0 → 결과는 항상 [0, 1]):
  - `post_rate × 0.45` — 새 post 생성, 다른 agent feed 진입 (가장 강한 신호)
  - `reply_rate × 0.20` — LIKE / REPOST / QUOTE total reaction
  - `repost_rate × 0.15` — 재공유로 노출 증폭
  - `controversy_affinity × 0.15` — 광장에서 회자될 가능성
  - `follow_rate × 0.05` — out-degree (본인 영향력 prior 와 약한 상관)
  - `like_rate` 는 `reply_rate` 의 split 의 일부 (`reply_rate = like + repost + quote` 정의) 라 별도 가중치를 안 박는다.
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

Plaza 부감 뷰 화면이 노드를 배치할 때 쓸 좌표 + 영향력. 좌표는 의미 차원 직접
매핑 — `x = ontology_a.profile.ideology` (Phase 1 이 박은 정적 좌-우 spectrum,
0=비판적/1=우호적), `y = 같은 plaza 내 활동량 (DO_NOTHING 제외 액션 카운트)
최댓값 정규화`. 같은 plaza 면 폴링/리로드 어디서 불러도 x 는 안 튀고 y 는
라운드가 가며 monotonically 증가한다. FR force-directed 을 떼낸 이유는 sim 의
follower=0 long-tail 에서 1D 로 압축되는 측정값 때문 — 정적 prior + 라이브
활동량으로 갈라 노드가 한 점에 뭉치는 걸 피한다 (#133).

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
      "x": 0.65, "y": 1.0,
      "influence": 1.0, "follower_count": 5,
      "avatar_seed": 2853741920
    },
    {
      "id": "agent_002", "name": "스타트업 협회", "role": "IndustryGroup",
      "x": 0.30, "y": 0.4,
      "influence": 0.4, "follower_count": 2,
      "avatar_seed": 1937204815
    }
  ]
}
```

- `ready`: `true` 면 sim 라운드 끝나 events.jsonl 이 안정적 (composing /
  completed / failed). `false` 면 `agents=[]` — pending / running 인 동안만
  떨어진다. 프론트는 `ready` 로 부감 뷰 빈 상태 / 채워진 상태를 분기.
- `x`: `ontology_a` 의 `AgentProfile.ideology` 그대로 (`[0.0, 1.0]`, 0=비판적,
  1=우호적). 정적이라 plaza 진행과 무관 — `/agents` 와 같은 값.
- `y`: 같은 plaza 내 활동량 (events.jsonl 의 DO_NOTHING 제외 액션 카운트) 최댓값
  정규화 `[0.0, 1.0]`. 라운드가 가면 monotonically 증가 (활동 없는 agent 는 0.0).
  모든 agent 의 활동량이 0 이면 전부 0.0.
- `follower_count`: events.jsonl 의 FOLLOW 이벤트에서 해당 agent 가 받은
  follow 수 (절대값).
- `influence`: 같은 plaza 내 engagement-weighted score 최댓값으로 정규화한
  `[0.0, 1.0]`. 가중치는 받은 LIKE×1, REPOST×2, QUOTE×3, FOLLOW×5 의 합 (#132).
  FOLLOW in-degree 만 보면 sim 의 long-tail (현 표본은 follower=0 100% 비율) 에서
  노드 크기 차별이 0 으로 떨어져 LIKE/REPOST/QUOTE 까지 합산한다. 노드 크기/색
  매핑에 그대로 곱해 쓰면 된다. 모든 agent 의 점수가 0 이면 전부 0.0.
- `avatar_seed`: `/agents` 와 동일한 uint32. 같은 agent 면 두 응답이 같은 값.
- `width` / `height`: 좌표 박스. 현재 항상 1.0 x 1.0 — 향후 비정방 화면에
  맞춰 늘릴 여지.
- `404`: 존재하지 않는 `plaza_id`, 또는 `ontology_a_path` 가 디스크에 없는 경우.
- `500`: ontology_a 가 있지만 스키마 파싱 실패.
- events.jsonl 자체가 없으면 (`--fake` 모드 등) 활동량 / follower / influence 모두 0
  으로 계산. `y` 는 전 agent 0.0, `x` 는 ontology 의 ideology 그대로. `ready` 는
  record status 기준이라 ontology_a 만 있으면 그래도 `true` 로 떨어진다.

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

## `POST /api/documents`

Phase 1 generation 의 입력이 될 자료 한 건을 업로드한다. multipart/form-data
한 번 — 라벨/추가 필드는 미지정. 응답의 `document_id` 를 그대로
`POST /api/ontologies` 의 본문 `document_id` 에 넣는다.

요청 (multipart/form-data):

```
file: <PDF 또는 TXT 한 건>
```

- 허용 확장자: `.pdf`, `.txt` 두 종만 받는다. 그 외는 422.
- 최대 크기: 5 MB. 초과 시 413.
- 빈 파일 / 파일명 누락은 422.
- MIME type 은 확장자 기반 매핑 (`.pdf` → `application/pdf`, `.txt` → `text/plain`). 클라가 보낸 content-type 은 무시한다 — multipart 측이 `application/octet-stream` 으로 통일해 넘기는 경우가 많다.

응답 201:

```json
{
  "document_id": "ab12cd34...",
  "filename": "spec.pdf",
  "mime_type": "application/pdf",
  "size_bytes": 13241,
  "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "created_at": "2026-05-26T07:08:40Z"
}
```

- `document_id`: uuid4 hex. 디스크에는 `<data-dir>/documents/{document_id}{ext}` 로 저장 — 원본 파일명을 그대로 쓰면 중복/한글 등 OS 차이가 골치 아파서 UUID 로 통일한다. 원본은 row 의 `filename` 컬럼에 따로 보관.
- `sha256`: 본문 16진 해시 64자. 같은 파일 재업로드 식별용.
- 한 문서를 여러 ontology 가 재사용해도 막지 않는다 — 분리된 라우트.

## `GET /api/documents/{document_id}`

업로드된 자료 한 건의 메타데이터. 본문 자체는 노출하지 않는다 (디스크 경로
도 비공개).

응답 200: `POST /api/documents` 와 같은 shape.

- `404`: 그 `document_id` 가 없는 경우.

## `GET /api/documents`

업로드한 자료의 메타 리스트. 정렬은 `created_at DESC`.

응답 200:

```json
{
  "documents": [
    { "document_id": "...", "filename": "spec.pdf", "mime_type": "application/pdf", "size_bytes": 13241, "sha256": "...", "created_at": "2026-05-26T07:08:40Z" }
  ]
}
```

## `POST /api/ontologies`

업로드해 둔 자료 한 건으로 Phase 1 ontology generation 을 한 건 시작한다.
즉시 202 + `ontology_id` 반환 — 실제 LLM 콜은 백그라운드. 클라는 `ontology_id`
로 폴링해 `ready=true` 가 되면 그 id 를 `POST /api/plazas` 의 `ontology_id`
에 그대로 넣는다 — 사용자 PDF 가 실제 시뮬에 반영되는 3-step 정공 흐름.

요청 본문:

```json
{
  "document_id": "ab12cd34...",
  "requirement": "AI 규제 법안에 대한 시민 반응 시뮬레이션",
  "preset": "quick"
}
```

- `document_id`: 필수. `POST /api/documents` 의 응답. 없는 id 면 404.
- `requirement`: 필수. 1–500자. Phase 1 의 entity ranking / profile generation 에 그대로 전달되는 한 줄 문맥.
- `preset`: 선택. `"quick"` (기본, 100명) / `"standard"` (300명) / `"full"` (500명). agent 수가 늘어날수록 LLM 호출 시간/비용이 커진다.
- 알 수 없는 필드는 422 거절.
- 서버에 `ontology_store` 가 안 붙어 있으면 (`--fake` 모드) 라우터 자체가 등록되지 않아 404 응답.

응답 202:

```json
{
  "ontology_id": "ab12cd34...",
  "document_id": "ab12cd34...",
  "status": "pending",
  "preset": "quick",
  "requirement": "AI 규제 법안에 대한 시민 반응 시뮬레이션",
  "agent_count": null,
  "error": null,
  "ready": false,
  "created_at": "2026-05-26T07:08:40Z",
  "updated_at": "2026-05-26T07:08:40Z"
}
```

`status` 는 즉시 응답 시점에 `pending` 또는 `running` 일 수 있다.

## `GET /api/ontologies/{ontology_id}`

Phase 1 generation 한 건의 진행 상태 + 결과. 폴링 대상.

응답 200:

```json
{
  "ontology_id": "ab12cd34...",
  "document_id": "ab12cd34...",
  "status": "completed",
  "preset": "quick",
  "requirement": "AI 규제 법안에 대한 시민 반응 시뮬레이션",
  "agent_count": 100,
  "error": null,
  "ready": true,
  "created_at": "2026-05-26T07:08:40Z",
  "updated_at": "2026-05-26T07:10:12Z"
}
```

`status` 상태 머신:

```
pending → running → completed
                  ↘ failed
```

- `ready`: `status == "completed"` 의 boolean 별칭. 폴링 측이 한 줄로 분기할 수 있도록 노출 — plaza 의 `ready` 패턴과 같은 의도.
- `agent_count`: `completed` 일 때만 채워진다. preset 으로 정해진 목표치 그대로 (`quick=100` 등) 인 게 보통.
- `error`: `failed` 일 때만 non-null. `"<ExceptionType>: <message>"` 형식.
- 프로세스가 generation 도중에 죽으면 다음 부팅에 `pending` / `running` row 는 `failed` + `error="cancelled"` 로 강제 마킹 — `/status` 가 stuck 처럼 보이지 않게 한다.
- `404`: 존재하지 않는 `ontology_id`.

폴링 권장 주기는 **1.5 ~ 2 초**. Phase 1 generation 이 분 단위라 더 짧게 잡아도
의미 있는 갱신은 안 늘고 백엔드 부담만 커진다. `ready=true` 가 되면 즉시 다음
단계 (`POST /api/plazas` with `ontology_id`) 로 넘어가면 된다.

산출물은 `<data-dir>/ontologies/{ontology_id}/ontology_{a,b}_*.json` 두 파일.
경로 자체는 응답에 노출하지 않고, plaza 호출 시 `ontology_id` 만 넘기면
백엔드가 그 경로를 박아 시뮬에 연결한다.

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
- 업로드 문서 본문은 `--data-dir/documents/` 에 디스크 영속, 메타는 같은
  `plazas.db` 의 `documents` 테이블. ontology generation 산출은 `--data-dir/ontologies/{ontology_id}/`
  에 두 JSON 으로 저장 + 메타는 `ontologies` 테이블.
- 라운드 단위 progress 까지 영속 — `on_progress` 콜백마다 upsert 1회. 따라서
  재시작 시 `rounds_done` 은 마지막 commit 된 라운드 그대로.
- 재시작 시점에 마지막 commit 된 status 가 `pending` / `running` / `composing`
  인 row 는 도중에 죽은 것으로 보고 `failed` + `error="process restarted while
  <prev>"` 로 강제 마킹한다 — checkpoint 기반 자동 재개는 별도 작업. ontology
  도 같은 패턴 (`pending` / `running` → `failed` + `error="cancelled"`).
- 비 영속 (프로세스 lifetime 한정): asyncio Task, SSE subscriber 큐, `DataAggregator`
  결과 캐시 (events.jsonl 로 lazy 재집계).
