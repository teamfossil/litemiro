# Phase 1 → Phase 2 통합 계약 (MVP)

## 0. 목적

Phase 1(듀얼 온톨로지 생성)이 산출하는 `OntologyA` / `OntologyB` JSON 산출물을
Phase 2(라운드 시뮬레이션)의 입력 객체(`Agent`, `SocialGraph`, `StateStore`)로
변환하는 단일 경계 — `OntologyLoader` — 의 동작을 고정한다.

본 문서는 **MVP 통합 1차분**의 계약만을 정의한다. 메모리 갱신 사이클, 동적 토픽
가중치 등은 Section 8 후속 마일스톤에서 다룬다.

## 1. 범위

| 항목 | MVP 포함 | 비고 |
|---|---|---|
| `OntologyA` → `Agent[]` 매핑 | O | Section 4.1 |
| `OntologyB` → `Agent.memory_summary` (top-N concat) | O | Section 4.2 |
| `initial_following` → `SocialGraph` | O | Section 4.3 |
| `OntologyLoader` API | O | Section 5 |
| 스키마 검증 (jsonschema) | O | Section 6 |
| E2E 스모크 테스트 | O | Section 7 |
| Phase 2 → Phase 1 메모리 갱신 | X | post-MVP (Section 8) |
| 동적 `MemoryConfig` 반영 | X | post-MVP (Section 8) |
| `OntologyA.ontology.topic_hierarchy` 활용 | X | post-MVP (Section 8) |

## 2. Phase 1 출력 (소비 측 관점)

`src/litemiro/phase1/models.py` 기준. 본 절은 소비자가 의존하는 필드만 열거한다.

### 2.1 `OntologyA` (필수 소비 필드)

- `seed: int` — 재현성 검증용
- `agent_count: int` — 일관성 검사용
- `agents: dict[str, AgentProfile]` — 키는 `agent_id`

### 2.2 `AgentProfile` (필수 소비 필드)

- `agent_id: str`
- `topics: list[str]` → `Agent.interests`
- `personality, speech_style, background: str` → `Agent.persona_traits`
- `behavior_tendency.post_rate: float ∈ [0,1]` → `Agent.activation_rate`
- `initial_following: list[str]` → `SocialGraph`

MVP에서 미사용(보존만): `entity_type`, `origin`, `derived_from`, `skeleton`,
`ideology`, `sensitive_topics`, `behavior_tendency.{reply_rate,repost_rate,controversy_affinity}`.
이들은 `Agent.persona_traits`에 그대로 들어가 후속 단계에서 참조 가능하다.

### 2.3 `OntologyB` (필수 소비 필드)

- `stores: dict[str, MemoryStore]` — 키는 `agent_id`
- `stores[id].semantic: list[SemanticMemory]`
  - `summary: str`
  - `simulation_count: int`
  - `last_relevant_sim: int`

MVP에서 미사용(보존만): `config`, `stores[id].episodic`, `semantic.{topics,
dominant_sentiment, key_relationships}`.

## 3. Phase 2 입력 (소비 측 관점)

`src/litemiro/models.py`, `src/litemiro/core/state_store.py`,
`src/litemiro/social/graph.py` 기준.

### 3.1 `Agent` (Pydantic strict)

```python
Agent(
    agent_id: str,
    interests: tuple[str, ...],
    persona_traits: Mapping[str, Any],
    memory_summary: str | None,
    activation_rate: float,  # [0.0, 1.0]
)
```

### 3.2 `StateStore` (construct-and-freeze)

```python
StateStore(
    agents: Iterable[Agent],
    social: SocialGraphLike,
    social_factory: _SocialGraphFactory,  # callable[Mapping[str,Iterable[str]]] -> SocialGraphLike
    checkpoint_dir: Path,
    global_seed: int,
)
```

→ 에이전트는 생성자에서 한 번에 주입. 런타임 `add_agent` 없음.

### 3.3 `SocialGraph`

```python
SocialGraph.from_dict({follower_id: [followee_id, ...], ...}) -> SocialGraph
```

→ self-follow는 `ValueError`. `OntologyLoader`가 사전 필터링한다(Section 4.3).

