#!/usr/bin/env python3
"""Create a deterministic, evidence-linked Candidate Skill from a Trace v0.1."""

from __future__ import annotations

import argparse
from collections import defaultdict
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import tempfile
from typing import Any


ROOT = Path(__file__).parents[1]
TRACE_SCHEMA = ROOT / "schemas" / "trace.schema.json"
CANDIDATE_SCHEMA = ROOT / "schemas" / "candidate-skill.schema.json"
SKILL_NAME = "diagnose-javascript-dependency-failures"
SUPPORTED_MANAGERS = {"npm", "pnpm", "yarn"}
READ_ONLY_SUBCOMMANDS = {
    "npm": {"ls", "list", "explain", "why", "view", "info", "outdated", "doctor"},
    "pnpm": {"ls", "list", "why", "view", "info", "outdated"},
    "yarn": {"list", "why", "info"},
}
DRY_RUN_SUBCOMMANDS = {"npm": {"install"}, "pnpm": {"install"}, "yarn": set()}
FORBIDDEN_FLAGS = {"--force", "--legacy-peer-deps"}
SHELL_CONTROL_PATTERN = re.compile(r"[;&|<>`$\r\n]|%[A-Za-z_][A-Za-z0-9_]*%")
REQUIRED_CHECKS = {
    "manager_dispatched_to_worker",
    "worker_acknowledged",
    "worker_submitted",
    "worker_completed",
    "submission_verified",
    "semantic_package_manager",
    "secret_scan",
}

CHECK_EVENT_TYPES = {
    "manager_dispatched_to_worker": "task_dispatched",
    "worker_acknowledged": "task_acknowledged",
    "worker_submitted": "task_submitted",
    "worker_completed": "task_completed",
    "submission_verified": "task_submitted",
    "semantic_package_manager": "task_completed",
}


class TraceEligibilityError(ValueError):
    """The trace is valid data but is not safe evidence for Skill extraction."""


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read valid UTF-8 JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def validate_source_trace(trace: dict[str, Any]) -> None:
    validator = _load_module("trace_validator", ROOT / "scripts" / "validate_trace.py")
    exporter = validator.load_exporter()
    schema = load_json(TRACE_SCHEMA)
    errors = validator.validate_schema_instance(trace, schema)
    if errors:
        raise TraceEligibilityError("Source trace failed schema validation: " + "; ".join(errors[:8]))

    if trace["run"]["status"] != "success":
        raise TraceEligibilityError("Only successful traces can produce a Candidate Skill")
    if not trace["validation"]["passed"]:
        raise TraceEligibilityError("Source validation.passed must be true")
    failed_checks = [item["name"] for item in trace["validation"]["checks"] if not item["passed"]]
    if failed_checks:
        raise TraceEligibilityError("Source contains failed checks: " + ", ".join(sorted(failed_checks)))
    passed_checks = {item["name"] for item in trace["validation"]["checks"] if item["passed"]}
    missing = REQUIRED_CHECKS - passed_checks
    if missing:
        raise TraceEligibilityError("Source lacks required evidence checks: " + ", ".join(sorted(missing)))
    events_by_id = {event["event_id"]: event for event in trace["events"]}
    roles = _actor_roles(trace)
    for check in trace["validation"]["checks"]:
        expected_type = CHECK_EVENT_TYPES.get(check["name"])
        if not expected_type:
            continue
        evidence = [events_by_id[event_id] for event_id in check["evidence_event_ids"] if event_id in events_by_id]
        if not evidence or any(event["type"] != expected_type for event in evidence):
            raise TraceEligibilityError(f"Check {check['name']} does not reference {expected_type} evidence")
        expected_role = "manager" if check["name"] == "manager_dispatched_to_worker" else "worker"
        if any(roles.get(event["actor_id"]) != expected_role for event in evidence):
            raise TraceEligibilityError(f"Check {check['name']} references an unexpected actor role")
    security = trace["security"]
    if not security["secret_scan_passed"] or security["contains_chain_of_thought"]:
        raise TraceEligibilityError("Source trace does not satisfy the extraction security boundary")
    errors = exporter.validate_trace(trace)
    if errors:
        raise TraceEligibilityError("Source trace failed invariant validation: " + "; ".join(errors[:8]))


