# QA self-baseline

`scripts/collect_qa_baseline.py` 가 같은 ontology 로 seed 만 바꿔 Phase 2 를 N 회
돌려 측정한 `QaMetrics` + `PhenomenaMetrics` 의 run-to-run 분포다. OASIS 단일
베이스라인이 없으므로(`metrics.md`) 회귀 게이트의 기준은 이 self-baseline 이다 —
`scripts/check_qa_regression.py` 가 새 run 을 평균 ±2σ 와 비교한다. 기계 판독본은
`docs/qa/baseline.json`.

## 수집 조건

- runs: 11 (seeds 7, 101–110)
- rounds: 5, batch-size 20, model `openrouter/qwen/qwen-plus`
- ontology_a: `runs/e2e2-2026-05-29/ontology_a_persona.json` (페르소나 고정, seed 만 변동)
- 수집일: 2026-05-29

## 분포 (N=11)

| metric | mean | std | min | max |
|---|---|---|---|---|
| action_entropy_normalized | 0.7918 | 0.0347 | 0.749 | 0.849 |
| content_word_entropy_normalized | 0.9443 | 0.0026 | 0.938 | 0.947 |
| cascade_max_depth | 1.3636 | 0.6742 | 1.000 | 3.000 |
| cascade_max_breadth | 7.8182 | 3.4588 | 3.000 | 14.000 |
| cascade_max_scale | 8.6364 | 3.6131 | 4.000 | 16.000 |
| n_cascades | 7.0909 | 2.1192 | 3.000 | 10.000 |
| follow_ideology_gap | 0.2060 | 0.0198 | 0.160 | 0.229 |
| ideology_assortativity | 0.0153 | 0.1360 | -0.152 | 0.237 |
| popularity_gini | 0.4393 | 0.0373 | 0.391 | 0.486 |
| early_mover_share | 0.5083 | 0.1106 | 0.360 | 0.737 |

## N=5 → N=11 (대표성 보완)

초기 baseline 은 seed 101–105 (N=5) 였는데, 독립 시드(e2e2=7, fullcheck=7777)가
entropy 등에서 ±2σ 밖으로 일관되게 떨어져 표본 편향이 의심됐다. seed 7·106–110 을
넣어 N=11 로 늘린 결과:

- **`action_entropy` 평균 0.861→0.792, σ 0.014→0.035** — seed 101–105 가 우연히
  entropy 높은 클러스터였음이 확인됐다. N=11 평균이 독립 시드(0.79)와 일치하고 σ 가
  시드 변동을 담는다.
- `content_word_entropy`·`popularity_gini`·`follow_ideology_gap` 도 평균이 소폭
  내려가며 분포가 현실화.

## 해석

- **안정** (게이트 신뢰): `content_word_entropy`(σ 0.003)·`follow_ideology_gap`
  (σ 0.020)·`popularity_gini`(σ 0.037)·`action_entropy`(σ 0.035, N=11 로 안정화).
- **불안정** (단독 차단 금지): `ideology_assortativity`(σ 0.136, 부호 뒤집힘)·
  `early_mover_share`(σ 0.111).
- **게이트 제외**: `follow_clustering_coefficient` 는 현 규모에서 신호가 없어
  (5R/15R 0.00~0.09, metrics.md deprecated) 회귀 게이트(`METRIC_NAMES`)에서 뺐다 —
  herd 는 `popularity_gini` 로 추적. 모델·계산·보고서 인용은 스키마 안정 위해 유지.

## 게이트 검증 (N=11)

| run | ontology | 위반 |
|---|---|---|
| e2e2 (seed 7) | **같은** ontology, baseline 에 포함 | `early_mover_share` 1 개(0.75 vs max 0.74, 경계 직상) |
| fullcheck (seed 7777) | **다른** ontology (fresh Phase 1) | `follow_ideology_gap`·`popularity_gini` 2 개 |

N=5 에선 두 run 모두 entropy 가 밖이었으나 N=11 에선 entropy 가 들어왔다. 남은
위반은 (a) e2e2 early_mover 경계 직상(시드 변동 끝자락), (b) fullcheck 는 **다른
페르소나 집단**이라 양극화·herd 구조가 구조적으로 다른 것이다.

## 운용 범위

- **회귀 게이트는 "같은 ontology + seed 변동" 회귀 감지용이다.** 코드 변경 후 같은
  ontology 로 재실행한 run 이 ±2σ 밖이면 회귀를 의심한다.
- **다른 ontology**(fresh Phase 1 산출)는 페르소나 분포가 달라 메트릭이 구조적으로
  다르므로 이 baseline 으로 게이트하지 않는다 — fullcheck 의 2 위반이 그 예다.
- 분산이 큰 메트릭(`ideology_assortativity`·`early_mover_share`)은 단독으로 차단
  근거로 쓰지 않고 "확인 요망" 신호로 본다.

## 한계 / 다음

- N=11 로 entropy 과민은 해소됐다. 분산 큰 메트릭은 **라운드 수↑**(현 5R)가 σ 를
  줄일 가능성이 있다 — 장기 시뮬 baseline 은 별도 수집 후보.
- baseline 갱신: `collect_qa_baseline.py` 재실행 → `baseline.json` 덮어쓰기 후 본 표 갱신.