## 4. 매핑 규칙

### 4.1 `AgentProfile` → `Agent`

| Phase 2 필드 | 출처 | 변환 |
|---|---|---|
| `agent_id` | `AgentProfile.agent_id` | passthrough |
| `interests` | `AgentProfile.topics` | `tuple(...)` |
| `persona_traits` | `AgentProfile` 전체 dict | `model_dump(mode="json")` |
| `memory_summary` | `OntologyB.stores[agent_id].semantic` | Section 4.2 알고리즘 |
| `activation_rate` | `AgentProfile.behavior_tendency.post_rate` | passthrough |

`persona_traits`에는 `AgentProfile.model_dump(mode="json")` 전체를 넣는다
(MVP 미사용 필드 보존, 후속 단계 확장 여지 확보).

> **비용 가정 (검증 의무)**: Phase 0/3 비용 추정 표는 `activation_rate=0.5`
> 평균 가정에 의존한다. 본 매핑은 에이전트별 `post_rate` 를 그대로 사용하므로,
> Phase 1 산출의 `mean(post_rate)` 가 비용 표 가정에서 ±0.1 이상 벗어나면 비용
> 표 재산정 필요. **quick 첫 실행 시
> `statistics.fmean(a.activation_rate for a in agents)` 로 측정**하여 차이가 크면
> Section 8 후속 마일스톤에서 다룬다. (Phase 1 `ProfileGenerator` 가 어떤 prior 로
> `post_rate` 를 결정하는지는 별도 명세 — Phase 1 측 책임.)

### 4.2 `memory_summary` 알고리즘

```
N = 3  # MVP 고정값. post-MVP에서 OntologyB.config.retrieval_max 반영.

semantic = OntologyB.stores[agent_id].semantic
if len(semantic) == 0:
    memory_summary = None
else:
    sorted_mem = sorted(
        semantic,
        key=lambda m: (-m.simulation_count, -m.last_relevant_sim, m.id),
    )
    top_n = sorted_mem[:N]
    memory_summary = "; ".join(m.summary for m in top_n)
```

- **N=3 근거**: `MemoryConfig.retrieval_max` 기본값과 일치. post-MVP에서 동적화.
- **정렬 기준**: `simulation_count desc` (자주 회상된 기억 우선) →
  동률 시 `last_relevant_sim desc` (최근에 회상된 기억 우선) →
  그래도 동률이면 `id asc` (결정성 보장, Section 6.4).
- **결합자 "; "**: 토큰 효율과 가독성 트레이드오프. Phase 3 분석에서 의미
  단위 분리 필요시 조정.
- **빈 리스트는 `None`**: `Agent.memory_summary: str | None` 계약과 일치.

> 품질 트레이드오프 (사용자 합의 사항): top-N concat은 (1) 의미 손실(요약 압축
> 누적), (2) 시간 순서 소실, (3) 관계 정보 누락의 단점이 있으나, MVP는 ActionSelector
> 프롬프트의 메모리 슬롯 길이 제약을 우선한다. 후속 단계에서 retrieval-on-demand로
> 전환.

### 4.3 `initial_following` → `SocialGraph`

```python
edges: dict[str, list[str]] = {}
for agent_id, profile in ontology_a.agents.items():
    followees = [f for f in profile.initial_following
                 if f != agent_id and f in ontology_a.agents]
    if followees:
        edges[agent_id] = followees
social_graph = SocialGraph.from_dict(edges)
```

- self-follow는 사전 필터링 (Phase 1 validator가 거른다고 가정하나 방어적 처리).
- 미지의 agent_id를 가리키는 엣지는 무시 (경고 로그).

## 5. `OntologyLoader` API

`src/litemiro/integration/ontology_loader.py` (신규, **owner: C**).

