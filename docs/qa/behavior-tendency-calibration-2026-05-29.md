# behavior_tendency 비율 보정 — ActionSelector family 게이트 (2026-05-29)

## 문제

Phase 1 이 페르소나마다 6 개 행동 성향 (`post_rate`, `reply_rate`,
`repost_rate`, `like_rate`, `follow_rate`, `controversy_affinity`) 을 박지만,
Phase 2 시뮬레이션의 실제 action 분포가 이 목표 비율과 크게 어긋났다. e2e
(`runs/e2e-2026-05-28`, 100 agent · 3 라운드 · 114 events) 에서:

| action | 실제 | 목표 (100인 평균) |
|---|---:|---|
| CREATE_POST | 28.1% | post_rate 0.357 — 그런데 r0 cold-start 31 개가 전부, **r1=1 · r2=0** |
| FOLLOW | 1.8% | follow_rate 0.465 — 목표의 1/25 |
| REPOST | 7.0% | repost_rate 0.474 |
| QUOTE | 21.9% | — (r2 에서 49% 로 폭발) |

두 가지 근본 원인이 겹쳐 있었다.

**1. prompt 산수가 깨져 있었다.** 직전 prompt 는 reaction 분배를
`QUOTE = reply_rate − like_rate − repost_rate` (remainder) 로 정의했는데,
Phase 1 페르소나의 **97/100 명에서 이 값이 음수** (평균 −0.458) 였다. 거의
전원에게 "QUOTE 확률 = 음수" 라는 불가능한 지시를 준 셈이라 LLM 이 무시하거나
제멋대로 해석했고, 그 결과 QUOTE 가 라운드에 따라 49% 까지 튀었다. Phase 1 은
6 개 rate 를 각각 독립 [0,1] 성향으로 생성하는데, prompt 가 임의로
"reply = umbrella, like/repost = 그 안의 share" 라는 산수를 덮어씌운 게 화근.

**2. originate 축은 prompt cue 로는 못 맞춘다.** CREATE_POST · FOLLOW 는 feed
와 무관한 독립 축인데, feed 가 차면 LLM 은 매 틱 "지금 그럴듯한" reaction 을
고르고 originate 를 계속 건너뛴다. #120 → #122 → #150 이 전부 prompt cue 로
때웠지만 FOLLOW 1.8% / CREATE_POST r1+ 0 은 그대로였다. LLM 은 0.465 같은
확률을 장기 빈도로 누적하지 않는다 — 단발 의사결정만 한다.

## 설계 — opt-in family 게이트 (하이브리드)

행동 family 결정을 코드가 맡고, family 안의 구체화 (타깃 · 콘텐츠) 만 LLM 에
맡긴다. `ActionSelector` 에 `global_seed` 를 넘기면 게이트가 켜지고, 안 넘기면
(단위 테스트) 꺼져 기존 prompt/선택 계약이 그대로 유지된다.

**family 가중 정규화 샘플 (`_gate`)** — 세 family 가 성향 가중으로 경쟁한다.
순차 우선순위가 아니라 정규화 샘플이라 `reply_rate` 가 originate 에 밀려나지
않는다.

```
feed 비었으면(cold-start)           → CREATE_POST 강제
아니면 weight 정규화 후 샘플:
  CREATE_POST = post_rate
  FOLLOW      = follow_rate   (feed 에 non-self author 가 있을 때만)
  REACTION    = reply_rate    (LIKE / REPOST / QUOTE / DO_NOTHING)
```

샘플된 family 로 prompt 의 출력 schema 를 좁힌다 — CREATE_POST/FOLLOW 강제 시
허용 타입을 그 하나로, reaction 분기 시 originate 두 종을 allowed 에서 제거.
LLM 이 그래도 어기면 `DO_NOTHING` + `fallback_used=True` 로 떨어뜨려 위반을
관측 가능하게 남긴다.

**reaction 분배** — 음수 remainder 산수를 폐기하고
`LIKE : REPOST : QUOTE = like_rate : repost_rate : controversy_affinity` 정규화
share 를 prompt 에 준다. 셋 다 [0,1] 양수라 음수가 나올 수 없다.

