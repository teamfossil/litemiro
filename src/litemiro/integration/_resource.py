"""``run_simulation`` 종료 시점 RAM/SSD 사용량 측정 헬퍼.

기기 스펙 결정 (실험 노트북 vs 워크스테이션) 에 들어가는 단일 신호 — peak
RSS + output 디렉토리 트리 사이즈. 외부 의존 (psutil 등) 없이 표준 라이브러리
``resource`` / ``pathlib`` 만 쓴다.

macOS (Darwin BSD) 와 Linux 의 ``ru_maxrss`` 단위가 다르다 — Darwin 은 bytes,
Linux 는 KiB. 호출자가 OS 분기를 다시 하지 않도록 본 헬퍼가 항상 bytes 로 정규화.
"""

from __future__ import annotations

import resource
import sys
from pathlib import Path


def peak_rss_bytes() -> int:
    """현 프로세스의 peak resident set size — bytes 단위.

    ``resource.getrusage(RUSAGE_SELF).ru_maxrss`` 는 OS 마다 단위가 갈린다.
    Darwin/BSD 는 bytes, GNU/Linux 는 KiB. 호출자 측 분기를 막으려 본 함수에서
    OS 판별 후 항상 bytes 로 반환.

    Windows 처럼 ``resource`` 가 없는 플랫폼은 모듈 import 자체가 실패하므로
    여기까지 들어오지 않는다 — 본 헬퍼는 POSIX 만 지원.
    """
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return int(raw)
    return int(raw) * 1024


def directory_size_bytes(path: Path) -> int:
    """``path`` 트리의 모든 정규 파일 크기 합. symlink 는 미포함.

    ``run_simulation`` 종료 시 ``event_log_path.parent`` 를 넘기면 ``events.jsonl``
    + ``checkpoints/`` 가 한 번에 잡힌다. 존재하지 않는 경로면 0 — Phase 3 가
    측정 실패와 0-byte 산출물을 구분할 수 있게 호출자가 별도 판단할 책임.
    """
    if not path.exists():
        return 0
    total = 0
    for entry in path.rglob("*"):
        # ``is_file`` 만으로는 symlink 가 가리키는 외부 파일까지 합산되어
        # output_dir 가 아닌 영역의 사이즈가 섞일 위험 — 명시적으로 제외.
        if entry.is_symlink():
            continue
        if entry.is_file():
            total += entry.stat().st_size
    return total


__all__ = ["directory_size_bytes", "peak_rss_bytes"]
