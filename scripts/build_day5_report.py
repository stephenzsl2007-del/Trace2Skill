#!/usr/bin/env python3
"""Build the deterministic Day 5 failure-driven Skill refinement report."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parents[1]
EVIDENCE = ROOT / "evidence" / "day5"
OUTPUT = ROOT / "evidence" / "day5-report.json"
TRIAL_SCHEMA = ROOT / "schemas" / "day4-trial.schema.json"
FILES = (
    "train-pnpm-workspace-core-baseline.json",
    "train-pnpm-workspace-core-skill-v1.json",
    "eval-pnpm-workspace-ui-baseline.json",
    "eval-pnpm-workspace-ui-skill-v2.json",
    "eval-pnpm-workspace-ui-skill-v2-correction.json",
)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def validator_module():
    path = ROOT / "scripts" / "validate_trace.py"
    spec = importlib.util.spec_from_file_location("day5_report_validator", path)
    if not spec or not spec.loader:
        raise RuntimeError("Unable to load schema validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render_report(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def build_report(input_dir: Path = EVIDENCE) -> dict[str, Any]:
    validator = validator_module()
    schema = load_json(TRIAL_SCHEMA)
    trials = []
    for name in FILES:
        path = input_dir / name
        trial = load_json(path)
        errors = validator.validate_schema_instance(trial, schema)
        if errors:
            raise ValueError(f"{path}: " + "; ".join(errors))
        trials.append((name, trial))

    by_name = {name: trial for name, trial in trials}
    training_baseline = by_name[FILES[0]]
    training_v1 = by_name[FILES[1]]
    heldout_baseline = by_name[FILES[2]]
    heldout_v2_initial = by_name[FILES[3]]
    heldout_v2_correction = by_name[FILES[4]]
    if not all(trial["validation"]["original_failure_reproduced"] for _, trial in trials):
        raise ValueError("Every Day 5 trial must reproduce the declared original failure")
    if training_v1["validation"].get("proposal_rejection") != "protected-metadata-or-no-dependency-change":
        raise ValueError("v1 training failure is not the expected protected-metadata rejection")
    if heldout_v2_initial["validation"].get("proposal_rejection") != "protected-metadata-or-no-dependency-change":
        raise ValueError("Initial v2 held-out failure is not the expected protected-metadata rejection")
    expected_statuses = ("success", "failed", "success", "failed", "success")
    if tuple(trial["result"]["status"] for _, trial in trials) != expected_statuses:
        raise ValueError("Day 5 trial outcome sequence changed")
    workers = [trial["framework"]["worker_name"] for _, trial in trials]
    if len(workers) != len(set(workers)):
        raise ValueError("Day 5 trials must use isolated Worker contexts")

    v1 = ROOT / "skill-versions" / "diagnose-javascript-dependency-failures" / "v1" / "SKILL.md"
    v2 = ROOT / "skill-versions" / "diagnose-javascript-dependency-failures" / "v2" / "SKILL.md"
    v1_hash = hashlib.sha256(v1.read_bytes()).hexdigest()
    v2_hash = hashlib.sha256(v2.read_bytes()).hexdigest()
    if training_v1["skill_sha256"] != v1_hash or heldout_v2_correction["skill_sha256"] != v2_hash:
        raise ValueError("Versioned Skill hashes do not match their trial evidence")

    return {
        "schema_version": "0.1.0",
        "experiment": {
            "framework": "AgentTeams",
            "framework_version": "v1.1.2",
            "model": "qwen-plus",
            "package_manager": "pnpm",
            "trial_count": len(trials),
        },
        "versions": {
            "v1_sha256": v1_hash,
            "v2_initial_sha256": heldout_v2_initial["skill_sha256"],
            "v2_final_sha256": v2_hash,
        },
        "sources": [
            {"file": name, "sha256": hashlib.sha256((input_dir / name).read_bytes()).hexdigest(), "trace_id": trial["trace_id"]}
            for name, trial in trials
        ],
        "refinement": {
            "observed_failure": "protected-metadata-or-no-dependency-change",
            "v1_training_status": training_v1["result"]["status"],
            "v2_initial_heldout_status": heldout_v2_initial["result"]["status"],
            "v2_correction_status": heldout_v2_correction["result"]["status"],
            "v2_change": "mechanical deep-copy construction plus exact top-level-key and protected-value invariants",
        },
        "comparison": {
            "heldout_baseline_status": heldout_baseline["result"]["status"],
            "correction_status": heldout_v2_correction["result"]["status"],
            "duration_delta_ms": heldout_v2_correction["metrics"]["duration_ms"] - heldout_baseline["metrics"]["duration_ms"],
            "tool_error_delta": heldout_v2_correction["metrics"]["tool_errors"] - heldout_baseline["metrics"]["tool_errors"],
            "token_measurement": "unavailable",
        },
        "conclusion": "refinement_closed_not_independent_transfer",
        "limitations": [
            "The initial held-out v2 trial failed; the passing correction reused the same task and is not independent transfer evidence.",
            "Baseline also passed, while the correction run was not faster and had one additional tool error; no efficiency improvement is claimed.",
            "Provider token and cost telemetry was unavailable and was not estimated.",
            "The Skill remains candidate and is not registry-publication ready until another unseen pnpm task passes.",
        ],
    }


def main() -> int:
    report = build_report()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(render_report(report))
    print(f"[PASS] Day 5 report: {OUTPUT} conclusion={report['conclusion']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
