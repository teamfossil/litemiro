# Phase 1 Model Benchmark - 2026-05-26

Issue: #98

## Scope

Same input document and requirement were used for every run:

- input: `tests/data/sample_document.txt`
- requirement: `한국 AI 규제 정책을 둘러싼 이해관계자 간 소셜 미디어 토론 시뮬레이션`
- seed: `42`
- provider: OpenRouter through LiteLLM

The benchmark measures Phase 1 ontology generation only. Costs are provider-reported
USD values from the LiteLLM response usage/cost path.

## Quick Preset Results

| run | model / option | elapsed | tokens | cost | fallback | sensitive empty | following empty | ideology mean |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| qwen baseline | `openrouter/qwen/qwen-plus` | 277.56s | 56,079 | $0.033289 | 20/100 | 20 | 2 | 0.3469 |
| qwen JSON mode | `openrouter/qwen/qwen-plus` + `response_format=json_object` | 226.20s | 57,297 | $0.034126 | 0/100 | 0 | 7 | 0.4070 |
| claude haiku | `openrouter/anthropic/claude-3-haiku` | 65.24s | 47,458 | $0.037067 | 0/100 | 80 | 4 | 0.3271 |
| gpt-4o-mini | `openrouter/openai/gpt-4o-mini` | 201.34s | 34,687 | $0.013948 | 49/100 | 49 | 4 | 0.4476 |
| deepseek-chat | `openrouter/deepseek/deepseek-chat` | 308.84s | 34,684 | $0.023006 | 0/100 | 20 | 0 | 0.4017 |

OpenRouter accepted `response_format={"type":"json_object"}` for qwen-plus.
The current profile prompt still asks for a JSON array, and the recorded
profile responses parsed as `json_list`. Treat this as "option accepted and
improved one run", not as strict schema enforcement.

## Preset Cost Checks

These runs used `openrouter/qwen/qwen-plus` with `response_format=json_object`.

| preset | agents | elapsed | tokens | cost | fallback | sensitive empty | following empty | ideology mean |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| quick | 100 | 226.20s | 57,297 | $0.034126 | 0/100 | 0 | 7 | 0.4070 |
| standard | 300 | 470.51s | 141,167 | $0.088237 | 19/300 | 19 | 2 | 0.3084 |
| full | 500 | 698.61s | 224,404 | $0.141425 | 2/500 | 2 | 7 | 0.3051 |

## Seed Reproducibility

Two quick runs used identical `seed=42`, model, input, and
`response_format=json_object`.

| run | elapsed | tokens | cost | fallback | normalized OntologyA hash |
|---|---:|---:|---:|---:|---|
| first | 226.20s | 57,297 | $0.034126 | 0/100 | `fbed9894e60d1368b071a6a56e780d1272241594d80a32fbc7a9d3a37eb1ddfc` |
| rerun | 210.36s | 55,451 | $0.033226 | 20/100 | `af7bac9674cd3903b876fc5da16345c74322fdd1b5b596105e896a8c0c70bb4a` |

Result: real LLM runs are not byte-level reproducible. They are also not
stable after normalizing `generated_at`; the LLM changes extracted entities,
profile coverage, and fallback counts.

## Fallback Patterns

- qwen baseline fallback: 10 missing agents from profile responses, 10 profile
  validation failures.
- gpt-4o-mini fallback: 10 missing agents, 39 profile validation failures.
- qwen JSON-mode rerun fallback: 20 missing agents from profile responses.
- qwen standard JSON-mode fallback: 19 missing agents.
- qwen full JSON-mode fallback: 2 missing agents.

The dominant validation failure was `BehaviorTendency` range validation. This
means the prompt should explicitly constrain `post_rate`, `reply_rate`,
`repost_rate`, and `controversy_affinity` to numeric values in `[0.0, 1.0]`,
and the parser should clamp or reject with diagnostics instead of silently
falling back.

## Decision

Do not switch production to `gpt-4o-mini` despite its lower cost; the 49%
fallback rate is too high. `claude-3-haiku` is fast and parse-stable, but the
80 empty `sensitive_topics` outputs make it weak for persona richness.

`qwen-plus` with JSON mode is the best immediate production candidate, but it is
not sufficiently stable by itself. Before treating 0% fallback as guaranteed,
add a structured profile response shape, explicit `BehaviorTendency` range
constraints, and missing-agent retry/fill policy.

## Local Artifacts

Raw responses, full summaries, and generated ontology files were written under:

`./.codex-worktrees/phase1_model_benchmark_20260526/`

Those artifacts are intentionally not committed because they are generated
benchmark outputs.
