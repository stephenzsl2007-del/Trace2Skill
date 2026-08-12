#!/usr/bin/env python3
"""Build a deterministic Day 4 baseline-versus-Skill evaluation report."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from statistics import mean
from typing import Any


ROOT = Path(__file__).parents[1]
TRIAL_SCHEMA = ROOT / "schemas" / "day4-trial.schema.json"
REPORT_SCHEMA = ROOT / "schemas" / "evaluation-report.schema.json"
OFFICIAL = (
    "train-npm-peer-react-baseline.json",
    "train-npm-peer-react-skill-assisted.json",
    "eval-npm-peer-eslint-baseline.json",
    "eval-npm-peer-eslint-skill-assisted.json",
)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def render_report(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def validator_module():
    path = ROOT / "scripts" / "validate_trace.py"
    spec = importlib.util.spec_from_file_location("day4_report_validator", path)
    if not spec or not spec.loader:
        raise RuntimeError("Unable to load schema validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def metric_view(trial: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": trial["result"]["status"],
        "duration_ms": trial["metrics"]["duration_ms"],
        "event_count": trial["metrics"]["event_count"],
        "tool_calls": trial["metrics"]["tool_calls"],
        "tool_errors": trial["metrics"]["tool_errors"],
        "token_measurement": trial["metrics"]["token_measurement"],
    }


def build_report(input_dir: Path) -> dict[str, Any]:
    validator = validator_module()
    schema = load_json(TRIAL_SCHEMA)
    trials: list[tuple[str, dict[str, Any]]] = []
    for name in OFFICIAL:
        path = input_dir / name
        trial = load_json(path)
        errors = validator.validate_schema_instance(trial, schema)
        if errors:
            raise ValueError(f"{path}: " + "; ".join(errors))
        if not trial["validation"]["original_failure_reproduced"] or not trial["validation"]["verification_passed"]:
            raise ValueError(f"Trial did not pass both validation gates: {path}")
        trials.append((name, trial))

    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for _, trial in trials:
        grouped.setdefault(trial["fixture_id"], {})[trial["condition"]] = trial
    if set(grouped) != {"train-npm-peer-react", "eval-npm-peer-eslint"}:
        raise ValueError("Official Day 4 fixture pair set is incomplete")

    pairs = []
    for fixture_id in sorted(grouped):
        conditions = grouped[fixture_id]
        if set(conditions) != {"baseline", "skill-assisted"}:
            raise ValueError(f"Missing comparison condition for {fixture_id}")
        baseline = conditions["baseline"]
        skill = conditions["skill-assisted"]
        if baseline["framework"]["worker_name"] == skill["framework"]["worker_name"]:
            raise ValueError(f"Comparison conditions share a Worker context: {fixture_id}")
        pairs.append({
            "fixture_id": fixture_id,
            "split": baseline["split"],
            "baseline": metric_view(baseline),
            "skill_assisted": metric_view(skill),
            "delta": {
                "duration_ms": skill["metrics"]["duration_ms"] - baseline["metrics"]["duration_ms"],
                "event_count": skill["metrics"]["event_count"] - baseline["metrics"]["event_count"],
                "tool_calls": skill["metrics"]["tool_calls"] - baseline["metrics"]["tool_calls"],
                "tool_errors": skill["metrics"]["tool_errors"] - baseline["metrics"]["tool_errors"],
            },
        })

    baselines = [trial for _, trial in trials if trial["condition"] == "baseline"]
    skills = [trial for _, trial in trials if trial["condition"] == "skill-assisted"]
    baseline_success = mean(trial["result"]["status"] == "success" for trial in baselines)
    skill_success = mean(trial["result"]["status"] == "success" for trial in skills)
    baseline_duration = mean(trial["metrics"]["duration_ms"] for trial in baselines)
    skill_duration = mean(trial["metrics"]["duration_ms"] for trial in skills)
    baseline_errors = mean(trial["metrics"]["tool_errors"] for trial in baselines)
    skill_errors = mean(trial["metrics"]["tool_errors"] for trial in skills)
    improved = skill_success > baseline_success or (
        skill_success == baseline_success and skill_duration < baseline_duration and skill_errors <= baseline_errors
    )
    conclusion = "measured_improvement" if improved else "no_measured_improvement"
    skill_hashes = {trial["skill_sha256"] for trial in skills}
    if len(skill_hashes) != 1 or None in skill_hashes:
        raise ValueError("Skill-assisted trials do not share one auditable Skill hash")

    report = {
        "schema_version": "0.2.0",
        "experiment": {
            "framework": "AgentTeams", "framework_version": "v1.1.2", "model": "qwen-plus",
            "trial_count": len(trials), "pair_count": len(pairs),
        },
        "sources": [
            {"file": name, "sha256": hashlib.sha256((input_dir / name).read_bytes()).hexdigest(), "trace_id": trial["trace_id"]}
            for name, trial in trials
        ],
        "pairs": pairs,
        "aggregate": {
            "baseline_success_rate": baseline_success,
            "skill_success_rate": skill_success,
            "success_rate_delta": skill_success - baseline_success,
            "baseline_mean_duration_ms": baseline_duration,
            "skill_mean_duration_ms": skill_duration,
            "duration_delta_ms": skill_duration - baseline_duration,
            "baseline_mean_tool_errors": baseline_errors,
            "skill_mean_tool_errors": skill_errors,
            "tool_error_delta": skill_errors - baseline_errors,
            "skill_sha256": next(iter(skill_hashes)),
            "token_measurement": "unavailable",
        },
        "conclusion": conclusion,
        "limitations": [
            "Only two paired fixtures were executed; this is a pilot, not a statistically powered benchmark.",
            "The Candidate Skill was supplied verbatim in task context and hash-attested, not installed from a registry.",
            "AgentTeams/CoPaw output-contract corrections are included in observed event and error metrics.",
            "Provider token and cost telemetry was unavailable and was not estimated.",
        ],
    }
    report_errors = validator.validate_schema_instance(report, load_json(REPORT_SCHEMA))
    if report_errors:
        raise ValueError("Generated report failed schema: " + "; ".join(report_errors))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT / "evidence" / "day4")
    parser.add_argument("--output", type=Path, default=ROOT / "evidence" / "day4-report.json")
    args = parser.parse_args()
    report = build_report(args.input_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(render_report(report))
    print(f"[PASS] Day 4 report: {args.output} conclusion={report['conclusion']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
