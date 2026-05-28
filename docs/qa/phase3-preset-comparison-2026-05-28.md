# Phase 3 preset 별 보고서 깊이·비용 비교 - 2026-05-28

## Scope

`ReportConfig.preset` 가 보고서 깊이에 미치는 영향을 측정. PRD §4.2 / §6.3
는 quick (1 콜) / standard (4 콜) / full (4 콜, macro+micro) 의 호출 수만
정의하고, **실제 보고서 quality 와 비용 차이** 가 raw 데이터로 박혀 있지
않다. CLI 디폴트가 `quick` 인 근거를 evidence 로 잠그고, standard / full
을 선택할 때의 trade-off 를 외부 사용자가 한 화면에 볼 수 있게 한다.

`8df3d3c fix(phase3): 보고서 깊이 보강` 이후 composer 가 카테고리 raw
JSON 통계를 직접 인용해 풍부한 본문을 작성한다 — 즉 quick 도 짧은 analyzer
인사이트 + 풍부한 raw JSON 으로 깊이가 확보된다는 게 본 측정의 출발 가정.

## Data

- Events: `runs/debug3/sim/events.jsonl` — 7 라운드, 92 agent, 275 events.
- 코드: main `a76707e`.
- 동일 `events.jsonl` × 3 preset × 1 run 씩.
- Composer primary `claude-opus-4.7`, analyzer `qwen-plus`. 폴백 없이 모두
  primary 로 닫힘.
- 산출물: `runs/measure/{baseline,standard,full}.md`.

## 정량 비교

| preset | bytes | L2 (`##`) | L3 (`###`) | 표 row | bullet | analyzer tok | composer tok | total tok | quick 대비 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| quick | 9,975 | 7 | 7 | 32 | 41 | 4,206 | 10,751 | 14,957 | 1.0× |
| standard | 13,488 | 7 | 11 | 42 | 30 | 17,225 | 29,013 | 46,238 | 3.1× |
| full | 11,018 | 7 | 12 | 32 | 31 | 10,921 | 18,419 | 29,340 | 2.0× |

- L2 섹션 7 개는 세 preset 모두 동일 — composer system prompt 가 "(1) 규모
  개요 ~ (7) 종합 요약" 7 섹션을 강제하기 때문.
- standard 가 가장 풍부 (bytes +35%, 표 row +31%, L3 +57% over quick).
- full 은 길이 / 표 row 가 quick 과 거의 동일 — macro+micro 두 시각이 한
  응답에 들어가 카테고리당 분량이 짧게 끊겼기 때문으로 추정.
- standard 가 full 보다 토큰을 1.57 배 더 썼다 (`_FULL_INSTRUCTION` 보다
  `_STANDARD_INSTRUCTION` 이 더 길게 분석가 톤을 풀어내는 결과).

## 정성 비교

세 산출물을 동일 7 섹션 기준으로 읽어 분석가 톤 / 메타 해석 / sub-section
세분화를 비교했다.

| 측면 | quick | standard | full |
|---|---|---|---|
| 메타 해석 | "이념적으로 정렬된 정책 토론장" 정도의 종합 명제 1~2 개 | "이중 채널 + 보조 채널 구조" / "12.4% 행동 점유" 등 정량 라벨 다수 | "어떠한 멱함수 분포도 관측되지 않는다" 등 분포 자체에 대한 정성 평가 |
| QA 지표 인용 | 본문 표 1 곳 | 본문 단락에서 entropy 0.685 를 직접 인용·해석 | 본문 1 곳, 표 1 곳 |
| 데이터 정합성 검증 | 없음 | "시계열 라운드 합산 = 275 와 일치" 명시 | 없음 |
| sub-section 번호링 | `### 한계` 식 | `2.1 / 2.2 / 3.1 / 3.2` 식 | `### 행동 유형별 비중` 식 |
| 한계 절 | 명시 (3 개 bullet) | 명시 (4 개 bullet, 표본 분산 포함) | 명시 (2 개 bullet) |

종합:

