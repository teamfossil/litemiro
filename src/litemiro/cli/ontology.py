"""``litemiro-ontology`` — Phase 1 pipeline CLI entry point."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import structlog
from dotenv import load_dotenv

from litemiro.phase1.models import Preset
from litemiro.phase1.pipeline import OntologyPipeline, PipelineConfig

log = structlog.get_logger(__name__)


class Phase1LiteLLMClient:
    async def complete(self, *, system: str, user: str, model: str) -> str:
        import litellm  # noqa: PLC0415

        response = await litellm.acompletion(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return str(response.choices[0].message.content or "")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="litemiro-ontology",
        description="Run the Phase 1 ontology generation pipeline.",
    )
    parser.add_argument("--input", required=True, type=Path, help="Path to document (PDF or text)")
    parser.add_argument("--requirement", required=True, help="Simulation requirement string")
    parser.add_argument(
        "--preset",
        default="quick",
        choices=["quick", "standard", "full"],
        help="Agent count preset (default: quick)",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="Output directory (default: current directory)",
    )
    parser.add_argument(
        "--model",
        default="openrouter/qwen/qwen-plus",
        help="LLM model identifier (default: openrouter/qwen/qwen-plus)",
    )

    args = parser.parse_args(argv)

    config = PipelineConfig(
        input_path=args.input,
        requirement=args.requirement,
        preset=Preset(args.preset),
        seed=args.seed,
        output_dir=args.output_dir,
        model=args.model,
    )

    llm = Phase1LiteLLMClient()
    t_start = time.monotonic()

    try:
        ontology_a, _ontology_b = asyncio.run(OntologyPipeline(config, llm).run())
    except Exception as exc:
        log.error("pipeline_failed", error=str(exc))
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    elapsed = time.monotonic() - t_start

    path_a = args.output_dir / "ontology_a_persona.json"
    path_b = args.output_dir / "ontology_b_memory.json"

    print(f"Agents generated : {ontology_a.agent_count}")
    print(f"OntologyA written: {path_a}")
    print(f"OntologyB written: {path_b}")
    print(f"Elapsed          : {elapsed:.1f}s")

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
