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

### `content_word_entropy_normalized`

`CREATE_POST` / `QUOTE_POST` 의 `content` 를 공백으로 토크나이즈한 word
frequency 의 Shannon / `log2(|vocab|)`.

* 빈 토큰 또는 vocab ≤ 1 → 0.0
* 한국어 형태소 분석 없이 단순 어휘 다양성만 근사

진짜 토픽 entropy 는 `RoundEvent` 스키마에 `topics: list[str]` 필드 추가 후
별도 PR 에서 정확화 — 본 메트릭은 그때까지의 프록시.

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
