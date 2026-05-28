# ADR-0001 — 페르소나-메모리 cosine threshold 0.40 + extracted hard-error 승격

- 결정일: 2026-05-28
- 상태: Accepted
- 근거 이슈: [#21](https://github.com/teamfossil/litemiro/issues/21) (측정),
  [#58](https://github.com/teamfossil/litemiro/issues/58) (옵션 B 인프라),
  계약 §6.5 / §8.4
- Owner: B (관측 + threshold 민감도), C (Loader hard-error 게이트 합의)

## 컨텍스트

계약 §6.5 의 `OntologyLoader.validate_consistency` 가 페르소나 토픽 (LLM 추상 정책
개념) 과 메모리 토픽 (NER 추출 엔티티명 + 도메인 클래스) 을 set intersection
으로 비교했을 때, #54 머지 후 quick 3런 측정에서 active 의 **78.3% (54/69)** 가
warning. 모두 `origin=extracted`. 어휘공간이 근본적으로 분리돼 있어 표면 정규화
(casefold / stop-word) 로는 못 좁힘 — 자세한 사례는 `#21` 측정 코멘트.

#58 옵션 B 가 `sentence-transformers all-MiniLM-L6-v2` 임베딩 cosine 으로 비교
방식을 우회하도록 인프라를 깔았고 (PR #69, ConsistencyWarning.max_similarity +
`EmbedderLike` 주입), §8.4 가 hard-error 승격 기준을 "warning rate 5% 미만 안정"
으로 박았다. 본 ADR 은 그 calibration 측정 결과와 승격 결정의 근거를 보존한다.

## 측정

입력은 #54 머지 후 #21 측정에 썼던 ontology JSON 을 그대로 재활용 (`runs/persona-
mem-remeasure/seed-{42,43,44}/ontology_{a,b}.json`, `runs/` 는 gitignore). 같은
입력에 (a) legacy set intersection (b) 옵션 B 임베딩 cosine 두 경로를 돌려
경로별 차이만 정량한다. 재현 명령:

```
uv run --extra embedding python scripts/measure_persona_memory_cosine.py \
    runs/persona-mem-remeasure
```

### Warning rate (active 69 = semantic 비어있지 않은 에이전트, 3 seed pool)

| 경로                                | warning | rate     |
| ----------------------------------- | ------- | -------- |
| legacy (set intersection)           | 54 / 69 | **78.3%** |
| 옵션 B cosine, threshold=0.40       | 0 / 69  | **0.0%**  |

### Cosine 분포 (옵션 B, active 69)

| min  | p25  | p50  | p75  | max  | mean |
| ---- | ---- | ---- | ---- | ---- | ---- |
| 0.574 | 0.686 | 0.743 | 0.849 | 1.000 | 0.781 |

### Threshold sweep

| threshold | warn | rate  | extracted | derived |
| --------- | ---- | ----- | --------- | ------- |
| 0.25      | 0    | 0.0%  | 0         | 0       |
| 0.35      | 0    | 0.0%  | 0         | 0       |
| **0.40**  | **0** | **0.0%** | **0** | **0** |
| 0.45      | 0    | 0.0%  | 0         | 0       |
| 0.50      | 0    | 0.0%  | 0         | 0       |
| 0.55      | 0    | 0.0%  | 0         | 0       |
| 0.60      | 5    | 7.2%  | 5         | 0       |

### 최저 cosine 사례 (가장 mismatch — warning 이 의미 있는 가까운 케이스)

| seed | agent_id                 | cos    |
| ---- | ------------------------ | ------ |
| 44   | lee_ha_nul               | 0.574  |
| 42   | kai_alliance             | 0.574  |
| 42   | lee_junseok              | 0.594  |
| 44   | jeong_min_su             | 0.594  |
| 42   | naver                    | 0.596  |

`lee_ha_nul` 의 페르소나 `(AI 기본법의 기술 구현 가능성, AI 기술 원리, ...)` ↔
메모리 `(AIRegulationPolicy, Journalist, 기자가, 이하늘)` — 의미적으로
"기술" 축이 매칭됐지만 페르소나의 추상 정책 vs 메모리의 직업/이름 NER 어휘로
0.574 가 최저. threshold=0.40 과 마진 0.17.

## 결정

1. `OntologyLoader.validate_consistency` 의 디폴트 `similarity_threshold=0.40`
   유지 — 측정 분포 (p25=0.686) 와 threshold (0.40) 사이 마진 0.29 로 안정.
2. `OntologyLoader.validate_consistency` 에 `raise_on_extracted_mismatch: bool =
   False` opt-in 인자를 추가. `True` 면 `embedder` + `origin=EXTRACTED` 조합에서
   threshold 미만 시 `ValueError`. `derived` 는 warning 유지 (메모리 토픽 결정
   시퀀스가 아직 추상 개념 공간으로 정착되지 않은 단계라 보수적 유지).
3. `integration/run.py` 의 `run_simulation` 진입점이 `raise_on_extracted_mismatch
   =True` 를 켠다. 단위 테스트 / 디버깅 호출은 디폴트 `False` 라 backward-compat.
4. 부수 fix — `_cosine` 의 결과를 `[-1, 1]` 로 clamp. 부동소수 누적 오차로
   `1.0000000000000002` 가 ConsistencyWarning 의 `Field(le=1.0)` 를 깨던 production
   ValidationError 의 근본 fix.

## 함정 / 한계

- **측정 코퍼스 단일** — 입력 ontology 가 "AI 규제" 단일 도메인. 다른 도메인
  (예: 의료, 교육) 에서 페르소나-메모리 어휘 분리가 비슷한 분포를 만드는지
  미검증. 후속 도메인이 추가되면 본 ADR 의 측정을 같은 스크립트로 재실행.
- **cos=1.000 케이스 10건** — `data_sovereignty_issue` seed=42 의 페르소나
  `(RegulatoryIssue, 데이터 주권)` ↔ 메모리 `(RegulatoryIssue, 개인정보위가, 검토)`
  처럼 단일 토픽 매치로 1.0 이 나옴. `_generate_seed_memories` 가 페르소나 토픽
  일부를 메모리 토픽으로 복사하던 #54 이전 잔재일 가능성. **결론에는 영향 없음**
  (0% << 5%) 이지만 표본 신뢰도를 일부 깎으므로 별도 후속 이슈 후보.
- **derived 보수 유지** — derived 에이전트는 현재 quick 측정에서 `semantic` 가
  거의 비어 있어 active 표본에 0건. derived 의 메모리 토픽 결정 시퀀스가 정착
  (post-MVP §8) 한 뒤 같은 calibration 으로 derived 까지 hard-error 확대 가능.
- **active 표본 작음 (69)** — Phase 1 quick 의 cold start 비율 76% 가 남기는
  active 한정 표본. medium / full 프리셋 측정으로 갱신 권장.

## 후속

- `validate_consistency` 호출자가 늘면 opt-in 디폴트를 `True` 로 뒤집을지 재검토
  (현 디폴트는 backward-compat 위주).
- `cos=1.000` 군집 원인 추적 — `_generate_seed_memories` 의 토픽 복사 경로 확인.
- 다른 도메인 ontology 추가 시 본 ADR 측정 반복 + 표 부록.
