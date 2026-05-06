"""``litemiro-validate`` — RoundEvent JSONL contract checker.

Walks a JSONL file line by line; reports the first failure on each line
to stderr and exits non-zero if any line violates the schema.

Used both by developers locally and by CI as the gate that every Phase 2
JSONL artefact must clear before Phase 3 ingests it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator, FormatChecker

from litemiro.schemas import round_event_schema


def load_schema(path: Path | None = None) -> dict[str, Any]:
    """Load ``path`` if given, else the bundled RoundEvent schema."""
    if path is None:
        schema = round_event_schema()
    else:
        with path.open(encoding="utf-8") as fh:
            schema = json.load(fh)
    Draft7Validator.check_schema(schema)
    return schema


def iter_jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    with path.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                msg = f"line {lineno}: invalid JSON ({exc.msg})"
                raise SystemExit(msg) from None
            if not isinstance(payload, dict):
                raise SystemExit(f"line {lineno}: top-level value must be a JSON object")
            yield lineno, payload


def validate_file(jsonl_path: Path, schema_path: Path | None = None) -> int:
    """Validate every non-blank JSONL line; return 0 on success, 1 otherwise."""
    schema = load_schema(schema_path)
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    failed_lines = 0
    total = 0
    for lineno, payload in iter_jsonl(jsonl_path):
        total += 1
        errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path))
        if not errors:
            continue
        failed_lines += 1
        for err in errors:
            location = "/".join(map(str, err.absolute_path)) or "<root>"
            print(f"line {lineno}: {location}: {err.message}", file=sys.stderr)
    print(
        f"{total - failed_lines}/{total} lines valid",
        file=sys.stderr,
    )
    return 0 if failed_lines == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="litemiro-validate", description=__doc__)
    parser.add_argument("--jsonl", required=True, type=Path, help="JSONL file to check")
    parser.add_argument(
        "--schema",
        type=Path,
        default=None,
        help="JSON Schema file (default: bundled RoundEvent schema)",
    )
    args = parser.parse_args(argv)
    return validate_file(args.jsonl, args.schema)


if __name__ == "__main__":  # pragma: no cover - module is a CLI
    raise SystemExit(main())
