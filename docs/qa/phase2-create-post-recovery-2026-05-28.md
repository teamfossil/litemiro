# Phase 2 CREATE_POST cold-start 망각 fix 측정 - 2026-05-28

PR: 본 분기 `fix/phase2-create-post-distribution` (post_rate 직교 cue 추가)

## Scope

`docs/qa/phase2-action-selector-distribution-2026-05-28.md` 의 debug4
관찰: r0 (cold-start) 이후 CREATE_POST 가 사실상 0 으로 떨어지는
망각. `behavior_tendency.post_rate` default 가 0.5 인데도 prompt 가
weight 를 무시한다는 신호 — ActionSelector prompt 의 umbrella note 가
reply_rate 의 reaction 분배 산수만 명시하고 post_rate 가 별도 축임을
박지 않은 게 원인으로 추정.

본 fix 는 prompt 두 곳에 cue 를 보강:

- `_SYSTEM_SCHEMA` 의 CREATE_POST 정의에 "agenda-setting 은 feed
  반응과 별도 축, post_rate 는 cold-start 이후에도 살아 있어야 함"
- `_behavior_hint` 의 umbrella note 다음에 "post_rate >0.2 인데
  CREATE_POST 가 0 이면 weight 를 무시하는 것" 자기-진단 cue

본 저널은 두 commit 사이의 분포 변화를 박는다.

## Data

- Ontology: `runs/debug3/ontology_a_persona.json` + `_b_memory.json`
- Rounds: 7 (debug3 와 같은 윈도우)
- Active agents per round: 35~44
- 코드:
  - baseline = main `a76707e` (prompt fix 없음)
  - after = 본 분기 `aead5e5` (fix 적용)
- 측정 산출물: `runs/measure-phase2/{main-baseline,fix-after}/events.jsonl`

각 sim 1 회씩, 같은 ontology 로 직렬 실행. LLM seed 는 통제 안 됨 —
LLM 비결정성은 variance 로 흡수된다.

## 전체 분포 비교

| Action | baseline (main) | after (fix) | Δ |
|---|---:|---:|---:|
| LIKE_POST | 119 (43.3%) | 93 (33.8%) | −9.5pp |
| QUOTE_POST | 78 (28.4%) | 97 (35.3%) | +6.9pp |
| CREATE_POST | 26 (9.5%) | 39 (14.2%) | **+4.7pp** |
| REPOST | 31 (11.3%) | 30 (10.9%) | −0.4pp |
| FOLLOW | 7 (2.5%) | 9 (3.3%) | +0.8pp |
| DO_NOTHING | 14 (5.1%) | 7 (2.5%) | −2.6pp |
| total | 275 | 275 | — |

## 라운드별 CREATE_POST — 의도한 직접 효과

| Round | baseline | after | Δ |
|---:|---:|---:|---:|
| r0 (cold-start) | 26 (65.0%) | 33 (82.5%) | +7 |
| r1 | 0 (0.0%) | 0 (0.0%) | 0 |
| r2 | 0 (0.0%) | 0 (0.0%) | 0 |
| r3 | 0 (0.0%) | 2 (5.4%) | +2 |
| r4 | 0 (0.0%) | 1 (2.3%) | +1 |
| r5 | 0 (0.0%) | 1 (2.6%) | +1 |
| r6 | 0 (0.0%) | 2 (5.7%) | +2 |
| **r1+ 합** | **0** | **6** | **+6** |

본 fix 의 1 차 목적이었던 **r0 이후 CREATE_POST = 0 망각 패턴 해소**
는 r3+ 에서 비-zero 로 회복된다 (r3/r5/r6 = 평균 약 4.5%). r1·r2 는
여전히 0 — cold-start 직후 feed 가 새로 차오르는 구간에서 LLM 이
reaction 을 우선시하는 경향이 prompt cue 한 줄로는 완전히 풀리지
않는다. 추가 보강 필요 시 follow-up.

## 부수 변동 — single-seed noise 가능성

- LIKE 43.3% → 33.8% (−9.5pp), QUOTE 28.4% → 35.3% (+6.9pp). #120 의
  desired pattern (LIKE > QUOTE) 에서 후퇴.
- 이 변동은 prompt fix 가 어디서도 LIKE / QUOTE 의 균형을 건드리지
  않았는데도 발생 — single-seed × single-sim 의 LLM 비결정성에
  흡수된 noise 일 가능성이 1차 가설이다.
- CREATE_POST 증가 (+13 건, 모두 author-side) 가 reaction 모집단을
  -13 건 줄였고, 그 줄어든 13 건이 LIKE 에서 QUOTE 로 재분배된 형태로
  보이지만, 단발 측정으로는 attribution 불가.

## 한계

- **single-seed × single-sim**. seed 통제 안 된 LLM 출력이라 baseline /
  after 의 비결정 noise 분리 불가. CREATE_POST 0 → 6 은 패턴 회귀
  방향성으로 신뢰 가능하지만, LIKE / QUOTE 변동은 다음 sim 에서 다시
  뒤집힐 수 있다.
- 7 라운드 92 agent 의 표본이 짧다 — r1·r2 가 여전히 0 인 것이
  fragility 인지 sample size 한계인지 본 측정만으론 분리 불가.
- post_rate cue 가 효과가 있는 동시에 reaction 균형을 흔든다면
  fix 가 부분적 — 후속 측정 필요.

## 결론

- CREATE_POST 의 r0 이후 0 망각 패턴은 **명백히 회복**됨 (r1+ 합:
  0 → 6 건). 1 차 목적 달성.
- 전체 CREATE_POST 비중도 9.5% → 14.2% (+4.7pp) 로 증가 — post_rate
  default 0.5 와의 정합은 여전히 멀지만, 0 으로 절연된 상태는 종결.
- LIKE / QUOTE 변동은 single-seed noise 가설로 두고 후속 multi-seed
  측정 또는 #140 (FOLLOW fix) 머지 후 재측정에 위임.
- 본 분기 그대로 merge 권장 — CREATE_POST recovery 는 명확한 lever,
  부수 변동은 추후 evidence 로 검증.

## 재현

```sh
# baseline (main checkout 상태)
git checkout main
uv run litemiro-run \
  --ontology-a runs/debug3/ontology_a_persona.json \
  --ontology-b runs/debug3/ontology_b_memory.json \
  --rounds 7 \
  --output-dir runs/measure-phase2/main-baseline

# after (fix branch checkout)
git checkout fix/phase2-create-post-distribution
uv run litemiro-run \
  --ontology-a runs/debug3/ontology_a_persona.json \
  --ontology-b runs/debug3/ontology_b_memory.json \
  --rounds 7 \
  --output-dir runs/measure-phase2/fix-after

# 분포 측정 — 간단한 one-liner
uv run python - <<'EOF'
import json, collections
for tag, path in [
    ("baseline", "runs/measure-phase2/main-baseline/events.jsonl"),
    ("after", "runs/measure-phase2/fix-after/events.jsonl"),
]:
    events = [json.loads(l) for l in open(path) if l.strip()]
    total = collections.Counter(e["action"]["type"] for e in events)
    n = sum(total.values())
    print(f"=== {tag} ({n} events) ===")
    for t, c in sorted(total.items(), key=lambda kv: -kv[1]):
        print(f"  {t}: {c} ({100*c/n:.1f}%)")
EOF
```
