"""Phase 1 — OntologyA / OntologyB JSON serializer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from jsonschema import Draft7Validator, FormatChecker

from litemiro.phase1.models import OntologyA, OntologyB
from litemiro.schemas import ontology_a_schema, ontology_b_schema

log = structlog.get_logger(__name__)

_FILENAME_A = "ontology_a_persona.json"
_FILENAME_B = "ontology_b_memory.json"


class OntologySerializer:
    def serialize_a(self, ontology_a: OntologyA) -> str:
        data = ontology_a.model_dump(mode="json")
        return json.dumps(data, ensure_ascii=False, indent=2)

    def serialize_b(self, ontology_b: OntologyB) -> str:
        data = ontology_b.model_dump(mode="json")
        return json.dumps(data, ensure_ascii=False, indent=2)

    def write(
        self, ontology_a: OntologyA, ontology_b: OntologyB, output_dir: Path
    ) -> tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)

        path_a = output_dir / _FILENAME_A
        path_b = output_dir / _FILENAME_B

        data_a = ontology_a.model_dump(mode="json")
        data_b = ontology_b.model_dump(mode="json")
        schema_errors = [
            *(
                f"ontology_a: {message}"
                for message in self.validate_against_schema(data_a, "ontology_a")
            ),
            *(
                f"ontology_b: {message}"
                for message in self.validate_against_schema(data_b, "ontology_b")
            ),
        ]
        if schema_errors:
            raise ValueError("ontology schema validation failed: " + "; ".join(schema_errors))

        text_a = json.dumps(data_a, ensure_ascii=False, indent=2)
        text_b = json.dumps(data_b, ensure_ascii=False, indent=2)

        path_a.write_text(text_a, encoding="utf-8")
        log.info("wrote_ontology_a", path=str(path_a))

        path_b.write_text(text_b, encoding="utf-8")
        log.info("wrote_ontology_b", path=str(path_b))

        return path_a, path_b

    def validate_against_schema(self, data: dict[str, Any], schema_name: str) -> list[str]:
        if schema_name == "ontology_a":
            schema = ontology_a_schema()
        elif schema_name == "ontology_b":
            schema = ontology_b_schema()
        else:
            return [f"unknown schema_name: {schema_name!r}"]

        validator = Draft7Validator(schema, format_checker=FormatChecker())
        errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
        messages: list[str] = []
        for err in errors:
            location = "/".join(map(str, err.absolute_path)) or "<root>"
            messages.append(f"{location}: {err.message}")
        return messages


__all__ = ["OntologySerializer"]
