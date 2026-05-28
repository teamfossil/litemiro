# Phase 2 ActionSelector 분포 측정 - 2026-05-28

Issue: #123 (PR #120 follow-up)

## Scope

PR #120 (라벨 정정 + QUOTE 가드 + umbrella note) 가 100-agent 풀 sim 의
ActionType 분포에 미친 영향을 측정. PR #120 코멘트에 표가 한 번 올라갔지만
docs lock-in 이 안 돼 있어 본 저널로 박는다.

데이터 출처는 두 raw events.jsonl:

- `runs/debug3/sim/events.jsonl` — #120 적용 전 baseline. 7 라운드, 92 agent, 275 events
- `runs/debug4/events.jsonl` — #120 적용 후. 15 라운드, 97 agent, 571 events

debug3 가 7 라운드라 fair comparison 은 **debug4 의 0~6 라운드 슬라이스만**
취해 동일 윈도우로 자른다. 같은 ontology / 같은 seed 기반 비교.

## Fair Comparison (rounds 0–6)

| Action | v1 baseline (debug3) | v3 #120 적용 (debug4 r0-6) | Δ |
|---|---:|---:|---:|
| LIKE_POST | 60 (21.8%) | 113 (41.1%) | +19.3pp |
| REPOST | 14 (5.1%) | 35 (12.7%) | +7.6pp |
| QUOTE_POST | 157 (57.1%) | 80 (29.1%) | −28.0pp |
| CREATE_POST | 30 (10.9%) | 27 (9.8%) | −1.1pp |
| FOLLOW | 4 (1.5%) | 6 (2.2%) | +0.7pp |
| DO_NOTHING | 10 (3.6%) | 14 (5.1%) | +1.5pp |
| total | 275 | 275 | — |

PR #120 의 핵심 의도였던 **QUOTE 단독 쏠림 (57.1%) → 29.1% 로 28pp 감소**
가 들어왔고, 그만큼이 LIKE (+19.3) + REPOST (+7.6) 의 reaction split 으로
분산됨. CREATE_POST 와 FOLLOW 는 거의 안 흔들림 — reaction family 안에서만
재분배된 결과.

## debug4 15-라운드 시계열

| round | total | LIKE | REPOST | QUOTE | CREATE | FOLLOW | DO_NOTHING |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 40 | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) | 27 (67.5%) | 0 (0.0%) | 13 (32.5%) |
| 1 | 42 | 25 (59.5%) | 7 (16.7%) | 10 (23.8%) | 0 | 0 | 0 |
| 2 | 38 | 18 (47.4%) | 8 (21.1%) | 12 (31.6%) | 0 | 0 | 0 |
| 3 | 37 | 11 (29.7%) | 10 (27.0%) | 14 (37.8%) | 0 | 2 (5.4%) | 0 |
| 4 | 44 | 23 (52.3%) | 1 (2.3%) | 19 (43.2%) | 0 | 1 (2.3%) | 0 |
| 5 | 39 | 18 (46.2%) | 4 (10.3%) | 15 (38.5%) | 0 | 2 (5.1%) | 0 |
| 6 | 35 | 18 (51.4%) | 5 (14.3%) | 10 (28.6%) | 0 | 1 (2.9%) | 1 (2.9%) |
| 7 | 43 | 25 (58.1%) | 5 (11.6%) | 13 (30.2%) | 0 | 0 | 0 |
| 8 | 36 | 18 (50.0%) | 8 (22.2%) | 10 (27.8%) | 0 | 0 | 0 |
| 9 | 40 | 15 (37.5%) | 10 (25.0%) | 13 (32.5%) | 0 | 2 (5.0%) | 0 |
| 10 | 31 | 14 (45.2%) | 5 (16.1%) | 12 (38.7%) | 0 | 0 | 0 |
| 11 | 30 | 17 (56.7%) | 6 (20.0%) | 5 (16.7%) | 0 | 2 (6.7%) | 0 |
| 12 | 43 | 23 (53.5%) | 9 (20.9%) | 11 (25.6%) | 0 | 0 | 0 |
| 13 | 34 | 16 (47.1%) | 6 (17.6%) | 11 (32.4%) | 0 | 1 (2.9%) | 0 |
| 14 | 39 | 17 (43.6%) | 4 (10.3%) | 16 (41.0%) | 1 (2.6%) | 1 (2.6%) | 0 |

## 관찰

- **r0 은 cold-start 패턴** — feed 가 비어 reaction 자체가 불가하므로
  CREATE_POST 67.5% + DO_NOTHING 32.5% 로 갈린다. 분포 비교에서 r0 은
  논리적으로 reaction 평균에서 빼고 봐야 함.
- **r1 이후 LIKE/QUOTE/REPOST 가 안정적** — LIKE 평균 약 48%, QUOTE 약 31%,
  REPOST 약 16% 로 round drift 없이 평평하다. PR #120 의 "라운드가 지나면
  QUOTE 가 다시 폭증할 수도" 우려는 15 라운드까지는 발생 안 함.
- **FOLLOW 가 여전히 sparse (라운드당 0~2건)** — r0-6 fair window 에서
  2.2%, 전체 15 라운드에서도 2.1%. behavior_tendency 의 follow_rate 평균
  0.358 와 비교하면 여전히 한참 낮다. #140 (FOLLOW 0건 fix) 가 동일
  raw observation 에서 출발한 별도 PR — 머지 후 재측정해야 함.
- **CREATE_POST 가 r0 이후 사실상 0** — 15 라운드 중 r14 에 1건. agent 가
  존재하는 post 에 reaction 으로만 응답하고 새 post 를 잘 만들지 않는
  bias. 본 PR 의 scope 밖이지만 추적 대상.

## 한계

- 단일 ontology / 단일 seed 측정이라 표본 1. 다른 ontology 에서 같은
  분포 형태가 나오는지는 미확인.
- 92 / 97 agent 의 7 / 15 라운드는 통계적 baseline 으로는 짧다. FOLLOW 처럼
  2% 미만 액션은 표본 분산이 크다.
- v2 (PR #111 의 LIKE-default cue, 81.9% LIKE) 는 본 측정에 포함 안 함 —
  이미 PR #111 코멘트에서 폐기 결정 났고 raw events 도 보존돼 있지 않음.

## 후속

- #140 (action_selector FOLLOW 0건 — author 정보 + behavior cue 보강) 머지
  후 동일 ontology / seed 로 재측정. FOLLOW % 가 follow_rate 분포
  (평균 0.358) 에 어디까지 따라가는지 확인.
- 20+ 라운드 sim 으로 LIKE/QUOTE/REPOST 평균이 수렴하는지 추가 측정.
  본 측정의 15 라운드는 cold-start 1 + 정상 14 라 정상 구간의 표본이 14.
- like_rate / repost_rate / quote_rate 가 Phase 1 ontology 스키마에서
  분리되면 (현재는 `reply_rate` 가 reply or quote 로 매핑) reaction split
  의 직접 가중치가 잡힌다 — PR #111 본문 후속 항목.
