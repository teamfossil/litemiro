# Phase 1 → Phase 2 통합 계약 (MVP)

## 0. 목적

Phase 1(듀얼 온톨로지 생성)이 산출하는 `OntologyA` / `OntologyB` JSON 산출물을
Phase 2(라운드 시뮬레이션)의 입력 객체(`Agent`, `SocialGraph`, `StateStore`)로
변환하는 단일 경계 — `OntologyLoader` — 의 동작을 고정한다.

본 문서는 **MVP 통합 1차분**의 계약만을 정의한다. 메모리 갱신 사이클, 동적 토픽
가중치 등은 §8 후속 마일스톤에서 다룬다.

## 1. 범위

| 항목 | MVP 포함 | 비고 |
|---|---|---|
| `OntologyA` → `Agent[]` 매핑 | O | §4.1 |
| `OntologyB` → `Agent.memory_summary` (top-N concat) | O | §4.2 |
| `initial_following` → `SocialGraph` | O | §4.3 |
| `OntologyLoader` API | O | §5 |
| 스키마 검증 (jsonschema) | O | §6 |
| E2E 스모크 테스트 | O | §7 |
| Phase 2 → Phase 1 메모리 갱신 | X | post-MVP (§8) |
| 동적 `MemoryConfig` 반영 | X | post-MVP (§8) |
| `OntologyA.ontology.topic_hierarchy` 활용 | X | post-MVP (§8) |

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

→ self-follow는 `ValueError`. `OntologyLoader`가 사전 필터링한다(§4.3).

## 4. 매핑 규칙

### 4.1 `AgentProfile` → `Agent`

| Phase 2 필드 | 출처 | 변환 |
|---|---|---|
| `agent_id` | `AgentProfile.agent_id` | passthrough |
| `interests` | `AgentProfile.topics` | `tuple(...)` |
| `persona_traits` | `AgentProfile` 전체 dict | `model_dump(mode="json")` |
| `memory_summary` | `OntologyB.stores[agent_id].semantic` | §4.2 알고리즘 |
| `activation_rate` | `AgentProfile.behavior_tendency.post_rate` | passthrough |

`persona_traits`에는 `AgentProfile.model_dump(mode="json")` 전체를 넣는다
(MVP 미사용 필드 보존, 후속 단계 확장 여지 확보).

> **비용 가정 (검증 의무)**: Phase 0/3 비용 추정 표는 `activation_rate=0.5`
> 평균 가정에 의존한다. 본 매핑은 에이전트별 `post_rate` 를 그대로 사용하므로,
> Phase 1 산출의 `mean(post_rate)` 가 비용 표 가정에서 ±0.1 이상 벗어나면 비용
> 표 재산정 필요. **quick 첫 실행 시
> `statistics.fmean(a.activation_rate for a in agents)` 로 측정**하여 차이가 크면
> §8 후속 마일스톤에서 다룬다. (Phase 1 `ProfileGenerator` 가 어떤 prior 로
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
        key=lambda m: (-m.simulation_count, -m.last_relevant_sim),
    )
    top_n = sorted_mem[:N]
    memory_summary = "; ".join(m.summary for m in top_n)
```

- **N=3 근거**: `MemoryConfig.retrieval_max` 기본값과 일치. post-MVP에서 동적화.
- **정렬 기준**: `simulation_count desc` (자주 회상된 기억 우선) →
  동률 시 `last_relevant_sim desc` (최근에 회상된 기억 우선).
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
        """§4.3 매핑."""
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

## 7. E2E 스모크 테스트

`tests/e2e/test_phase1_to_phase2_smoke.py` (신규, **owner: B**).

```
- fixture: 3-agent OntologyA + 3-agent OntologyB JSON (소규모)
- OntologyLoader.load → build_agents → build_social_graph
- StateStore 생성
- AgentScheduler.select_active(round_num=0) 호출 → 결정적 결과
- 동일 입력 2회 실행 → 결과 동일성 검증
```

`OntologyLoader` 미구현 단계에서는 **inline helper**로 임시 매핑을 작성하여
계약을 미리 lock-in. C 구현 완료 후 helper를 제거하고 진짜 Loader로 교체.

## 8. 후속 마일스톤 (post-MVP)

| 항목 | 트리거 | owner |
|---|---|---|
| `MemoryConfig.retrieval_max` 동적 반영 | Phase 3 분석 결과 | C |
| Phase 2 → Phase 1 메모리 갱신 (`update_memory` API) | 멀티 라운드 학습 도입 | A + B |
| `OntologyA.ontology.topic_hierarchy` → FeedEngine 가중치 | 토픽 다이버시티 실험 | B |
| Episodic memory retrieval-on-demand | ActionSelector 컨텍스트 확장 | C |
| `ideology` → SocialGraph homophily | 양극화 실험 | B |

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
