"""``litemiro-api`` CLI 진입점 — uvicorn 으로 FastAPI 앱을 띄운다.

step 1 의 runner 는 의도적으로 **즉시 완료되는 no-op**. step 2 에서 실
``run_simulation`` 어댑터로 교체. ``LITEMIRO_API_REAL_RUNNER=1`` 같은 env
flag 는 step 4 까지 도입 안 함 — fake 와 real 사이의 매끄러운 분리가
없으면 이후 단계 PR 이 본 파일을 만질 일이 없게 한다.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import TYPE_CHECKING

from litemiro.api.app import create_app

if TYPE_CHECKING:
    from pathlib import Path

    from litemiro.api.store import ProgressCallback


async def _noop_runner(
    *,
    plaza_id: str,
    ontology_a_path: Path,
    ontology_b_path: Path,
    rounds: int,
    on_progress: ProgressCallback,
) -> None:
    """step 1 placeholder: 라운드만큼 잠깐 sleep 하고 진행률을 채운다.

    프론트와 손 맞춰보려고 progress polling 동작은 살려둔다. 실 시뮬레이션
    실행은 step 2 에서 ``run_simulation`` 으로 교체.
    """
    del plaza_id, ontology_a_path, ontology_b_path
    for r in range(rounds):
        await asyncio.sleep(0)
        on_progress(rounds_done=r + 1)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="litemiro-api", description="Litemiro HTTP API server")
    parser.add_argument("--host", default=os.environ.get("LITEMIRO_API_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("LITEMIRO_API_PORT", "8765"))
    )
    parser.add_argument(
        "--cors-origin",
        action="append",
        default=None,
        help="허용 CORS origin (반복 가능). 기본: http://localhost:5173",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    # uvicorn 은 ``[api]`` extra 에서만 들어오므로 main 안에서 import 한다 — fastapi
    # 만 깔린 테스트 환경에서도 모듈 import 가 깨지지 않도록.
    import uvicorn  # noqa: PLC0415

    args = _parse_args(argv)
    origins = tuple(args.cors_origin) if args.cors_origin else ("http://localhost:5173",)
    app = create_app(runner=_noop_runner, cors_origins=origins)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
