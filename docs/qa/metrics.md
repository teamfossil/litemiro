# QA 메트릭 — OASIS 등가성 회귀 게이트

Phase 3 `DataAggregator` 가 LLM 호출 없이 산출하는 결정적 수치 3 종.
같은 JSONL 입력은 항상 같은 값을 돌려준다. 보고서 (`ReportComposer`) 가
본 메트릭을 그대로 인용해 run-to-run 회귀를 추적한다.

근거: Issue #59, Phase 3 메모리 노트, OASIS 논문 (arXiv:2411.11581).

## 메트릭

### `action_entropy_normalized`

`ActionType` 6 종에 대한 Shannon entropy 를 `log2(K)` 로 정규화한 값.
`K = len(ActionType) = 6`.

* 값 0 → 한 액션만 발생 (예: 모든 에이전트가 `DO_NOTHING`)
* 값 1 → 6 종이 균일 분포
* 낮으면 행동 다양성 결여 → 시뮬레이션이 한쪽으로 쏠렸다는 신호

부동소수 오차로 1.0 을 미세하게 넘어가는 경우는 `[0, 1]` 로 클램프해 Pydantic
`Field(le=1.0)` 검증을 통과시킨다.

### `follow_clustering_coefficient`

`FOLLOW` 이벤트로 재구성한 무방향 그래프의 평균 local clustering coefficient.

* self-loop 와 중복 엣지는 set 으로 정규화해 무시
* 노드 수 < 3 이면 정의되지 않아 0.0
* degree < 2 인 노드는 분모가 0 이라 0.0 으로 카운트

값 1 = 모든 이웃 쌍이 서로 연결 (완전 클러스터), 값 0 = 별 그래프나 트리 형태.
클러스터링이 안 보이면 echo chamber 없는 평탄 네트워크라는 의미.

> **[Deprecated — 현 규모에서 신호 없음]** 5R/15R 실측에서 이 값이 0.00~0.09
> (15R 은 전부 0.000) — 시뮬 네트워크가 희소해 삼각 폐쇄가 거의 안 생긴다.
> 회귀 게이트로는 무의미하므로 herd(인기 집중)는 `PhenomenaMetrics.popularity_gini`
> 로 대체 추적한다. 라운드 수·팔로우 밀도가 크게 늘기 전까지는 이 메트릭을
> 게이트 기준으로 쓰지 않는다 (스키마 안정을 위해 계산 자체는 유지).

### `content_word_entropy_normalized`

`CREATE_POST` / `QUOTE_POST` 의 `content` 를 공백으로 토크나이즈한 word
frequency 의 Shannon / `log2(|vocab|)`.

* 빈 토큰 또는 vocab ≤ 1 → 0.0
* 한국어 형태소 분석 없이 단순 어휘 다양성만 근사

진짜 토픽 entropy 는 `RoundEvent` 스키마에 `topics: list[str]` 필드 추가 후
별도 PR 에서 정확화 — 본 메트릭은 그때까지의 프록시.

## 현상 메트릭 (`PhenomenaMetrics`)

OASIS 가 재현 대상으로 삼는 3 현상(정보 확산·집단 양극화·herd)을 우리 데이터로
측정한 결정적 프록시. OASIS 는 LLM 평가·실세계 RMSE 를 쓰지만 우리는 재현성을 위해
전부 LLM 없는 계산이다 — 같은 입력은 같은 값. `DataAggregator.aggregate(jsonl,
ontology_path)` 가 산출하며, `aggregate(jsonl)` 단일 인자(하위호환)면 양극화는 None.

### 정보 확산 — `cascade_*`

REPOST/QUOTE 의 `target_post_id` 체인으로 전파 트리를 재구성한다. post_id 가
`{agent}_r{round:04d}`(round_manager 보장, 에이전트당 라운드당 1 액션)라
events.jsonl 만으로 부모-자식 추적이 가능하다.

* `cascade_max_depth` — 재게시의 재게시 최대 깊이
* `cascade_max_breadth` — 한 포스트의 최대 직접 재게시 수
* `cascade_max_scale` — 한 캐스케이드의 고유 참여 에이전트 수
* `n_cascades` — 재게시가 1 건 이상 달린 원본 수 (표본 크기)