def _actor_roles(trace: dict[str, Any]) -> dict[str, str]:
    return {actor["id"]: actor["role"] for actor in trace["actors"]}


def _normalize_diagnostic_command(recommendation: str, manager: str) -> str:
    raw = recommendation.strip()
    if SHELL_CONTROL_PATTERN.search(raw):
        raise TraceEligibilityError("Diagnostic recommendation contains shell control syntax")
    text = " ".join(raw.split())
    wrapped = re.match(r"^(?:run|execute)\s+(['\"])(.+?)\1(?:\s+.*)?$", text, re.IGNORECASE)
    command = wrapped.group(2).strip() if wrapped else text
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError as exc:
        raise TraceEligibilityError(f"Diagnostic recommendation is not a valid command: {exc}") from exc
    if len(tokens) < 2 or tokens[0].lower() != manager:
        raise TraceEligibilityError(f"Diagnostic recommendation must begin with the declared package manager {manager}")
    subcommand = tokens[1].lower()
    lowered = {token.lower() for token in tokens[2:]}
    forbidden = FORBIDDEN_FLAGS & lowered
    if forbidden:
        raise TraceEligibilityError("Diagnostic recommendation uses forbidden flags: " + ", ".join(sorted(forbidden)))
    if subcommand in READ_ONLY_SUBCOMMANDS[manager]:
        return shlex.join(tokens)
    if subcommand in DRY_RUN_SUBCOMMANDS[manager] and "--dry-run" in lowered:
        return shlex.join(tokens)
    raise TraceEligibilityError(f"Unsupported or mutating {manager} diagnostic subcommand: {subcommand}")


def _recommendations(trace: dict[str, Any], manager: str) -> list[dict[str, Any]]:
    roles = _actor_roles(trace)
    found: dict[tuple[str, str], list[str]] = defaultdict(list)
    pattern = re.compile(r'"first_diagnostic_step"\s*:\s*"([^"\r\n]+)"', re.IGNORECASE)
    for event in trace["events"]:
        if event["type"] != "agent_message":
            continue
        role = roles.get(event["actor_id"])
        if role not in {"manager", "worker"}:
            continue
        content = event["payload"].get("content", "")
        if not isinstance(content, str):
            continue
        for match in pattern.finditer(content):
            command = _normalize_diagnostic_command(match.group(1), manager)
            if command and event["event_id"] not in found[(command, role)]:
                found[(command, role)].append(event["event_id"])
    if not found:
        raise TraceEligibilityError("No structured first_diagnostic_step evidence was found")

    sequences = {event["event_id"]: event["sequence"] for event in trace["events"]}
    submit_sequences = [event["sequence"] for event in trace["events"] if event["type"] == "task_submitted"]
    submit_sequence = min(submit_sequences)
    eligible = [
        key for key, event_ids in found.items()
        if key[1] == "worker" and max(sequences[event_id] for event_id in event_ids) < submit_sequence
    ]
    if not eligible:
        eligible = list(found)
    selected_key = max(
        eligible,
        key=lambda key: (max(sequences[event_id] for event_id in found[key]), key[0].lower(), key[1]),
    )
    result = []
    for command, role in sorted(found, key=lambda item: (item[0].lower(), item[1])):
        result.append({
            "command": command,
            "selected": (command, role) == selected_key,
            "actor_role": role,
            "evidence_event_ids": sorted(found[(command, role)]),
        })
    return result


