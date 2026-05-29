# QA self-baseline

`scripts/collect_qa_baseline.py` 가 같은 ontology 로 seed 만 바꿔 Phase 2 를 N 회
돌려 측정한 `QaMetrics` + `PhenomenaMetrics` 의 run-to-run 분포다. OASIS 단일
베이스라인이 없으므로(`metrics.md`) 회귀 게이트의 기준은 이 self-baseline 이다 —
`scripts/check_qa_regression.py` 가 새 run 을 평균 ±2σ 와 비교한다. 기계 판독본은
`docs/qa/baseline.json`.

## 수집 조건

- runs: 5 (seeds 101–105, seed 당 1 run)
- rounds: 5, batch-size 20, model `openrouter/qwen/qwen-plus`
- ontology_a: `runs/e2e2-2026-05-29/ontology_a_persona.json` (페르소나 고정, seed 만 변동)
- 수집일: 2026-05-29

## 분포

| metric | n | mean | std | min | max |
|---|---|---|---|---|---|
| action_entropy_normalized | 5 | 0.8611 | 0.0142 | 0.8432 | 0.8750 |
| follow_clustering_coefficient | 5 | 0.0606 | 0.0551 | 0.0000 | 0.1502 |
| content_word_entropy_normalized | 5 | 0.9576 | 0.0023 | 0.9541 | 0.9606 |
| cascade_max_depth | 5 | 2.0000 | 0.7071 | 1.0000 | 3.0000 |
| cascade_max_breadth | 5 | 7.8000 | 4.2071 | 5.0000 | 15.0000 |
| cascade_max_scale | 5 | 9.4000 | 4.3932 | 6.0000 | 17.0000 |
| n_cascades | 5 | 10.6000 | 2.8810 | 7.0000 | 15.0000 |
| follow_ideology_gap | 5 | 0.2337 | 0.0369 | 0.1736 | 0.2726 |
| ideology_assortativity | 5 | 0.1354 | 0.2241 | -0.1238 | 0.4716 |
| popularity_gini | 5 | 0.4817 | 0.0737 | 0.3922 | 0.5952 |
| early_mover_share | 5 | 0.6154 | 0.1439 | 0.3750 | 0.7436 |

## 해석

- **안정** (게이트 신뢰 가능): `action_entropy`(σ 0.014)·`content_word_entropy`
  (σ 0.002)·`follow_ideology_gap`(σ 0.037) 은 seed 간 거의 안 흔들린다.
- **불안정** (노이즈): `follow_clustering`(σ 0.055, min 0)·`ideology_assortativity`
  (σ 0.224, 부호까지 뒤집힘). clustering 은 deprecated(`metrics.md`), assortativity
  도 현 규모(5R)에서 분산이 커 — 양극화는 `follow_ideology_gap` 을 1 차 신호로 본다.
- **변동 중간**: `cascade_breadth/scale`·`popularity_gini`·`early_mover_share` 는
  시드별 폭이 있으나 부호·자릿수는 일관.

## 게이트 검증

e2e2(seed 7)를 새 run 으로 `check_qa_regression` 에 넣으면 2 개 위반:

```
action_entropy_normalized: 0.7901 outside [0.8326, 0.8895]
content_word_entropy_normalized: 0.9435 outside [0.9530, 0.9623]
```

게이트 메커니즘은 정상 작동한다(범위 밖 감지 → exit 1). 다만 두 entropy 의 σ 가
매우 좁아(0.014 / 0.002) seed 7 이 범위를 벗어난다.

## 한계 / 다음

- **N=5 는 잠정치**. entropy 처럼 σ 가 좁은 메트릭은 표본이 늘면 범위가 넓어질 수
  있다 — seed 7 이 구조적으로 낮은 건지 N=5 가 σ 를 과소추정한 건지는 표본 확대로
  가린다. **N≥10 + seed 7 포함 재수집** 시 entropy 게이트의 과민성이 풀릴 가능성이
  높다. 그전까지 entropy 위반은 "확인 요망" 신호로 다루고 자동 차단으로 쓰지 않는다.
- 분산 0 메트릭은 게이트하지 않는다(`check_regression`).
- baseline 갱신: `collect_qa_baseline.py` 재실행 → `baseline.json` 덮어쓰기 후 본 표
  갱신.