```python
from pathlib import Path
from litemiro.models import Agent
from litemiro.social.graph import SocialGraph
from litemiro.phase1.models import OntologyA, OntologyB


class OntologyLoader:
    @staticmethod
    def load(
        *,
        ontology_a_path: Path,
        ontology_b_path: Path,
    ) -> tuple[OntologyA, OntologyB]:
        """JSON 파일 → 검증된 Pydantic 객체. jsonschema + Pydantic 양방 검증."""

    @staticmethod
    def build_agents(
        *,
        ontology_a: OntologyA,
        ontology_b: OntologyB,
    ) -> tuple[Agent, ...]:
        """결정적 순서(agent_id 사전순) 보장."""

    @staticmethod
    def build_social_graph(
        *,
        ontology_a: OntologyA,
    ) -> SocialGraph:
        """Section 4.3 매핑."""
```

호출자(통합 진입점, **owner: A**)는 위 3개를 조합하여 `StateStore`를 생성한다.

```python
ontology_a, ontology_b = OntologyLoader.load(...)
agents = OntologyLoader.build_agents(ontology_a=ontology_a, ontology_b=ontology_b)
social = OntologyLoader.build_social_graph(ontology_a=ontology_a)
store = StateStore(
    agents=agents,
    social=social,
    social_factory=SocialGraph.from_dict,
    checkpoint_dir=...,
    global_seed=ontology_a.seed,
)
```

## 6. 검증

1. **스키마 검증** — `litemiro.schemas.ontology_a_schema()` /
   `ontology_b_schema()` 로 jsonschema 검증 후 Pydantic 파싱.
2. **참조 일관성** — `set(OntologyB.stores) == set(OntologyA.agents)` 강제.
   불일치 시 `ValueError`.
3. **agent_count 일관성** — `len(OntologyA.agents) == OntologyA.agent_count`.
4. **재현성** — 동일 입력 + 동일 seed → 동일 `Agent` 튜플, 동일 `SocialGraph.to_dict()`.
5. **페르소나–메모리 모순 검출** — 각 `agent_id` 에 대해
   `set(AgentProfile.topics) & set(reduce(union, [m.topics for m in SemanticMemory]))`
   가 공집합이면 warning 로그. MVP 는 warning 만, 후속 단계에서 hard error 승격
   검토. Phase 2 design 의 `OntologyLoader.validate_consistency` 두 번째 의도
   ("페르소나 관심사 vs 메모리 경험 모순 검출") 반영. **빈 `semantic` 리스트는
   warning 면제** (cold start 케이스).

### 6.1 Reference fixtures

- `tests/data/sample_ontology_a.json` / `sample_ontology_b.json` — 3-agent
  (Journalist/Academic/Citizen) quick 프리셋, self-follow 와 unknown follow,
  cold-start (빈 semantic), 메모리 top-N tie-breaker 케이스를 모두 포함.
- Loader 단위 테스트는 본 fixture 를 입력으로 Section 6 검증 5 항목을 모두 통과해야 한다.

## 7. E2E 스모크 테스트

두 파일로 분리해 lock-in 한다 (**owner: B**).

| 파일 | 역할 |
|---|---|
| `tests/e2e/test_phase1_to_phase2_smoke.py` | in-memory 픽스처로 Section 4 매핑 규칙 (top-N tie-breaker, self-follow drop, unused-field 보존 등) 단위 검증 |
| `tests/e2e/test_phase1_to_phase2_json_smoke.py` | 실제 디스크 JSON (`tests/data/sample_ontology_*.json`) → Pydantic round-trip → 매핑 → `StateStore` 결정성 |

