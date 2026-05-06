# litemiro

Mirofish — LLM-driven social media simulation engine. Phase 2 (asyncio simulation) + Phase 3 (analysis & report).

## Status
- W1 in progress: scaffolding, JSONL contract, shared models.
- Owner: 김태우 (B — feed/action: `ActionSelector`, `FeedEngine`, `SocialGraph`, prompt design).

## Layout
```
src/litemiro/
  action/      # B  ActionSelector + LLM integration
  feed/        # B  FeedEngine (hot_score + topic_index)
  social/      # B  SocialGraph
  prompts/     # B  prompt templates
  core/        # A  RoundManager · AgentScheduler · ConcurrencyController · StateStore
  ontology/    # C  OntologyLoader
  budget/      # C  TokenBudgetManager
  eventlog/    # C  EventLogger
  schemas/     #    JSON Schema (RoundEvent — Phase 2 → Phase 3 contract)
  models.py    #    shared Pydantic v2 models
  interfaces.py#    Protocols across owners
  cli/         #    `litemiro-validate` schema validator
tests/
  unit/  integration/  e2e/
scripts/
```

## Install
```bash
pip install -e ".[dev,embedding]"
```

## Develop
```bash
ruff check .
ruff format --check .
mypy
pytest -q
```

## RoundEvent contract
JSONL — one event per line. Authoritative schema: `src/litemiro/schemas/round_event.schema.json`.

Validate any JSONL output:
```bash
litemiro-validate --schema src/litemiro/schemas/round_event.schema.json --jsonl path/to/run.jsonl
```

## Determinism
Same seed + mocked LLM → identical JSONL.
Checkpoints (managed by `core.StateStore`) include the per-agent RNG state.

## Owners
- A — 권현재: engine/state (`core/`).
- B — 김태우: feed/action (`action/`, `feed/`, `social/`, `prompts/`).
- C — 배강민: integration → Phase 3 (`ontology/`, `budget/`, `eventlog/`).