**재현성** — 게이트 RNG seed 는 `sha256(f"{global_seed}:{agent_id}:{round_num}")`
(AgentScheduler 와 같은 방식). 같은 시드 재실행 시 모든 게이트 결정이 동일.

## 설계 반복 — 순차 → 정규화

첫 구현은 `post_rate 베르누이 → 실패 시 follow_rate → 실패 시 reaction` 순차
게이트였다. 측정해보니 붕괴는 풀렸지만 reaction 이 위축됐다.

| action | 순차 게이트 | 정규화 게이트 | 어제 e2e |
|---|---:|---:|---:|
| CREATE_POST | 52.0% | 35.7% | 28.1% |
| LIKE | 11.2% | 23.0% | 36.8% |
| REPOST | 2.6% | 3.1% | 7.0% |
| QUOTE | 7.1% | 16.3% | 21.9% |
| FOLLOW | 24.5% | 20.9% | 1.8% |
| DO_NOTHING | 2.6% | 1.0% | 4.4% |

순차 게이트는 originate 를 먼저 다 굴리고 남은 것만 reaction 에 줘서
`reply_rate` (reaction 성향, 평균 0.628 로 6 키 중 최고) 를 통째로 무시했다 —
LIKE 가 37% → 11% 로 죽은 이유. 정규화 샘플은 세 family 를 공평히 경쟁시켜
reply_rate 를 반영한다.

## 측정 (정규화 게이트)

- 코드: 본 분기. ontology 재사용 `runs/e2e-2026-05-28` (100 agent), seed 동일.
- 시뮬: 5 라운드, 196 events, `runs/calib2-2026-05-29`. `litemiro-validate`
  196/196 통과.
- fallback_used **1.0%** — 게이트가 좁힌 family 를 LLM 이 99% 순응.

**정상 라운드 (r1~r4, cold-start r0 제외)**: CREATE 21% / FOLLOW 26% /
reaction 53%. 순차 게이트의 reaction 26% → 정규화 53% 로 회복.

**LLM 없는 게이트 분포** (feed 참 가정, 100 agent × 8 라운드): CREATE 24.8% /
FOLLOW 31.8% / REACTION 43.5%. 목표 `post:follow:reply` 정규화 (25% / 32% /
43%) 와 일치 — 게이트 자체가 목표 비율을 정확히 구현함을 LLM 비용 없이 확인.

**reaction 내부**: LIKE 54% / REPOST 7% / QUOTE 39%. LIKE 가 최다로 정상화
(직전 QUOTE 폭발 해소). QUOTE 가 다소 높은 건 controversy_affinity (평균
0.373) 가 가중에 들어간 결과.

## 한계

- **cold-start CREATE 강제**가 초반 라운드 CREATE 를 끌어올린다. 전체 35.7% 중
  상당수가 r0 (feed 빈 36 개 전부 CREATE) 다 — 정상 라운드만 보면 21% 로 목표
  (feed 참 25%) 에 근접. 라운드가 길어져 feed 가 차면 더 수렴할 것으로 보이나
  5 라운드 단명 측정이라 직접 확인은 미수행.
- **follow_rate 값 자체의 적정성**은 본 작업 범위 밖. 게이트는 follow_rate 를
  충실히 반영할 뿐 — 매 정상 라운드 26% FOLLOW 가 과하다면 Phase 1 생성 로직의
  값 분포를 조정해야 한다.
- single-seed · single-corpus · 5 라운드. multi-seed 분산이나 다른 도메인
  ontology 에서의 재현은 미수행.

## 재현

```sh
uv run litemiro-run \
  --ontology-a runs/e2e-2026-05-28/ontology_a_persona.json \
  --ontology-b runs/e2e-2026-05-28/ontology_b_memory.json \
  --rounds 5 --output-dir runs/calib2-2026-05-29

uv run litemiro-validate --jsonl runs/calib2-2026-05-29/events.jsonl
```

게이트는 `global_seed` 를 받는 경로 (`litemiro-run` → `integration/run.py`) 에서만
활성. 단위 테스트는 seed 를 안 넘겨 게이트 off — 기존 계약 그대로 검증된다.
