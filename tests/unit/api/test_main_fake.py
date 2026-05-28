"""``--fake`` 모드 백엔드 helper 들의 단위 테스트.

실 API end-to-end (``POST /api/plazas`` → /report 까지) 는 ``test_plazas`` /
``test_ontologies`` 가 다룬다. 여기서는 ``__main__`` 안의 세 noop 컬어블이
LLM 키 없이 닫히고 (1) Seed→Ontology 가 fixture 로 채워지고, (2) /report
가 0/0/0 으로 떨어지지 않고, (3) markdown 자리가 stub 으로 채워지는지를
직접 잡는다.
"""

from __future__ import annotations

from pathlib import Path

from litemiro.api.__main__ import (
    _noop_composer,
    _noop_ontology_runner,
    _noop_runner,
)
from litemiro.api.sample_fixtures import (
    DEFAULT_ONTOLOGY_A_PATH,
    DEFAULT_ONTOLOGY_B_PATH,
)
from litemiro.models import ActionType, RoundEvent
from litemiro.phase1.models import Preset


async def test_noop_ontology_runner_copies_default_fixtures(tmp_path: Path) -> None:
    out = tmp_path / "ontology-xyz"
    progressed: list[tuple[str, str | None]] = []

    def _record(step: str, fallback_model: str | None) -> None:
        progressed.append((step, fallback_model))

    result = await _noop_ontology_runner(
        document_path=tmp_path / "ignored.txt",
        requirement="fake requirement",
        preset=Preset.QUICK,
        output_dir=out,
        on_progress=_record,
    )
    assert result.ontology_a_path == out / "ontology_a_persona.json"
    assert result.ontology_b_path == out / "ontology_b_memory.json"
    # 원본 fixture 와 byte-for-byte 동일 — frontend 가 보는 agent 풀이 ``/agents``
    # 와 ``/report`` 양쪽에서 같은 ontology 로 일관되게 흘러야 한다.
    assert result.ontology_a_path.read_bytes() == DEFAULT_ONTOLOGY_A_PATH.read_bytes()
    assert result.ontology_b_path.read_bytes() == DEFAULT_ONTOLOGY_B_PATH.read_bytes()
    assert result.agent_count >= 1
    # #126: fake runner 도 실 pipeline 의 7 step 시퀀스를 그대로 콜백으로
    # 흘려 프론트 progress UI 가 fake 모드에서 동작 확인 가능해야 한다.
    assert [step for step, _ in progressed] == [
        "step0_document",
        "step1_ontology",
        "step2_graph",
        "step3_seeds",
        "step4_profiles",
        "step5_memory",
        "step6_serialize",
    ]
    assert all(model is None for _, model in progressed)


async def test_noop_runner_writes_synthetic_events(tmp_path: Path) -> None:
    event_log = tmp_path / "events.jsonl"
    progressed: list[int] = []

    def _record(*, rounds_done: int) -> None:
        progressed.append(rounds_done)

    await _noop_runner(
        plaza_id="plz-fake",
        ontology_a_path=DEFAULT_ONTOLOGY_A_PATH,
        ontology_b_path=DEFAULT_ONTOLOGY_B_PATH,
        rounds=2,
        event_log_path=event_log,
        checkpoint_dir=tmp_path / "ckpt",
        on_progress=_record,
    )
    assert progressed == [1, 2]
    assert event_log.exists(), "fake runner 가 events.jsonl 을 비워두면 /report 가 0/0/0 (#2 회귀)"

    lines = event_log.read_text(encoding="utf-8").splitlines()
    assert lines, "비어있는 jsonl 은 회귀"
    events = [RoundEvent.model_validate_json(line) for line in lines]
    # 6 종이 모두 한 번 이상 나와야 /report 의 카테고리 분포 / follower flow / hot post
    # 가 한쪽으로 쏠리지 않는다.
    seen = {e.action.type for e in events}
    assert seen == {
        ActionType.CREATE_POST,
        ActionType.LIKE_POST,
        ActionType.REPOST,
        ActionType.QUOTE_POST,
        ActionType.FOLLOW,
        ActionType.DO_NOTHING,
    }
    assert {e.round_num for e in events} == {0, 1}


async def test_noop_composer_returns_stub_when_events_exist(tmp_path: Path) -> None:
    event_log = tmp_path / "events.jsonl"
    await _noop_runner(
        plaza_id="plz-fake",
        ontology_a_path=DEFAULT_ONTOLOGY_A_PATH,
        ontology_b_path=DEFAULT_ONTOLOGY_B_PATH,
        rounds=1,
        event_log_path=event_log,
        checkpoint_dir=tmp_path / "ckpt",
        on_progress=lambda *, rounds_done: None,
    )
    outcome = await _noop_composer(
        plaza_id="plz-fake",
        event_log_path=event_log,
        preset=Preset.QUICK,
    )
    assert outcome.markdown is not None
    assert "fake" in outcome.markdown.lower()
    # composer 가 aggregation 을 채워야 store 가 record 에 캐싱해 /report 가
    # 빈 집계로 떨어지지 않는다.
    assert outcome.aggregation is not None
    assert outcome.aggregation.n_events > 0
    assert outcome.aggregation.n_agents > 0


async def test_noop_composer_returns_none_when_no_events(tmp_path: Path) -> None:
    # 실 composer (``RealPlazaComposer``) 의 빈 events 분기와 동일 동작 — markdown
    # 만 None 으로 비우고 통계는 호출 측이 lazy 로 채운다.
    outcome = await _noop_composer(
        plaza_id="plz-empty",
        event_log_path=tmp_path / "missing.jsonl",
        preset=Preset.QUICK,
    )
    assert outcome.markdown is None
    assert outcome.aggregation is None
