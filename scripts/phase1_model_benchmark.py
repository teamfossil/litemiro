from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import statistics
import time
from pathlib import Path
from typing import Any

import litellm
from json_repair import repair_json

from litemiro.llm.litellm_client import _extract_usage
from litemiro.phase1 import profile_generator
from litemiro.phase1.models import PRESET_AGENT_COUNTS, AgentSeed, Preset
from litemiro.phase1.pipeline import OntologyPipeline, PipelineConfig

_AGENT_ID_RE = re.compile(r"^agent_id:\s*(?P<agent_id>\S+)", re.MULTILINE)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonable_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _usage_value(usage: object, name: str) -> object:
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage.get(name)
    return getattr(usage, name, None)


def _extract_cost(response: object) -> float | None:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    cost = _jsonable_float(_usage_value(usage, "cost"))
    if cost is not None:
        return cost
    try:
        return float(litellm.completion_cost(completion_response=response))
    except Exception:
        return None


def _extract_model(response: object, requested: str) -> str:
    if isinstance(response, dict):
        return str(response.get("model") or requested)
    return str(getattr(response, "model", None) or requested)


def _extract_finish_reason(response: object) -> str | None:
    try:
        choice = response.choices[0]  # type: ignore[attr-defined]
        value = getattr(choice, "finish_reason", None)
        return None if value is None else str(value)
    except Exception:
        return None


def _stage_for_prompt(user: str) -> str:
    if "agent_id:" in user:
        return "profile"
    if "relationships" in user or "entities" in user:
        return "entity_extraction"
    return "ontology"