공통 매핑 helper 는 `tests/e2e/_phase1_to_phase2_helpers.py` 에 분리해 둔다.
`OntologyLoader` (Issue #13, owner=C) 머지 후 helper 모듈과 본 import 를
삭제하고 호출부를 ``OntologyLoader`` 메서드로 교체한다.

## 8. 후속 마일스톤 (post-MVP)

| 항목 | 트리거 | owner |
|---|---|---|
| `MemoryConfig.retrieval_max` 동적 반영 | Phase 3 분석 결과 | C |
| Phase 2 → Phase 1 메모리 갱신 (`update_memory` API) | 멀티 라운드 학습 도입 | A + B |
| `OntologyA.ontology.topic_hierarchy` → FeedEngine 가중치 | 토픽 다이버시티 실험 | B |
| Episodic memory retrieval-on-demand | ActionSelector 컨텍스트 확장 | C |
| `ideology` → SocialGraph homophily | 양극화 실험 | B |

### 8.1 Loader 통합 E2E 시나리오 (Issue #13 머지 직후)

본 PR 의 helper 기반 스모크가 lock-in 한 모든 케이스를 `OntologyLoader` 호출로
재실행해 회귀 없음을 증명한다. **owner: A** (helper → Loader 치환 + 본 Section 7
테스트 두 파일 갱신).

```
1. Loader.load(Path("tests/data/sample_ontology_a.json"),
                Path("tests/data/sample_ontology_b.json"))
   → 검증 통과 + `OntologyA`, `OntologyB` 반환
2. Loader.build_agents(...)             → Section 4.1 매핑과 동일 결과
3. Loader.build_social_graph(...)       → Section 4.3 매핑과 동일 결과
4. StateStore(...) + AgentScheduler 결정성 (round 0/1 2 회 비교)
5. validate_consistency() warning 카운트 == 0 (sample fixture 기준)
```

### 8.2 `quick` 프리셋 활성률 측정 스니펫

`mean(post_rate)` 가 Section 3.1 의 `activation_rate` 권장 범위 (0.05~0.7) 안에 들고
quick 프리셋 전체 평균이 합리적인지 (~0.3 전후) 점검한다. **owner: B**, Phase 1
quick 실행이 처음으로 통과하는 시점에 1 회 실행해 결과를 issue 코멘트로 남긴다.

```python
from statistics import mean
from pathlib import Path
from litemiro.phase1.models import OntologyA

a = OntologyA.model_validate_json(Path("ontology_a_persona.json").read_text("utf-8"))
rates = [p.behavior_tendency.post_rate for p in a.agents.values()]
print(f"n={len(rates)}  mean={mean(rates):.3f}  min={min(rates):.3f}  max={max(rates):.3f}")
```

### 8.3 페르소나–메모리 모순 hard-error 승격 기준

Section 6.5 의 warning 을 hard error 로 올리는 조건:

1. Phase 1 quick 프리셋 3 회 이상 실행하여 누적 warning 비율이 **5% 미만** 이고,
2. 발생 사례가 모두 derived agent (`origin=derived`) 또는 빈 `topics` 등 의도적
   cold start 로 설명 가능하면,
3. extracted agent 의 모순은 hard error 로 승격하고 (`origin == EXTRACTED` 한정),
   `OntologyLoader.load` 가 `ValueError` 로 거부.

승격 결정은 ADR (`docs/decisions/`) 로 별도 기록한다. **owner: C** (Loader 측
hard error 게이트) + **B** (관측 데이터 수집).

## 9. Owner 분담

| 산출물 | Owner | 상태 |
|---|---|---|
| 본 문서 (`docs/integration/phase1-2-contract.md`) | B | 본 PR |
| `OntologyLoader` 구현 + 단위 테스트 | **C (배강민)** | GitHub Issue 발급 예정 |
| E2E 스모크 테스트 (helper 버전) | B | 본 PR (후속 커밋) |
| E2E 스모크 테스트 (Loader 통합) | A | C 구현 후 |
| Phase 1 모델 변경 (필요 시) | Younkyum | 본 문서 리뷰로 합의 |

## 10. 참조

- Phase 1 모델: `src/litemiro/phase1/models.py`
- Phase 2 Agent: `src/litemiro/models.py`
- StateStore: `src/litemiro/core/state_store.py`
- SocialGraph: `src/litemiro/social/graph.py`
- AgentScheduler (소비자): `src/litemiro/core/agent_scheduler.py`
- 스키마 로더: `src/litemiro/schemas/__init__.py`
- 컨텍스트 빌더 (라운드 예시): `src/litemiro/core/context_builder.py`
- Sample fixture: `tests/data/sample_ontology_a.json`, `tests/data/sample_ontology_b.json`
- E2E 스모크: `tests/e2e/test_phase1_to_phase2_smoke.py`,
  `tests/e2e/test_phase1_to_phase2_json_smoke.py`,
  `tests/e2e/_phase1_to_phase2_helpers.py`