REPOST 가 과소하면(prompt 가 중간 비율을 못 맞추는 binary 한계로 5~6% 수용) depth 가
얕다. QUOTE 를 포함해 신호는 있으나 `n_cascades` 를 함께 봐 표본 크기를 가늠한다.

### 집단 양극화 — `follow_ideology_gap`, `ideology_assortativity`

ontology_a 의 `ideology`(float[0,1])를 FOLLOW 엣지와 결합. OASIS 는 LLM 으로 의견
극단성을 평가하지만, 우리는 ideology 스칼라가 있어 결정적으로 잡는다.

* `follow_ideology_gap` — FOLLOW 엣지의 평균 |Δideology|([0,1]). 낮을수록 비슷한
  성향끼리 follow(호모필리 = 양극화 신호)
* `ideology_assortativity` — follower/followee ideology Pearson 상관([-1,1]).
  양수 = 동질 선호

ontology 미제공 시 둘 다 `None`(하위호환). FOLLOW 엣지 < 2 또는 한쪽 분산 0 이면
assortativity 는 None.

### herd 효과 — `popularity_gini`, `early_mover_share`

OASIS 의 up/down treatment 실험 대신 "인기 쏠림"으로 herd 발현을 측정한다.

* `popularity_gini` — 피팔로우 수 분포의 지니([0,1]).
  `network_metrics.followee_concentration.gini` 와 같은 분포라 동일 값
* `early_mover_share` — 전반부 라운드 상위 5 피팔로우 노드가 후반부 FOLLOW 의 몇
  비율을 흡수하는가([0,1]). 높을수록 "이미 인기있는 노드를 더 follow" 하는 herd.
  라운드 < 2 또는 전/후반 한쪽 FOLLOW 0 이면 None

## 정규화 규약

세 메트릭 모두 `[0, 1]` 구간으로 정규화한다. 입력 부족 / 정의되지 않은 케이스
(이벤트 0 건, 노드 < 3, vocab ≤ 1) 는 일관되게 0.0 으로 보고한다 —
self-consistent 한 fallback 규약.

## OASIS 베이스라인 상태

OASIS 논문은 위 3 메트릭에 대한 단일 베이스라인 수치를 공개하지 않는다.
활성률 같은 핵심 파라미터조차 단일 상수 형태로 보고되지 않음 (memory note
"Validation gaps" §8.3).

→ 등가성 판단은 **self-baseline (run-to-run 회귀)** 으로 시작한다:

1. 같은 시드 / 같은 입력으로 두 번 돌려 동일 값 확인 (재현성)
2. 시드만 바꿔 N 회 돌려 분포 측정 → 평균 ± 표준편차 기록
3. 코드 변경 후 같은 시드 셋으로 재실행 → 평균이 σ 범위 안인지 확인

OASIS 측 수치 확보 시 직접 비교로 승격. 그때까지는 회귀 게이트로만 사용.

## 캘리브레이션 계획

* **단기 (현재 PR)**: `QaMetrics` 모델 + `aggregate_events` 산출. 단위 테스트로
  계산 정확도와 결정성 검증.
* **중기**: Phase 2 시뮬레이션을 N=10 ~ 50 회 돌려 self-baseline 분포 수집.
  결과를 `docs/qa/baseline.md` 에 기록 (별도 PR).
* **장기**: OASIS 데이터셋 또는 동등한 외부 시뮬레이션 확보 시 직접 비교.
  `RoundEvent.topics` 추가 후 `content_word_entropy_normalized` 를 진짜
  topic entropy 로 교체.

## 보고서 인용 규약

`ReportComposer` 는 `AggregationResult.qa_metrics` 를 그대로 직렬화해 보고서
하단 "QA 메트릭" 섹션에 노출한다. LLM 분석을 거치지 않으므로 같은 입력에
같은 수치가 보장된다 — 재현성 강제 (Phase 3 메모리 노트).