class MeteredLiteLLMClient:
    def __init__(
        self,
        *,
        response_format_json: bool,
        llm_seed: int | None,
        raw_dir: Path,
    ) -> None:
        self.response_format_json = response_format_json
        self.llm_seed = llm_seed
        self.raw_dir = raw_dir
        self.calls: list[dict[str, Any]] = []

    async def complete(self, *, system: str, user: str, model: str) -> str:
        call_index = len(self.calls) + 1
        stage = _stage_for_prompt(user)
        agent_ids = _AGENT_ID_RE.findall(user)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self.response_format_json:
            kwargs["response_format"] = {"type": "json_object"}
        if self.llm_seed is not None:
            kwargs["seed"] = self.llm_seed

        started = time.monotonic()
        record: dict[str, Any] = {
            "index": call_index,
            "stage": stage,
            "agent_ids": agent_ids,
            "requested_model": model,
            "response_format_json": self.response_format_json,
            "llm_seed": self.llm_seed,
        }
        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as exc:
            record.update(
                {
                    "latency_ms": round((time.monotonic() - started) * 1000, 2),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            self.calls.append(record)
            raise

        text = str(response.choices[0].message.content or "")
        raw_path: str | None = None
        if stage == "profile":
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            raw_file = self.raw_dir / f"profile_call_{call_index:03d}.txt"
            raw_file.write_text(text, encoding="utf-8")
            raw_path = str(raw_file)

        prompt_tokens, completion_tokens = _extract_usage(response)
        cost_usd = _extract_cost(response)
        record.update(
            {
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "returned_model": _extract_model(response, model),
                "finish_reason": _extract_finish_reason(response),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "cost_usd": cost_usd,
                "content_chars": len(text),
                "content_sha256": _sha256_text(text),
                "raw_path": raw_path,
            }
        )
        self.calls.append(record)
        return text


def _parse_profile_raw(raw: str) -> tuple[str, list[dict[str, object]], str | None]:
    try:
        data = json.loads(repair_json(raw))
    except Exception as exc:
        return "json_parse_failed", [], f"{type(exc).__name__}: {exc}"
    if isinstance(data, list):
        items = [item for item in data if isinstance(item, dict)]
        return "json_list", items, None
    if isinstance(data, dict):
        return "json_object", [data], None
    return f"json_{type(data).__name__}", [], None


def _profile_call_diagnostics(call: dict[str, Any]) -> dict[str, Any]:
    raw_path = call.get("raw_path")
    if not raw_path:
        return {"parse_status": "no_raw", "item_agent_ids": [], "error": None}
    raw = Path(str(raw_path)).read_text(encoding="utf-8")
    status, items, error = _parse_profile_raw(raw)
    item_agent_ids = [
        str(item.get("agent_id")) for item in items if isinstance(item.get("agent_id"), str)
    ]
    stripped = raw.strip()
    return {
        "parse_status": status,
        "item_agent_ids": item_agent_ids,
        "error": error,
        "contains_markdown_fence": "```" in raw,
        "starts_markdown_fence": stripped.startswith("```"),
        "ends_like_complete_json": stripped.endswith("]") or stripped.endswith("}"),
        "raw_chars": len(raw),
    }


def _normal_hash_for_a(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["generated_at"] = "<normalized>"
    return _sha256_text(json.dumps(data, ensure_ascii=False, sort_keys=True))


def _classify_fallbacks(
    *,
    calls: list[dict[str, Any]],
    fallback_seeds: dict[str, AgentSeed],
) -> list[dict[str, Any]]:
    profile_calls = [call for call in calls if call.get("stage") == "profile"]
    parsed_calls = {
        int(call["index"]): (
            call,
            _parse_profile_raw(Path(str(call["raw_path"])).read_text(encoding="utf-8"))
            if call.get("raw_path")
            else ("no_raw", [], None),
        )
        for call in profile_calls
    }
    results: list[dict[str, Any]] = []
    for agent_id, seed in fallback_seeds.items():
        candidates = [
            call
            for call in profile_calls
            if agent_id in {str(aid) for aid in call.get("agent_ids", [])}
        ]
        call = candidates[-1] if candidates else None
        if call is None:
            results.append({"agent_id": agent_id, "reason": "no_profile_call"})
            continue

        status, items, error = parsed_calls[int(call["index"])][1]
        matching_item = next(
            (
                item
                for item in items
                if isinstance(item.get("agent_id"), str) and item.get("agent_id") == agent_id
            ),
            None,
        )
        reason = status
        parse_error = error
        if status in {"json_list", "json_object"}:
            if matching_item is None:
                reason = "missing_from_llm_response"
            else:
                try:
                    profile_generator._parse_profile(matching_item, seed)
                    reason = "fallback_after_parse_success"
                except Exception as exc:
                    reason = "profile_validation_failed"
                    parse_error = f"{type(exc).__name__}: {exc}"

        results.append(
            {
                "agent_id": agent_id,
                "call_index": call["index"],
                "reason": reason,
                "error": parse_error,
                "content_sha256": call.get("content_sha256"),
                "raw_path": call.get("raw_path"),
            }
        )
    return results


async def _run(args: argparse.Namespace) -> int:
    out_dir = args.output_root / args.label
    raw_dir = out_dir / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)

    fallback_seeds: dict[str, AgentSeed] = {}
    original_build_fallback = profile_generator.ProfileGenerator._build_fallback_profile

    def wrapped_build_fallback(self: profile_generator.ProfileGenerator, seed: AgentSeed) -> Any:
        fallback_seeds[seed.agent_id] = seed
        return original_build_fallback(self, seed)

    profile_generator.ProfileGenerator._build_fallback_profile = wrapped_build_fallback
    llm = MeteredLiteLLMClient(
        response_format_json=args.response_format_json,
        llm_seed=args.llm_seed,
        raw_dir=raw_dir,
    )
    started = time.monotonic()
    status = "ok"
    error: str | None = None
    try:
        config = PipelineConfig(
            input_path=args.input,
            requirement=args.requirement,
            preset=Preset(args.preset),
            seed=args.seed,
            output_dir=out_dir,
            model=args.model,
        )
        ontology_a, ontology_b = await OntologyPipeline(config, llm).run()
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        ontology_a = None
        ontology_b = None
    finally:
        profile_generator.ProfileGenerator._build_fallback_profile = original_build_fallback

    elapsed = time.monotonic() - started
    call_diagnostics = [
        {**call, "diagnostics": _profile_call_diagnostics(call)}
        if call.get("stage") == "profile"
        else call
        for call in llm.calls
    ]
    fallback_diagnostics = _classify_fallbacks(
        calls=llm.calls,
        fallback_seeds=fallback_seeds,
    )

    total_cost = sum(
        float(call["cost_usd"]) for call in llm.calls if call.get("cost_usd") is not None
    )
    total_tokens = sum(int(call.get("total_tokens") or 0) for call in llm.calls)
    profile_calls = [call for call in llm.calls if call.get("stage") == "profile"]

    preset = Preset(args.preset)
    expected_agents = PRESET_AGENT_COUNTS[preset]
    parse_status_values = sorted(
        {
            str(call.get("diagnostics", {}).get("parse_status"))
            for call in call_diagnostics
            if call.get("stage") == "profile"
        }
    )
    summary: dict[str, Any] = {
        "label": args.label,
        "status": status,
        "error": error,
        "model": args.model,
        "preset": args.preset,
        "seed": args.seed,
        "llm_seed": args.llm_seed,
        "response_format_json": args.response_format_json,
        "elapsed_seconds": round(elapsed, 2),
        "call_count": len(llm.calls),
        "profile_call_count": len(profile_calls),
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 8) if total_cost else None,
        "fallback_count": len(fallback_seeds),
        "fallback_rate": round(len(fallback_seeds) / expected_agents, 4),
        "fallback_reasons": {
            reason: sum(1 for item in fallback_diagnostics if item["reason"] == reason)
            for reason in sorted({item["reason"] for item in fallback_diagnostics})
        },
        "profile_parse_statuses": {
            status_value: sum(
                1
                for call in call_diagnostics
                if call.get("stage") == "profile"
                and str(call.get("diagnostics", {}).get("parse_status")) == status_value
            )
            for status_value in parse_status_values
        },
    }

    if ontology_a is not None and ontology_b is not None:
        agents = list(ontology_a.agents.values())
        ideologies = [agent.ideology for agent in agents]
        sensitive_empty = sum(1 for agent in agents if not agent.sensitive_topics)
        following_empty = sum(1 for agent in agents if not agent.initial_following)
        path_a = out_dir / "ontology_a_persona.json"
        path_b = out_dir / "ontology_b_memory.json"
        summary.update(
            {
                "agent_count": len(agents),
                "ideology_mean": round(statistics.fmean(ideologies), 4),
                "ideology_min": round(min(ideologies), 4),
                "ideology_max": round(max(ideologies), 4),
                "sensitive_topics_empty_count": sensitive_empty,
                "initial_following_empty_count": following_empty,
                "ontology_a_sha256": _sha256_file(path_a),
                "ontology_b_sha256": _sha256_file(path_b),
                "ontology_a_normalized_sha256": _normal_hash_for_a(path_a),
            }
        )

    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "calls.json").write_text(
        json.dumps(call_diagnostics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "fallbacks.json").write_text(
        json.dumps(fallback_diagnostics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if status == "ok" else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--requirement", required=True)
    parser.add_argument("--preset", default="quick", choices=["quick", "standard", "full"])
    parser.add_argument("--seed", default=42, type=int)
    parser.add_argument("--llm-seed", default=None, type=int)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--response-format-json", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
