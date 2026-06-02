# litemiro

LLM 기반 소셜 미디어 여론 형성 시뮬레이터. 사회 이슈 문서 하나를 입력받아 100명의
가상 인격을 생성하고, 최대 50 라운드의 광장 토론을 시뮬레이션한 뒤 정보 확산·집단
양극화·herd 효과를 정량 분석한다.

학부 캡스톤 프로젝트 (팀 화석, 2026).

---

## 연구 개요

OASIS (arXiv:2411.11581) 의 대규모 LLM 시뮬레이션 구조를 참고해 소규모(100 agents,
50 rounds) 재현 가능한 파이프라인을 구축한다. 핵심 가설: LLM 에이전트도 실제 소셜
미디어와 유사한 집단 역학(echo chamber, herd, viral cascade)을 보이는가.

신념 변동 메커니즘으로 Deffuant bounded confidence 모델(`BeliefUpdater`, ε=0.3,
μ=0.05)을 도입해 정적 ideology 의 한계를 보완한다.

---

## 파이프라인

```
문서 (PDF/TXT)
    │
    ▼
Phase 1 — 듀얼 온톨로지 생성
  litemiro-ontology
  └─ ontology_a_persona.json   (에이전트 100명: 이름·직업·성향·ideology)
  └─ ontology_b_memory.json    (토픽 어휘·에이전트 초기 기억)
    │
    ▼
Phase 2 — 시뮬레이션
  litemiro-run
  └─ events.jsonl              (RoundEvent JSONL, 라운드×에이전트)
  └─ belief_trajectory.jsonl   (라운드별 ideology 스냅샷)
  └─ checkpoints/              (재시작용 체크포인트)
    │
    ▼
Phase 3 — 분석 및 보고서
  litemiro-report
  └─ report-{timestamp}.md     (Markdown 보고서)
```

---

## 설치

```bash
git clone https://github.com/teamfossil/litemiro
cd litemiro
uv sync
```

`.env` 파일에 API 키 설정:

```
OPENROUTER_API_KEY=sk-or-...
```

---

## 사용법

### Phase 1 — 온톨로지 생성

```bash
uv run litemiro-ontology \
  --preset quick \
  --input path/to/document.txt \
  --out-dir runs/my-run \
  --seed 42
```

`--input` 은 PDF 또는 텍스트. preset 은 `quick` / `standard` / `full`.

산출: `runs/my-run/ontology_a_persona.json`, `runs/my-run/ontology_b_memory.json`

### Phase 2 — 시뮬레이션

```bash
uv run litemiro-run \
  --ontology-a runs/my-run/ontology_a_persona.json \
  --ontology-b runs/my-run/ontology_b_memory.json \
  --rounds 50 \
  --token-budget 12000000 \
  --output-dir runs/my-run/sim
```

산출: `events.jsonl`, `belief_trajectory.jsonl`, `checkpoints/`

### Phase 3 — 분석

```bash
uv run litemiro-report \
  --events runs/my-run/sim/events.jsonl \
  --ontology-a runs/my-run/ontology_a_persona.json \
  --preset quick
```

`belief_trajectory.jsonl` 이 `--events` 와 같은 디렉토리에 있으면 자동탐색해
동적 ideology 로 양극화 메트릭을 계산한다.

### HTTP API (선택)

```bash
uv run litemiro-api --host 127.0.0.1 --port 8765 --data-dir ./runs/api
```

Phase 1~3 파이프라인을 REST + SSE 로 노출한다. 전체 계약은
`docs/api/contract.md` 참고.

---

## 산출물

| 파일 | 단계 | 설명 |
|------|------|------|
| `ontology_a_persona.json` | Phase 1 | 에이전트 페르소나 (ideology, 직업, 관심사 등) |
| `ontology_b_memory.json` | Phase 1 | 토픽 어휘 + 에이전트 초기 기억 |
| `events.jsonl` | Phase 2 | 라운드별 액션 로그 (RoundEvent JSONL) |
| `belief_trajectory.jsonl` | Phase 2 | 라운드별 전 에이전트 ideology 스냅샷 |
| `report-{ts}.md` | Phase 3 | LLM 분석 + 현상 메트릭 보고서 |

`events.jsonl` 스키마: `src/litemiro/schemas/round_event.schema.json`

검증:

```bash
uv run litemiro-validate \
  --schema src/litemiro/schemas/round_event.schema.json \
  --jsonl path/to/events.jsonl
```

---

## 주요 메트릭

Phase 3 `DataAggregator` 가 LLM 없이 결정적으로 계산하는 현상 지표:

- **cascade** — REPOST/QUOTE 체인 최대 depth / breadth / scale (정보 확산)
- **ideology_assortativity** — FOLLOW 네트워크의 ideology Pearson 상관 (집단 양극화)
- **follow_ideology_gap** — FOLLOW 엣지 평균 |Δideology| (호모필리)
- **popularity_gini** — 피팔로우 수 분포 지니 계수 (herd 효과)
- **ideology_std_final** — 최종 라운드 ideology 표준편차 (수렴/발산)
- **ideology_drift_mean** — 에이전트별 |final − initial| 평균 (신념 변동 크기)

OASIS 비교 기준 및 베이스라인: `docs/qa/metrics.md`

---

## 재현성

같은 seed + mocked LLM → 동일 JSONL. 실 LLM 은 sampling 으로 인해 byte-level
동일성 미보장 — fallback 비율 / 누적 통계 수준에서만 비교 가능.

seed 별 재현 확인 완료: seed 7, 42, 99 (AI 규제 토픽, 50 rounds).

---

## 코드 구조

```
src/litemiro/
  models.py        shared Pydantic v2 모델 (Action / Post / Agent / RoundEvent ...)
  interfaces.py    owner-boundary Protocol (LLMClient / SocialGraphLike ...)
  core/            RoundManager · AgentScheduler · ConcurrencyController · StateStore · BeliefUpdater
  action/          ActionSelector (3-step fallback)
  feed/            FeedEngine (hot_score + topic inverted index)
  social/          SocialGraph (homophily augmentation)
  phase1/          온톨로지 파이프라인 (chunker → entity → ranker → profile → memory)
  phase3/          분석 파이프라인 (DataAggregator → PatternAnalyzer → ReportComposer)
  api/             FastAPI + PlazaStore (SQLite WAL) + SSE
  llm/             LiteLLMClient (OpenRouter via litellm)
  embedding/       STEmbedder (sentence-transformers)
  schemas/         JSON Schema 3종
  cli/             litemiro-validate · litemiro-ontology · litemiro-run · litemiro-report · litemiro-api
frontend/          Vite + React + TS
tests/             unit/ · e2e/  (pytest, asyncio_mode=auto)
```

---

## 개발

```bash
uv run pytest -q          # 테스트
uv run ruff check .       # lint
uv run ruff format .      # format
uv run mypy               # type check
```

PR 전 위 4개 통과 필수. CI 게이트: Ruff + Mypy / Pytest (3.11, 3.12) / JSON Schema.
