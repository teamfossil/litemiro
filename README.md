# litemiro

Mirofish — LLM-driven social media simulation engine. 학부 캡스톤 (W1~8).

## 현황

Phase 1 (듀얼 온톨로지 생성) quick preset 이 실 LLM (OpenRouter / qwen-plus) 으로
end-to-end 동작. 100 agent · ~220s · ~$0.03 / 1 회 기준. Phase 2 (시뮬레이션) /
Phase 3 (분석 + ReportComposer) 머지 완료. HTTP API (`litemiro-api`) + Vite/React
프론트 골격까지 step 1~5 합류 — preset 별 보고서 합성 + SSE 진행률 까지 동작.
plaza 메타데이터는 `--data-dir/plazas.db` (SQLite, WAL) 로 영속.

## 설치 / 실행

```bash
uv sync
uv run pytest -q
uv run ruff check .
uv run mypy
```

Phase 1 quick preset 실행:

```bash
export OPENROUTER_API_KEY=...
uv run litemiro-ontology \
  --preset quick \
  --input path/to/document.pdf \
  --out-dir /tmp/run \
  --seed 42
```

`--input` 은 PDF 또는 텍스트 1 개. preset 은 quick / standard / full.

산출: `/tmp/run/ontology_a_persona.json`, `/tmp/run/ontology_b_memory.json`.

JSONL 산출 검증:

```bash
uv run litemiro-validate \
  --schema src/litemiro/schemas/round_event.schema.json \
  --jsonl path/to/run.jsonl
```

## HTTP API

프론트 / 외부 도구가 시뮬레이션을 띄우고 진행률·보고서를 받기 위한 얇은 HTTP
표면. 전체 계약은 `docs/api/contract.md` 참고.

```bash
pip install -e ".[api]"
litemiro-api --host 127.0.0.1 --port 8765 --data-dir ./runs/api
```

- `--data-dir`: plaza 별 `events.jsonl` + `checkpoints/` 가 쌓일 루트. 이 디렉토리
  안의 `plazas.db` (SQLite, WAL) 에 plaza 메타 (status / progress / preset /
  markdown / error 등) 가 영속 — 프로세스 재시작 후에도 같은 `plaza_id` 로
  `/status` / `/report` / `/events` 가 디스크 산출물을 다시 바라본다. 라운드
  단위 progress 까지 row 에 박힌다.
- 재시작 시점에 마지막 commit 된 status 가 `pending` / `running` / `composing`
  인 plaza 는 도중에 죽은 것으로 보고 `failed` + `error="process restarted
  while <prev>"` 로 강제 마킹. checkpoint 기반 자동 재개는 별도 작업.
- `--fake`: LLM 키 없이 dummy runner 로 기동 — 프론트 라우팅/폴링 검증용.
- 실 모드는 `.env` 의 `OPENROUTER_API_KEY` 필요.

## 디렉토리

실제 존재하는 모듈만 표기. 비어 있는 의도 디렉토리는 owner 가 구현 시 채워짐.

```
src/litemiro/
  models.py            shared Pydantic v2 모델 (Action / Post / Agent / RoundEvent ...)
  interfaces.py        owner-boundary Protocol (LLMClient / SocialGraphLike ...)
  action/              B  ActionSelector + 3-step fallback
  feed/                B  FeedEngine (hot_score + topic inverted index)
  social/              B  SocialGraph (homophily augmentation 포함)
  prompts/             B  ActionSelector prompt templates
  topics/              B  TopicExtractor
  core/                A  RoundManager / AgentScheduler / ConcurrencyController / StateStore
  phase1/              Phase 1 pipeline (chunker → ontology → entity → ranker → profile → memory)
  phase3/              Phase 3 pipeline (DataAggregator → PatternAnalyzer → ReportComposer)
  api/                 FastAPI app + PlazaStore (SQLite 영속화) + SSE 라우트
  llm/                 LiteLLMClient (OpenRouter via litellm)
  embedding/           STEmbedder (sentence-transformers)
  schemas/             JSON Schema 3 종 (ontology_a / ontology_b / round_event)
  cli/                 litemiro-validate, litemiro-ontology, litemiro-api
frontend/              Vite + React + TS 프론트엔드 (plaza 생성 / 진행률 / 보고서)
tests/  unit/  e2e/    pytest, asyncio_mode=auto
```

## 결정성

같은 seed + mocked LLM → 동일 JSONL. 실 LLM 호출은 sampling 으로 인해 byte-level
동일성 보장 안 됨 — quick preset 의 fallback 비율 / mean(post_rate) 같은 누적
통계 수준에서만 비교 가능.

## RoundEvent

Phase 2 → Phase 3 JSONL 계약. 한 줄에 한 이벤트. 권위 스키마:
`src/litemiro/schemas/round_event.schema.json`. `RoundEvent.to_jsonl()` 가 표준
직렬화 (sorted keys, ensure_ascii=False, exclude_none).
