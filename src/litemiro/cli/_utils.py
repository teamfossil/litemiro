"""Shared helpers for command-line entry points."""

from __future__ import annotations

import argparse


def positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value < 1:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return value


__all__ = ["positive_int"]
