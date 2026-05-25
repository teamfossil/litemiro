# litemiro

Mirofish — LLM-driven social media simulation engine. 학부 캡스톤 (W1~8).

## 현황

Phase 1 (듀얼 온톨로지 생성) quick preset 이 실 LLM (OpenRouter / qwen-plus) 으로
end-to-end 동작. 100 agent · ~220s · ~$0.03 / 1 회 기준. Phase 2 단독 컴포넌트는
머지 완료, Phase 1↔2 통합 진입점 (`OntologyLoader` / `RunBootstrap`) 작업 중.
Phase 3 (분석/보고서) 미착수.

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
  llm/                 LiteLLMClient (OpenRouter via litellm)
  embedding/           STEmbedder (sentence-transformers)
  schemas/             JSON Schema 3 종 (ontology_a / ontology_b / round_event)
  cli/                 litemiro-validate, litemiro-ontology
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
