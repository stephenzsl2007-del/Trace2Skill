#!/usr/bin/env python3
"""Build a deterministic, evidence-attested Nacos Skill release bundle."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any
import zipfile


ROOT = Path(__file__).parents[1]
NAME = "diagnose-javascript-dependency-failures"
VERSION = "2.0.0"
SKILL_DIR = ROOT / "skills" / NAME
EVALUATED_SKILL = ROOT / "skill-versions" / NAME / "v2" / "SKILL.md"
RELEASE_SKILL = ROOT / "skill-versions" / NAME / VERSION / "SKILL.md"
EVIDENCE_DIR = ROOT / "evidence" / "day6"
OUTPUT_DIR = ROOT / "release-bundles" / NAME / VERSION
TRIAL_SCHEMA = ROOT / "schemas" / "day4-trial.schema.json"
BASELINE_FILE = "eval-pnpm-workspace-logger-baseline.json"
SKILL_FILE = "eval-pnpm-workspace-logger-skill-v2.json"
PACKAGE_FILES = ("SKILL.md", "agents/openai.yaml", "references/evidence.json")
FORBIDDEN = (b"matrix-local.hiclaw.io", b"@admin:", b"@manager:", b"HICLAW_ADMIN_PASSWORD", b"C:\\Users\\")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def validator_module():
    path = ROOT / "scripts" / "validate_trace.py"
    spec = importlib.util.spec_from_file_location("day6_release_validator", path)
    if not spec or not spec.loader:
        raise RuntimeError("Unable to load schema validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def expected_release_skill() -> bytes:
    evaluated = EVALUATED_SKILL.read_text(encoding="utf-8")
    release = evaluated.replace(
        "Treat this as a candidate v2 workflow backed by npm execution evidence and a pnpm training failure. Diagnose before mutating, preserve evidence, and never overstate validation.",
        "Treat this as a validated workflow for the bounded npm peer-conflict and pnpm workspace-package scope covered by the release evidence. Diagnose before mutating, preserve evidence, and never overstate validation beyond that scope.",
    ).replace(
        "Read `references/evidence.json` when original trace provenance matters. This version remains `candidate`; pnpm held-out evidence must pass before any validation or publication claim.",
        "Read `references/evidence.json` when original trace provenance matters. The release manifest carries the independent transfer evidence, exact hashes, validation scope, and registry state; broader failure classes and Yarn remain unvalidated.",
    )
    return release.encode("utf-8")


def behavior_bytes(skill: bytes) -> bytes:
    text = skill.decode("utf-8")
    start = text.index("## Workflow")
    end = text.index("## Evidence and status")
    return text[start:end].encode("utf-8")


def load_trials() -> tuple[dict[str, Any], dict[str, Any]]:
    validator = validator_module()
    schema = load_json(TRIAL_SCHEMA)
    baseline = load_json(EVIDENCE_DIR / BASELINE_FILE)
    skill = load_json(EVIDENCE_DIR / SKILL_FILE)
    for name, trial in ((BASELINE_FILE, baseline), (SKILL_FILE, skill)):
        errors = validator.validate_schema_instance(trial, schema)
        if errors:
            raise ValueError(f"{name}: " + "; ".join(errors))
        if trial["fixture_id"] != "eval-pnpm-workspace-logger" or trial["split"] != "held-out":
            raise ValueError(f"Day 6 evidence identity mismatch: {name}")
        if not trial["validation"]["original_failure_reproduced"]:
            raise ValueError(f"Original failure was not reproduced: {name}")
    if baseline["condition"] != "baseline" or baseline["result"]["status"] != "failed":
        raise ValueError("Day 6 baseline must preserve its observed failure")
    if baseline["validation"].get("proposal_rejection") != "agentteams-timeout":
        raise ValueError("Day 6 baseline failure category changed")
    if skill["condition"] != "skill-assisted" or skill["result"]["status"] != "success":
        raise ValueError("Day 6 Skill trial did not succeed")
    if not skill["validation"]["verification_passed"] or skill["validation"]["verification_exit_code"] != 0:
        raise ValueError("Day 6 Skill proposal did not pass real package-manager verification")
    if baseline["framework"]["worker_name"] == skill["framework"]["worker_name"]:
        raise ValueError("Day 6 comparison reused one Worker context")
    evaluated_hash = digest(EVALUATED_SKILL.read_bytes())
    if skill["skill_sha256"] != evaluated_hash:
        raise ValueError("Day 6 trial does not attest the immutable evaluated v2 Skill")
    return baseline, skill


def build_zip(path: Path) -> tuple[str, list[dict[str, str]]]:
    actual = {item.relative_to(SKILL_DIR).as_posix() for item in SKILL_DIR.rglob("*") if item.is_file()}
    if actual != set(PACKAGE_FILES):
        raise ValueError(f"Unexpected Skill package files: {actual}")
    entries = []
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for relative in PACKAGE_FILES:
            data = (SKILL_DIR / relative).read_bytes()
            leaked = [token.decode("ascii") for token in FORBIDDEN if token in data]
            if leaked:
                raise ValueError(f"Release package contains local identifier {leaked[0]}: {relative}")
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
            entries.append({"file": relative, "sha256": digest(data)})
    return digest(path.read_bytes()), entries


def build_release(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    baseline, skill = load_trials()
    if RELEASE_SKILL.read_bytes() != expected_release_skill():
        raise ValueError("Release Skill contains changes beyond the two approved status statements")
    if SKILL_DIR.joinpath("SKILL.md").read_bytes() != RELEASE_SKILL.read_bytes():
        raise ValueError("Current Skill does not match the immutable 2.0.0 release snapshot")
    evaluated_behavior = digest(behavior_bytes(EVALUATED_SKILL.read_bytes()))
    release_behavior = digest(behavior_bytes(RELEASE_SKILL.read_bytes()))
    if evaluated_behavior != release_behavior:
        raise ValueError("Release changed evaluated workflow or guardrail behavior")

    package = output_dir / f"{NAME}-{VERSION}.zip"
    package_hash, entries = build_zip(package)
    sources = []
    for filename, trial in ((BASELINE_FILE, baseline), (SKILL_FILE, skill)):
        path = EVIDENCE_DIR / filename
        sources.append({"file": f"evidence/day6/{filename}", "sha256": digest(path.read_bytes()), "trace_id": trial["trace_id"]})
    manifest = {
        "schema_version": "0.1.0",
        "skill": {
            "name": NAME,
            "version": VERSION,
            "status": "validated",
            "validation_scope": ["npm-peer-conflict", "pnpm-workspace-package"],
            "unvalidated_scope": ["yarn", "other-javascript-dependency-failure-classes"],
            "evaluated_skill_sha256": digest(EVALUATED_SKILL.read_bytes()),
            "release_skill_sha256": digest(RELEASE_SKILL.read_bytes()),
            "behavior_sha256": release_behavior,
            "change_from_evaluated": "status-only; workflow and guardrails byte-identical",
        },
        "package": {"file": package.name, "sha256": package_hash, "files": entries},
        "evidence": {
            "fixture": "eval-pnpm-workspace-logger",
            "first_run_unseen": True,
            "answer_in_prompt": False,
            "baseline_status": "failed",
            "baseline_failure": "agentteams-timeout",
            "skill_status": "success",
            "offline_verification_passed": True,
            "sources": sources,
        },
        "registry": {
            "backend": "Nacos AI Registry 3.2+",
            "agentteams_compatibility": "v1.1.2",
            "cli_compatibility": "nacos-cli 1.0.5-beta.1",
            "target": "nacos://market.hiclaw.io:80/public",
            "state": "staged-local",
            "external_write_performed": False,
            "required_flow": ["skill-upload", "skill-review", "skill-release"],
            "force_publish_allowed": False,
            "credential_policy": "Use a scoped Nacos profile or AgentTeams STS credential; never store credentials in the repository or manifest.",
        },
        "claims": {
            "independent_transfer": "observed-on-one-new-held-out-task",
            "efficiency_improvement": "not-claimed",
            "statistical_significance": "not-claimed",
            "public_registry_publication": "not-performed",
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "release-manifest.json").write_bytes(render(manifest))
    return manifest


def main() -> int:
    manifest = build_release()
    print(f"[PASS] Day 6 release bundle: {OUTPUT_DIR} status={manifest['skill']['status']} registry={manifest['registry']['state']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
