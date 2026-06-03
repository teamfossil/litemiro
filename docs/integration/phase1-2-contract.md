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
5. **페르소나–메모리 모순 검출** — `OntologyLoader.validate_consistency(*, ontology_a,
   ontology_b, embedder=None, similarity_threshold=0.4, raise_on_extracted_mismatch=False)`
   가 각 `agent_id` 에 대해 페르소나 토픽 묶음과 `SemanticMemory.topics` 합집합을
   비교한다. `embedder` 가 주어지면 (**옵션 B**, `#58`) 두 묶음을 임베딩 후
   **max pairwise cosine** 이 `similarity_threshold` 미만이면 warning — 페르소나
   (LLM 추상 개념) 와 메모리 (NER 엔티티) 의 어휘공간이 달라도 의미 매칭이 잡힌다.
   `embedder=None` 은 legacy set intersection 경로 (단위 테스트가 모델 로딩 없이
   돌게 두는 백워드 호환). `raise_on_extracted_mismatch=True` 면 §8.4 의
   hard-error 게이트가 켜진다 — `embedder` + `origin=EXTRACTED` 조합에서 미만 시
   `ValueError`. `derived` 는 메모리 토픽 결정 시퀀스가 미정착이라 warning 유지.
   **빈 `semantic` 리스트는 warning 면제** (cold start). `ConsistencyWarning.
   max_similarity` 는 임베딩 경로에서만 채워지며 threshold calibration 시 분포
   관측에 쓴다. `run_simulation` 은 `FeedEngine` 용으로 이미 인스턴스화된 embedder
   를 재사용해 모델 로딩이 한 번에 묶이게 하고, `raise_on_extracted_mismatch=True`
   를 켜서 production 게이트로 동작.

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

