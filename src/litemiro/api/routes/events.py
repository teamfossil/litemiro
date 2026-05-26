"""``GET /api/plazas/{id}/events`` — SSE 진행률 스트림.

step 3 의 핵심 — 프론트가 ``/status`` 를 폴링하지 않고 라운드 단위 progress
와 상태 머신 전환을 push 로 받는다.

페이로드 두 종:

- ``event: progress`` ``data: {rounds_done, rounds_total}`` — 라운드 1건 종료
- ``event: status``  ``data: {status, rounds_done, rounds_total, error}`` —
  pending→running, running→completed|failed 전환 시. ``status`` 가 terminal
  이면 본 이벤트가 스트림의 마지막이다.

라운드 단위 액션 스트림(events.jsonl 라이브 tail) 은 본 단계가 아님 —
step 3b 또는 step 4 에서 동일 라우트에 ``event: action`` 으로 얹는다.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from litemiro.api.store import PlazaEvent, PlazaStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

router = APIRouter(prefix="/api/plazas", tags=["plazas"])

_TERMINAL_STATUSES = {"completed", "failed"}
# 큐 폴링 timeout. 너무 길면 disconnect 감지가 늦고, 너무 짧으면 keepalive 가
# 잦아져서 의미가 없다. 15 초면 일반 proxy idle timeout(보통 60s) 안쪽.
_KEEPALIVE_INTERVAL_SECONDS = 15.0


def _store(request: Request) -> PlazaStore:
    s = getattr(request.app.state, "plaza_store", None)
    if s is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="plaza store not initialised",
        )
    return s  # type: ignore[no-any-return]


def _format_sse(event: PlazaEvent) -> str:
    """SSE wire 포맷 — ``event:`` 라벨 + ``data:`` JSON + 빈 줄."""
    payload = json.dumps(event.data, separators=(",", ":"), ensure_ascii=False)
    return f"event: {event.type}\ndata: {payload}\n\n"


@router.get("/{plaza_id}/events")
async def stream_events(plaza_id: str, request: Request) -> StreamingResponse:
    store = _store(request)
    record = await store.get(plaza_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"plaza {plaza_id!r} not found",
        )

    queue = await store.subscribe(plaza_id)
    if queue is None:
        # subscribe 와 get 사이 race — record 가 사라진 경우 (현재 store 는 삭제
        # API 가 없어서 사실상 도달 불가지만 미래의 prune 을 대비).
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"plaza {plaza_id!r} not found",
        )

    async def event_stream() -> AsyncIterator[str]:
        try:
            # 초기 status 한 번 yield — 폴링 없이도 현재 상태가 즉시 알려진다.
            # 이 직후 _drive 의 running→terminal 전환 이벤트는 큐로 들어온다.
            yield _format_sse(
                PlazaEvent(
                    type="status",
                    data={
                        "status": record.status,
                        "rounds_done": record.rounds_done,
                        "rounds_total": record.rounds_total,
                        "error": record.error,
                    },
                )
            )

            if record.status in _TERMINAL_STATUSES:
                return

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_INTERVAL_SECONDS)
                except TimeoutError:
                    # 큐가 한동안 비었으면 disconnect 확인 후 keepalive.
                    # ``: ...`` 는 SSE comment — 클라 onmessage 에 안 잡힌다.
                    if await request.is_disconnected():
                        return
                    yield ": keepalive\n\n"
                    continue
                yield _format_sse(event)
                if event.type == "status" and event.data.get("status") in _TERMINAL_STATUSES:
                    return
        finally:
            await store.unsubscribe(plaza_id, queue)

    # nginx 등 reverse proxy 는 기본으로 응답을 버퍼링해서 SSE 가 모이는 즉시
    # 전달되지 않는다. ``X-Accel-Buffering: no`` 로 nginx 를, ``Cache-Control:
    # no-cache`` 로 중간 캐시를 끈다 (CDN/브라우저 캐시 포함).
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


__all__ = ["router"]