- quick 도 7 섹션 + 표 + 한계 + 시사점이 모두 갖춰진다. composer 가 raw
  JSON 통계를 직접 인용해 풍부화하는 구조 (`_build_user_prompt` 의
  payload JSON) 가 효과적으로 동작 중.
- standard 는 sub-section 번호링 / QA 지표 본문 인용 / 데이터 정합성
  검증 같은 **분석가 보고서 톤** 이 한 단계 위. 발표·외부 공유용 보고서로
  적합.
- full 의 macro+micro 분리는 카테고리별 응답 길이를 짧게 만들어, standard
  대비 깊이가 오히려 얕다. 본 측정 1 회만 보면 full 의 ROI 가 가장 낮다.

## 권장 default

CLI 디폴트는 **`quick` 유지**. 근거:

- 7 섹션 / 표 다수 / 한계 / 시사점 모두 자동 포함 — 깊이 미달 사례가
  본 단발 측정에서 발견되지 않는다.
- standard 대비 토큰 비용 ⅓ (15k vs 46k).
- standard 의 marginal gain (sub-section 번호링, QA 지표 본문 인용 등) 이
  발표용에선 가치가 있지만 일반 시뮬레이션 라운드의 자동 산출물엔 과함.

`standard` 선택 시점: 발표·외부 공유용 보고서, 또는 entropy / clustering
같은 QA 지표를 본문 안에서 해석시키고 싶을 때. `full` 은 본 측정 기준
quick·standard 어느 쪽 대비도 ROI 가 약함 — macro+micro 분리가 명시적으로
필요한 ad-hoc 분석에서만 권장.

## Follow-up — cli2 표본 재측정 (2026-05-28)

위 측정은 debug3 단일 events.jsonl 단발 — 한계 절에 박은 "표본 1 회는
분산 안에 묻힐 수 있다" 가 가장 큰 빈틈이다. 본 절은 **다른 events.jsonl
한 건** 으로 같은 3 preset 을 재실행해 추세 방향성이 corpus 를 갈아도
유지되는지 확인한다.

### Data

- Events: `runs/cli2/sim/events.jsonl` — 15 라운드, 97 agent, 631 events
  (debug3 대비 라운드 2.1× · 이벤트 2.3×).
- 코드: 본 분기 `docs/phase3-preset-comparison` 상태 (composer / analyzer
  로직 동일, PR 본문이 docs-only 이므로 코드 변경 없음).
- 같은 `events.jsonl` × 3 preset × 1 run. Composer / analyzer 모델 동일.
- 산출물: `runs/measure-followup/{quick,standard,full}.md`.

### 정량 비교 (cli2)

| preset | bytes | L2 (`##`) | L3 (`###`) | 표 row | analyzer tok | composer tok | total tok | quick 대비 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| quick | 9,235 | 7 | 7 | 39 | 4,480 | 10,643 | 15,123 | 1.0× |
| standard | 12,345 | 7 | 13 | 56 | 17,720 | 28,903 | 46,623 | 3.1× |
| full | 11,406 | 7 | 14 | 40 | 11,976 | 19,865 | 31,841 | 2.1× |

### debug3 측정과의 일치

추세 방향성 (높은 깊이·비용 순) 은 두 corpus 에서 동일:

| 지표 | debug3 순위 | cli2 순위 | 일치 |
|---|---|---|---|
| bytes | quick < full < standard | quick < full < standard | ✓ |
| L3 sub-section | quick < standard < full | quick < standard < full | ✓ |
| 표 row | quick = full < standard | quick < full < standard | ✓ (standard 1 위) |
| analyzer tok | quick < full < standard | quick < full < standard | ✓ |
| composer tok | quick < full < standard | quick < full < standard | ✓ |

per-preset 변동은 5~9% 안쪽 (예: quick total tok 14,957 → 15,123 = +1.1%,
standard 46,238 → 46,623 = +0.8%, full 29,340 → 31,841 = +8.5%) — 본 결합
가설인 "LLM 비결정성은 단발 표본 안에 분산으로 흡수된다" 와 일관.

### 정성 일치

cli2 산출물도 PR #149 의 정성 비교 표와 같은 패턴을 그대로 재현:

