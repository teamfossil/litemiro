"""``litemiro.integration._resource`` — peak RSS / output dir size 헬퍼 단위.

본 헬퍼는 ``run_simulation`` 종료 시 단 1회 호출되는 경로라 실패 시 시뮬 결과
자체가 (필드 0 으로) 미관측처럼 보이게 된다. 단위 테스트로 OS 분기 / 트리
합산 / symlink 제외 / 누락 경로 처리 4지점을 lock-in.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from litemiro.integration._resource import directory_size_bytes, peak_rss_bytes


def test_peak_rss_bytes_is_positive() -> None:
    """현 프로세스가 적어도 인터프리터 자체로 메모리를 점유하므로 0 보다 커야 한다.

    실 값은 macOS/Linux 단위 차이를 본 헬퍼가 흡수한 뒤 bytes 로 통일된 값.
    절댓값이 아니라 "양수" 만 확인 — 호출자가 보고 형식에서 단위 변환할 책임.
    """
    assert peak_rss_bytes() > 0


def test_directory_size_bytes_missing_path_returns_zero(tmp_path: Path) -> None:
    """존재하지 않는 경로는 0 — 측정 실패와 0-byte 산출의 구분은 호출자 책임."""
    assert directory_size_bytes(tmp_path / "does-not-exist") == 0


def test_directory_size_bytes_empty_directory_returns_zero(tmp_path: Path) -> None:
    assert directory_size_bytes(tmp_path) == 0


def test_directory_size_bytes_sums_files_recursively(tmp_path: Path) -> None:
    """루트 + 하위 디렉토리 파일 사이즈 합산."""
    (tmp_path / "a.txt").write_bytes(b"x" * 10)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_bytes(b"y" * 25)

    assert directory_size_bytes(tmp_path) == 35


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink only")
def test_directory_size_bytes_ignores_symlinks(tmp_path: Path) -> None:
    """심볼릭 링크는 합산 제외 — output_dir 밖 영역 사이즈가 섞이는 위험 회피."""
    target = tmp_path / "target.txt"
    target.write_bytes(b"abc")
    link = tmp_path / "link.txt"
    os.symlink(target, link)

    # target (3 byte) 만 카운트 — link 가 target 을 가리키지만 별도로 더하지 않음.
    assert directory_size_bytes(tmp_path) == 3