`OntologyLoader` (Issue #13) 머지 후 임시 helper 모듈
(`tests/e2e/_phase1_to_phase2_helpers.py`) 은 삭제되었고 두 스모크의
호출부는 ``OntologyLoader`` 메서드로 교체되었다 (Section 8.1, owner=A).

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

### 8.3 `activation_rate=0.5` 가정 출처 / 민감도

§4.1 비용 가정의 `activation_rate=0.5` 는 **임의값**. OASIS (arXiv:2411.11581)
는 평균 활성률을 단일 상수로 명시하지 않고 에이전트별 24-시간 활동 확률 벡터로
샘플링하며, 그 안의 misinformation 실험에서는 core user `0.1` / regular user
`0.01` 로 우리 기본값보다 5~50 배 낮다. `0.5` 는 OASIS 인용이 아니라 quick
프리셋이 한 라운드에 의미 있는 행동량을 만들도록 잡은 시뮬레이션 기본 가정.

측정 추세 (`#21`):

| 차수 | 산출 | `mean(post_rate)` | 가드 (`±0.1`) |
|---|---|---|---|
| 1 차 (`#54` 머지 전) | seed 42/43/44 | 0.481 | 통과 |
| 2 차 (`#54` 머지 후) | seed 42/43/44 | 0.408 | 끝부근 |

`mean(post_rate)` 가 0.3 이하로 떨어지면 가드 이탈 — 비용 표 재산정 필수.

quick 1 회 토큰 비용을 `C(rate)` 라 할 때 (라운드 수 / 에이전트 수 / 토큰 단가
고정), `C` 가 활성률에 선형 비례한다는 가정 하에 표 갱신은 다음 비율로:

| `activation_rate` | 비용 비율 (`0.5` 기준) | 비고 |
|---|---|---|
| 0.3 | ×0.6 | post_rate 2 차 측정치 부근 |
| 0.5 | ×1.0 | 현 기본 가정 |
| 0.7 | ×1.4 | quick 권장 범위 상단 |
| 1.0 | ×2.0 | 모든 에이전트가 매 라운드 행동 (이론 상한) |

발표 / Q&A 에서 "왜 0.5?" 질문이 나오면: **(a) OASIS 인용 아님**, **(b) quick
프리셋 활성률 측정값이 ±0.1 가드 안**, **(c) 가드 이탈 시 위 비율표로 재산정**
3 점을 답변한다. (`#60`)

### 8.4 페르소나–메모리 모순 hard-error 승격 (2026-05-28 결정)

`docs/decisions/0001-persona-memory-cosine-threshold.md` 의 calibration 측정으로
승격 완료. 핵심 결과 (3 seed × 100 agents quick, active 69):

| 경로                          | warning | rate     |
| ----------------------------- | ------- | -------- |
| legacy (set intersection)     | 54 / 69 | **78.3%** |
| 옵션 B cosine, threshold=0.40 | 0 / 69  | **0.0%**  |

옵션 B 의 cosine 분포는 min=0.574 / p50=0.743 / max=1.000 / mean=0.781 이라
threshold=0.40 과 마진 0.17 이상. threshold sweep 으로 0.55 까지 warning 0건,
0.60 에서 5건 (7.2%) 발생. 디폴트 `similarity_threshold=0.40` 유지가 안전.

승격 형태:

1. `OntologyLoader.validate_consistency(..., raise_on_extracted_mismatch=True)` 가
   `embedder` + `origin=EXTRACTED` 조합에서 threshold 미만 시 `ValueError`. opt-in
   디폴트는 `False` 라 측정 / 단위 테스트 / 디버깅 호출은 분포만 받고 backward-
   compat. production 진입점 `integration/run.py:run_simulation` 만 `True` 로 켠다.
2. `derived` 에이전트는 보수적으로 warning 유지 — 메모리 토픽 결정 시퀀스가 추상
   개념 공간으로 정착되기 전 단계라, derived 까지 hard-error 확대는 별도 측정
   후로 분리.
3. 부수 fix — `_cosine` 결과를 `[-1, 1]` 로 clamp. 부동소수 누적 오차
   (`1.0000000000000002`) 가 `ConsistencyWarning.max_similarity` 의 `Field(le=1.0)`
   를 깨던 production ValidationError 의 근본 fix.

calibration 재실행: `uv run --extra embedding python scripts/measure_persona_memory
_cosine.py runs/persona-mem-remeasure`. 입력 ontology 는 `runs/` (gitignore) 또는
`litemiro-ontology --preset quick --seed {42,43,44}` 로 재생성.

**owner: B** (관측 + threshold 민감도 + opt-in 게이트 구현) + **C** (Loader hard-
error 경계 합의). 한계 / 후속 (단일 도메인 코퍼스, cos=1.000 군집 추적, derived
확대) 은 ADR-0001 후속 절에.

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
  `tests/e2e/test_phase1_to_phase2_json_smoke.py`
- OntologyLoader: `src/litemiro/integration/ontology_loader.py`

## 11. 부록 — 한국어 페르소나 템플릿 예시

발표 / 최종 보고서에서 "한국 환경 적합" 이 추상적으로만 박혀 있다는 지적 (`#62`)
에 대한 구체 예시. `tests/data/sample_ontology_a.json` 에 들어 있는 3-agent
quick fixture (Journalist / Academic / Citizen) 외에 풀 100-agent quick 산출에서
실제로 생성된 derived citizen 한 명을 그대로 인용한다 (`seed=42`):

```json
{
  "agent_id": "agent_0097",
  "name": "시민_agent_0097",
  "entity_type": "citizen",
  "origin": "derived",
  "ideology": 0.23,
  "topics": ["AI 규제 정책에 대한 이해관계자 입장 시뮬레이션"],
  "sensitive_topics": [
    "사용자 경험 저해",
    "규제로 인한 서비스 접근성 감소"
  ],
  "personality": "실용적이고 유연한 문제 해결자. 정책이 실제 생활에 어떻게 작동하는지에 집중.",
  "speech_style": "친근하고 대화체 중심. '~해보면 어떨까', '우리가 직접 체험해본 건…' 같은 표현 선호.",
  "background": "경기 지역에서 UX 디자이너 겸 AI 교육 콘텐츠 제작자로 활동 중인 30대 프리랜서.",
  "behavior_tendency": {
    "post_rate": 0.38,
    "reply_rate": 0.69,
    "repost_rate": 0.55,
    "controversy_affinity": 0.45
  },
  "initial_following": ["agent_0034", "agent_0038", "etri",
    "seoul_national_university_ai_research_center", "..."]
}
```

**왜 한국 환경 적합으로 보는가** — `background` 가 "경기 지역 / 30대 프리랜서 /
UX 디자이너" 처럼 한국 디지털 노동시장의 실 직군과 지역 분포를 담고 있고,
`speech_style` 이 영어 번역체가 아닌 구어체 한국어 (`"~해보면 어떨까"`) 로 잡혀
있다. `initial_following` 의 `etri` /
`seoul_national_university_ai_research_center` 처럼 OntologyA Entity extraction
이 뽑아낸 한국 실재 기관이 derived 시민의 팔로우 그래프에 자연스럽게 섞이는 게
"로컬 컨텍스트" 의 코어. (한 명 → 100 명 산출 전체에서 같은 패턴이 반복된다.)

`ideology=0.23` 은 진보 성향, `controversy_affinity=0.45` 는 양극단을 회피하는
중도-경향. 이 분포가 `behavior_tendency.post_rate` 와 곱해져 `#21` 의
`mean(post_rate)=0.408` 측정치를 만든다.