- **standard** — `2.1 / 2.2 / 3.1 / 3.2 / 3.3 / 4.1` 식 sub-section 번호링,
  본문에서 `action_entropy_normalized = 0.4812` 같은 QA 지표를 직접 인용·
  해석. "콘텐츠 생산이 전체 이벤트 대비 96.0%를 차지하며 … 콘텐츠·해석
  중심으로 설계되었음" 식의 정량 라벨 + 메타 명제.
- **full** — sub-section 번호링은 standard 와 동급이나, "극단적 장기분포
  (long-tail)" / "관계 형성 능력과 콘텐츠 영향력이 일치하지 않을 수 있음"
  처럼 분포 자체에 대한 정성 평가 중심. 카테고리당 분량은 standard 보다
  짧음 — debug3 측정에서 본 "macro+micro 분리가 카테고리 깊이를 얕게 만든다"
  현상이 그대로.
- **quick** — sub-section 번호링 없고 "단축적 핵심 주체 구조" 같은 종합
  명제 1~2 개 + 표 + 한계 + 시사점. 분석가 톤은 한 단계 낮으나 7 섹션 정형
  자체는 그대로.

### 결론

- 추세 방향성 **standard > full > quick** 은 corpus 두 건에서 일관 — PR
  #149 의 권장 default (`quick` 유지) 결정에 표본 외부 타당성을 부여한다.
- per-preset 토큰·길이 변동은 한 자리 % 수준 — 단발 측정의 "분산 안에
  묻힐 수 있다" 한계가 실제로는 추세를 흔들지 않음을 한 표본 늘려 확인.
- multi-seed × multi-corpus 까지 가는 비용 (분 단위 LLM 호출 × N) 대비,
  본 단계의 evidence-of-trend 만으로도 디폴트 결정에 충분하다는 판단.

## 한계

- 표본은 debug3 (275 events) + cli2 (631 events) 두 건 — corpus 다양성은
  늘었지만 여전히 한 ontology 가족 (AI 거버넌스 담론) 안이다. 다른 도메인
  ontology (예: 비-정책 주제) 에서 추세가 재현되는지는 미수행.
- preset 당 여전히 single-seed × single-sim. 한 corpus 안에서 다중 seed
  분산을 직접 측정한 것은 아니다. follow-up 변동이 작다는 것은 간접
  증거에 불과.
- 비용 비교는 token 합산 기준. Opus 출력 단가와 Qwen 입력 단가의
  실제 가격은 별도 계산 필요. composer (Opus) token 만 따로 봐도 ratio
  는 동일 추세 (cli2 quick 10.6k / standard 28.9k / full 19.9k).
- 정성 "분석가 톤" 평가는 단일 reader 인상 기반. 외부 reviewer 가 같은
  3 산출물을 평가해 합의를 보는 단계는 미수행.

## 재현

```sh
# debug3 측정 — 본 docs 의 1차 표
mkdir -p runs/measure
for p in quick standard full; do
  uv run litemiro-report \
    --events runs/debug3/sim/events.jsonl \
    --preset $p \
    --output runs/measure/$p.md
done

# cli2 follow-up 측정 — 추세 방향성 재확인
mkdir -p runs/measure-followup
for p in quick standard full; do
  uv run litemiro-report \
    --events runs/cli2/sim/events.jsonl \
    --preset $p \
    --output runs/measure-followup/$p.md \
    > runs/measure-followup/$p.stdout 2>&1
done

# 메트릭 수집 (각 디렉토리)
wc -c {디렉토리}/{quick,standard,full}.md
grep -cE "^## "  {디렉토리}/{quick,standard,full}.md   # L2 섹션
grep -cE "^### " {디렉토리}/{quick,standard,full}.md   # L3 섹션
grep -c  "^|"    {디렉토리}/{quick,standard,full}.md   # 표 row (헤더/구분선 포함)
```

산출물은 `runs/measure/{baseline,standard,full}.md` + `runs/measure-followup/
{quick,standard,full}.md` 로 박혀 있고, 각 호출의 토큰 수치는 stdout 파일에
기록됐다.