def _error_category(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False).lower()
    if "required" in text or "project_id" in text:
        return "missing-required-input"
    if "not found" in text or "no such file" in text:
        return "missing-artifact"
    if "outside" in text or "invalid path" in text:
        return "invalid-path"
    if "exit code" in text or "failed" in text:
        return "command-failure"
    return "unknown-tool-error"


def _failure_lessons(trace: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for event in trace["events"]:
        if event["type"] == "tool_error":
            grouped[_error_category(event["payload"])].append(event["event_id"])
    return [
        {"category": category, "count": len(ids), "evidence_event_ids": sorted(ids)}
        for category, ids in sorted(grouped.items())
    ]


def build_candidate(trace: dict[str, Any]) -> dict[str, Any]:
    validate_source_trace(trace)
    task_input = trace["task"]["input"]
    manager = str(task_input.get("package_manager", "")).strip().lower()
    if manager not in SUPPORTED_MANAGERS:
        raise TraceEligibilityError(f"Unsupported or missing package_manager: {manager or '<empty>'}")
    failure = str(task_input.get("failure", "")).strip()
    if not failure:
        raise TraceEligibilityError("Task input must include a non-empty failure signature")

    recommendations = _recommendations(trace, manager)
    selected = next(item for item in recommendations if item["selected"])
    successful_ids = []
    for check in trace["validation"]["checks"]:
        if check["name"] in REQUIRED_CHECKS and check["passed"]:
            successful_ids.extend(check["evidence_event_ids"])
    successful_ids.extend(selected["evidence_event_ids"])
    successful_ids = sorted(set(successful_ids))

    conflict = len({item["command"].lower() for item in recommendations}) > 1
    score = 0.55
    if conflict:
        score -= 0.10
    score = round(max(0.0, score), 2)
    candidate = {
        "schema_version": "0.1.0",
        "candidate_id": f"candidate:{trace['task']['kind']}:{manager}:{failure.lower()}",
        "status": "candidate",
        "source_trace_ids": [trace["trace_id"]],
        "task_signature": {"kind": trace["task"]["kind"], "package_manager": manager, "failure": failure},
        "skill": {
            "name": SKILL_NAME,
            "triggers": [f"{manager} {failure}", "JavaScript dependency resolution failure", "peer dependency conflict"],
            "preconditions": ["Run from the affected project root", "Identify the package manager from the lockfile before choosing commands"],
            "steps": [
                {"id": "detect-manager", "instruction": "Inspect package.json and lockfiles; stop if the package manager is ambiguous.", "evidence_type": "template", "evidence_event_ids": []},
                {"id": "diagnose-read-only", "instruction": f"Start with the observed read-only diagnostic: {selected['command']}.", "evidence_type": "observed", "evidence_event_ids": selected["evidence_event_ids"]},
                {"id": "locate-conflict", "instruction": "Identify the direct dependency, peer requirement, and incompatible resolved version; preserve the exact error evidence.", "evidence_type": "template", "evidence_event_ids": []},
                {"id": "propose-minimal-change", "instruction": "Propose the smallest semver-compatible manifest change and show the diff before mutation.", "evidence_type": "template", "evidence_event_ids": []},
                {"id": "verify", "instruction": "Use the detected package manager to install, run the relevant tests or CI command, and report command evidence and exit status.", "evidence_type": "template", "evidence_event_ids": []},
            ],
            "guardrails": ["Do not default to --force or --legacy-peer-deps", "Do not mix npm, pnpm, and Yarn lockfiles", "Do not claim resolution without an install plus project-level verification"],
            "verification": ["Dependency installation exits successfully", "Relevant tests or CI checks pass", "The original resolution error no longer appears"],
        },
        "evidence": {
            "successful_path_event_ids": successful_ids,
            "diagnostic_recommendations": recommendations,
            "failure_lessons": _failure_lessons(trace),
        },
        "confidence": {
            "score": score,
            "level": "low" if score < 0.6 else "medium",
            "factors": ["Source trace passed AgentTeams lifecycle, submission, semantic, and secret-scan checks", "A Worker supplied structured diagnostic evidence"],
            "limitations": ["Only one training trace supports this candidate", "The trace diagnosed supplied text and did not repair a real repository", *( ["Source roles proposed conflicting first diagnostic commands"] if conflict else [] )],
        },
        "security": {"raw_payloads_copied": False, "source_secret_scan_passed": True, "contains_chain_of_thought": False},
    }
    validate_candidate(candidate)
    return candidate


def validate_candidate(candidate: dict[str, Any]) -> None:
    validator = _load_module("candidate_validator", ROOT / "scripts" / "validate_trace.py")
    errors = validator.validate_schema_instance(candidate, load_json(CANDIDATE_SCHEMA))
    if errors:
        raise ValueError("Generated candidate failed schema validation: " + "; ".join(errors))


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def render_skill(candidate: dict[str, Any]) -> str:
    selected = next(item for item in candidate["evidence"]["diagnostic_recommendations"] if item["selected"])
    failure = candidate["task_signature"]["failure"]
    return f'''---
name: {SKILL_NAME}
description: Diagnose JavaScript package dependency resolution failures such as npm {failure} and peer-dependency conflicts. Use when install or CI fails during npm, pnpm, or Yarn dependency resolution and the task requires safe triage, a minimal proposed change, and evidence-based verification.
---

# Diagnose JavaScript Dependency Failures

Treat this as a candidate workflow backed by one AgentTeams trace. Diagnose before mutating, preserve evidence, and never overstate validation.

## Workflow

1. Inspect `package.json` and lockfiles. Select npm for `package-lock.json`, pnpm for `pnpm-lock.yaml`, or Yarn for `yarn.lock`. Stop and ask if lockfiles conflict.
2. Reproduce or inspect the exact resolution error without changing dependencies. For the observed npm {failure} case, begin with `{selected['command']}`; a non-zero diagnostic exit may still contain useful evidence.
3. Identify the direct dependency, the peer-dependency requirement, and the incompatible resolved version. Quote only the minimal relevant error lines.
4. Propose the smallest semver-compatible `package.json` change. Show the intended diff before editing.
5. After approval or when edits are already authorized, use only the detected package manager. Run install, then the relevant tests or CI command.
6. Report commands, exit codes, changed files, and whether the original error disappeared.

## Guardrails

- Do not default to `--force` or `--legacy-peer-deps`; they may hide an invalid dependency graph.
- Do not mix npm, pnpm, and Yarn lockfiles or commands.
- Do not delete a lockfile or `node_modules` without explicit authorization and a clear recovery path.
- Do not claim success from reasoning alone. Require successful installation plus project-level verification.
- If a command suggested by this Skill is unsupported by the detected package manager, stop and adapt it rather than executing blindly.

## Evidence and status

Read `references/evidence.json` when provenance, confidence, conflicts, or failure lessons matter. This version remains `candidate`; it is not eligible for registry publication until held-out repair tasks pass.
'''


def write_outputs(candidate: dict[str, Any], output: Path, skill_dir: Path | None) -> None:
    if skill_dir is not None and not (skill_dir / "agents" / "openai.yaml").is_file():
        raise ValueError("Skill directory must be initialized with skill-creator before generation")
    serialized = json.dumps(candidate, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_write(output, serialized)
    if skill_dir is not None:
        _atomic_write(skill_dir / "SKILL.md", render_skill(candidate))
        _atomic_write(skill_dir / "references" / "evidence.json", serialized)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skill-dir", type=Path)
    args = parser.parse_args()
    try:
        candidate = build_candidate(load_json(args.trace))
        write_outputs(candidate, args.output, args.skill_dir)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"[FAIL] {exc}\n")
    print(f"[PASS] Candidate generated: {args.output}")
    print(f"status={candidate['status']} confidence={candidate['confidence']['score']:.2f} traces={len(candidate['source_trace_ids'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
