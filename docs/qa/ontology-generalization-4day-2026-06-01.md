# 다른 ontology 일반화 검증 — 주 4일제 100명 × 50R (2026-06-01)

AI 규제 topic(seed 7 / seed 99)에서 얻은 현상이 같은 ontology의 seed 변동에서는 재현됐다.
이 측정은 **아예 다른 document/topic(주 4일제 도입 논쟁)**으로 Phase 1부터 새 ontology를 생성해
50R 돌림으로써 "다른 페르소나 분포에서도 같은 여론 동학이 나타나는가"를 직접 검증한다.

## 셋업

- **document**: `tests/data/four_day_week_document.txt` (주 4일제 도입 논쟁, 이해관계자 분석)
- **simulation_requirement**: "주 4일제 도입을 둘러싼 노동자·경영계·정부·시민사회 등 다양한 행위자의 소셜미디어 여론 형성 시뮬레이션"
- **Phase 1**: `litemiro-ontology --preset quick --seed 42` → 100명 생성 (fallback 10%, 검증 통과)
- **Phase 2**: rounds 50, `--token-budget 12_000_000`, `openrouter/qwen/qwen-plus`
- 완주: `early_exit=False`, `rounds_run=50`, tokens 9.54M, 1896 events

> 첫 시도(`runs/longrun-4day-2026-06-01/`)는 기본 token-budget 3M으로 R16에서 early_exit.
> token-budget 12M으로 재실행해 완주.

## 누적 현상 추세

| 구간 | agents | events | ideology_assortativity | popularity_gini | early_mover_share | n_cascades | cascade_max_scale |
|---|---|---|---|---|---|---|---|
| R0-9  | 96 | 399 | **+0.163** | 0.412 | 0.176 | 16 | 20 |
| R0-19 | 97 | 774 | **+0.109** | 0.415 | 0.152 | 17 | 25 |
| R0-29 | 98 | 1138 | **+0.041** | 0.391 | 0.136 | 19 | 32 |
| R0-39 | 99 | 1502 | **+0.019** | 0.410 | 0.138 | 21 | 33 |
| R0-49 | 99 | 1896 | **+0.000** | **0.370** | 0.163 | 21 | **34** |

(`cascade_max_depth` 전 구간 2 고정 — 생략.)

## AI 규제 50R 비교

| 메트릭 | AI 규제 seed7 (50R) | AI 규제 seed99 (50R) | 주 4일제 (50R) |
|---|---|---|---|
| assortativity 종점 | -0.113 | -0.180 | **+0.000** |
| assortativity 방향 | 음수 수렴 (disassortative) | 음수 수렴 (disassortative) | **양수 → 0 (neutralization)** |
| popularity_gini 종점 | 0.462 | 0.416 | **0.370** |
| popularity_gini 기저 | 0.6 (높음) → 감소 | 유사 | 0.41 (낮음) → 안정 |
| early_mover_share 종점 | 0.306 | 0.374 | 0.163 |

## 발견

### 1. 양극화 비수렴 — AI 규제 topic 특이적, 주 4일제에서 재현 안 됨

AI 규제 seed7/99는 `ideology_assortativity`가 음수(-0.11~-0.18)로 수렴해 "교차팔로우
우세(disassortative)" 패턴을 보였다. 주 4일제에서는 **초기 +0.163(homophily)에서 시작해
+0.000으로 중립 수렴** — 음수로 꺾이지 않는다.

R0-9에서 R0-49까지 지속적으로 양수에서 0으로 단조 감소하는 추세가 안정적이라
"50R 더 돌리면 disassortative로 전환될 것"으로 해석하기 어렵다. 여론 동학 자체가
다르다.

### 2. herd 집중 — 낮은 기저에서 안정, 방향 공통

AI 규제는 높은 기저(gini 0.6)에서 시작해 0.46으로 단조 감소했다. 주 4일제는
처음부터 0.41 수준이며 50R 후 0.37로 완만히 낮아진다. **기저 수준과 강도는 다르나
"중반 피크 후 완화" 방향은 유사하다.** 완전 재현은 아니지만 부분 공통.

### 3. 확산 — 넓되 얕음, 공통

`cascade_max_depth` 2 고정은 AI 규제와 동일. 넓고 얕은 확산 패턴은 topic과 무관하게
유지된다.

## 해석과 한계

- **핵심 결론**: AI 규제 topic의 가장 견고한 발견인 "disassortative 양극화 비수렴"은
  다른 topic(주 4일제)에서 재현되지 않는다. **측정된 메트릭이 ontology(초기 조건)에
  강하게 의존한다** — belief update 메커니즘 부재(#165)와 함께 해석되어야 한다:
  신념이 라운드별로 안 변하는 상태에서 topic만 바꿔도 동학이 달라진다면,
  측정값이 emergent dynamics보다 초기 페르소나 분포를 정적으로 sampling하고 있을
  가능성을 배제하기 어렵다.
- **게이트 효과 공통**: ActionSelector behavior_tendency 게이트(#140)가 두 topic 모두에
  적용됐으므로, 결과 차이는 게이트가 아닌 페르소나 이념 분포·관계 구조에서 기인할
  가능성이 높다.
- **baseline 노이즈 띠**: self-baseline(5R, N=11)의 `ideology_assortativity` mean=0.015,
  σ=0.136, ±2σ 범위 [-0.26, +0.29]. AI 규제 50R 종점(-0.11~-0.18)과 주 4일제 50R 종점(≈0)
  모두 개별 값으론 ±2σ 안에 있다. 차이는 종점보다 **궤적**: AI 규제는 R0-19부터 50R 내내
  baseline mean 아래(-0.11~-0.18)로 안정적으로 드리프트하는 반면, 주 4일제는 초기 +0.16에서
  시작해 mean 근방(≈0)으로 수렴한다 — 방향 자체가 다르다.
- **단일 ontology 비교**: 주 4일제는 seed 42 한 번만 측정. 같은 topic의 seed 변동
  재현 검증은 미수행.
- **OASIS 직접 비교 아님**: 정성 대비만 가능.

## 재현

```bash
# Phase 1 — 주 4일제 ontology 생성
litemiro-ontology \
  --input tests/data/four_day_week_document.txt \
  --requirement "주 4일제 도입을 둘러싼 노동자·경영계·정부·시민사회 등 다양한 행위자의 소셜미디어 여론 형성 시뮬레이션" \
  --preset quick --seed 42 \
  --output-dir runs/ontology-4day-2026-06-01

# Phase 2 — 50R 시뮬
litemiro-run \
  --ontology-a runs/ontology-4day-2026-06-01/ontology_a_persona.json \
  --ontology-b runs/ontology-4day-2026-06-01/ontology_b_memory.json \
  --rounds 50 --output-dir runs/longrun-4day-50R-2026-06-01 \
  --token-budget 12000000
```
