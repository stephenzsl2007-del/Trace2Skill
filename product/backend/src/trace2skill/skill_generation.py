from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .agent_specs import AgentSpec
from .agentteams import AgentTeamsClient, WorkerHandle
from .matrix_audit import MatrixAuditClient
from .security import sanitize
from .skill_packages import (
    SKILL_NAME,
    V1,
    V2_CANDIDATE,
    SkillPackage,
    SkillPackageValidator,
    install_package,
)


SKILL_MARKER = "TRACE2SKILL_SKILL_BLUEPRINT"
V2_MARKER = "TRACE2SKILL_SKILL_V2_BLUEPRINT"
BLUEPRINT_LISTS = (
    "trigger_conditions",
    "preconditions",
    "diagnostic_workflow",
    "tool_requirements",
    "prohibited_actions",
    "validation_rules",
    "decision_tree",
    "failure_patterns",
    "eval_scenarios",
    "evidence_refs",
)


def experience_evidence_refs(experience: dict[str, Any]) -> set[str]:
    references: set[str] = set()
    for key in (
        "task_signatures", "package_manager_detection", "success_paths", "failed_attempts",
        "preconditions", "tools_permissions", "prohibited_actions", "validator_rules",
        "conclusions",
    ):
        for item in experience.get(key) or []:
            references.update(str(ref) for ref in item.get("evidence_refs") or [])
    return references


def compact_experience_for_skill(experience: dict[str, Any]) -> dict[str, Any]:
    """Keep only validated, actionable material needed to design the Skill."""
    return {
        "experience_id": experience["experience_id"],
        "trace_ids": experience["trace_ids"],
        "scope": experience["scope"],
        "task_signatures": experience["task_signatures"],
        "success_paths": experience["success_paths"],
        "preconditions": experience["preconditions"],
        "tools_permissions": experience["tools_permissions"],
        "prohibited_actions": experience["prohibited_actions"],
        "validator_rules": experience["validator_rules"],
    }


def build_v1_spec(experience: dict[str, Any], role_spec: AgentSpec, task_id: str) -> str:
    evidence_refs = sorted(experience_evidence_refs(experience))
    return f"""# Trace2Skill v0.3 Skill v1 generation

TASK ID: {task_id}
ROLE SPEC HASH: {role_spec.content_hash}
MODEL: {role_spec.model}
SKILL NAME: {SKILL_NAME}
VERSION: {V1}

Design a concise Skill blueprint from only the validated Experience Model below. A deterministic host compiler will turn the blueprint into the six-file Skill package, so do not write Markdown files, Python source, or JSON-encoded file contents.
The training evidence observed npm only. Do not add support for unobserved package managers, held-out details, reference patches, bypass fixes, network-dependent fixes, or other ecosystems.

Return exactly `{SKILL_MARKER} ` followed by one JSON object:

{{
  "task_id": "{task_id}",
  "description": "one-line npm trigger description",
  "trigger_conditions": ["..."],
  "preconditions": ["..."],
  "diagnostic_workflow": ["..."],
  "tool_requirements": ["..."],
  "prohibited_actions": ["..."],
  "validation_rules": ["..."],
  "decision_tree": [{{"when":"observable condition","then":"safe action","evidence_refs":["..."]}}],
  "failure_patterns": [{{"signature":"observable error","diagnosis":"...","repair":"...","evidence_refs":["..."]}}],
  "eval_scenarios": [{{"name":"generic scenario name","failure_class":"lockfile-drift|missing-dev-dependency|peer-conflict"}}],
  "evidence_refs": ["..."]
}}

Use only evidence references from this allow-list: {json.dumps(evidence_refs, ensure_ascii=False)}. Preserve prohibitions against force, legacy-peer-deps, lifecycle scripts, network access, hidden host data, and unrelated file changes. Keep the workflow limited to npm. Do not use Markdown fences or add commentary after the JSON.

VALIDATED ACTIONABLE EXPERIENCE VIEW

{json.dumps(compact_experience_for_skill(experience), ensure_ascii=False, separators=(',', ':'))}
"""


def validate_v1_blueprint(
    value: dict[str, Any], allowed_evidence_refs: set[str], expected_task_id: str
) -> dict[str, Any]:
    clean = sanitize(value)
    expected = {"task_id", "description", *BLUEPRINT_LISTS}
    if set(clean) != expected:
        raise ValueError(f"Skill blueprint fields mismatch: {sorted(set(clean) ^ expected)}")
    if clean["task_id"] != expected_task_id:
        raise ValueError("Skill blueprint task_id does not match its assignment")
    if not isinstance(clean["description"], str) or not clean["description"].strip():
        raise ValueError("Skill blueprint requires a description")
    for key in BLUEPRINT_LISTS:
        if not isinstance(clean[key], list) or not clean[key]:
            raise ValueError(f"Skill blueprint requires non-empty {key}")
    for key in (
        "trigger_conditions", "preconditions", "diagnostic_workflow", "tool_requirements",
        "prohibited_actions", "validation_rules",
    ):
        if not all(isinstance(item, str) and item.strip() for item in clean[key]):
            raise ValueError(f"Skill blueprint has invalid text in {key}")
    for item in clean["decision_tree"]:
        if set(item) != {"when", "then", "evidence_refs"}:
            raise ValueError("Skill blueprint decision node has invalid fields")
    for item in clean["failure_patterns"]:
        if set(item) != {"signature", "diagnosis", "repair", "evidence_refs"}:
            raise ValueError("Skill blueprint failure pattern has invalid fields")
    for item in clean["eval_scenarios"]:
        if set(item) != {"name", "failure_class"} or item["failure_class"] not in {
            "lockfile-drift", "missing-dev-dependency", "peer-conflict",
        }:
            raise ValueError("Skill blueprint eval scenario is invalid")
    references = set(str(ref) for ref in clean["evidence_refs"])
    for item in clean["decision_tree"] + clean["failure_patterns"]:
        item_refs = set(str(ref) for ref in item.get("evidence_refs") or [])
        if not item_refs or not item_refs.issubset(allowed_evidence_refs):
            raise ValueError("Skill blueprint contains unauthorized evidence")
        references.update(item_refs)
    if not references or not references.issubset(allowed_evidence_refs):
        raise ValueError("Skill blueprint contains unauthorized evidence")
    non_guard_content = {key: value for key, value in clean.items() if key != "prohibited_actions"}
    serialized = json.dumps(non_guard_content, ensure_ascii=False).lower()
    if any(term in serialized for term in ("pnpm", "heldout", "held-out", "reference_patch")):
        raise ValueError("Skill v1 blueprint contains unobserved or hidden-answer material")
    clean["prohibited_actions"] = [
        "Do not use package managers that were not observed in the training Trace."
        if "pnpm" in item.lower()
        else item
        for item in clean["prohibited_actions"]
    ]
    guards = " ".join(clean["prohibited_actions"]).lower()
    mandatory_guards = {
        "force": "Do not use force flags.",
        "legacy-peer-deps": "Do not use legacy-peer-deps.",
        "lifecycle": "Do not enable dependency lifecycle scripts.",
        "network": "Do not use network access during diagnosis or repair.",
        "unrelated": "Do not modify unrelated files or fields.",
        "hidden": "Do not access hidden host data or private Validator configuration.",
    }
    for term, statement in mandatory_guards.items():
        if term not in guards:
            clean["prohibited_actions"].append(statement)
    clean["evidence_refs"] = sorted(references)
    return clean


def _bullets(values: list[str]) -> str:
    return "\n".join(f"- {value.strip()}" for value in values)


def render_v1_candidate(
    blueprint: dict[str, Any], experience: dict[str, Any], role_spec: AgentSpec
) -> dict[str, Any]:
    refs = blueprint["evidence_refs"]
    skill_md = f"""---
name: {SKILL_NAME}
description: {json.dumps(blueprint['description'].strip(), ensure_ascii=False)}
---

# Diagnose CI dependency failures

Apply evidence-grounded npm dependency repairs and let the host Validator decide success. Read [the decision tree](references/decision-tree.md) and [failure patterns](references/failure-patterns.md) when matching an observed error.

## Trigger conditions

{_bullets(blueprint['trigger_conditions'])}

## Preconditions

{_bullets(blueprint['preconditions'])}

## Diagnostic workflow

{_bullets(blueprint['diagnostic_workflow'])}

## Tool requirements

{_bullets(blueprint['tool_requirements'])}

## Prohibited actions

{_bullets(blueprint['prohibited_actions'])}

## Validation rules

{_bullets(blueprint['validation_rules'])}
"""
    decision_tree = "# Decision tree\n\n" + "\n".join(
        f"- When: {item['when']}\n  Then: {item['then']}\n  Evidence: {', '.join(item['evidence_refs'])}"
        for item in blueprint["decision_tree"]
    ) + "\n"
    failure_patterns = "# Failure patterns\n\n" + "\n".join(
        f"## {item['signature']}\n\n- Diagnosis: {item['diagnosis']}\n- Repair: {item['repair']}\n- Evidence: {', '.join(item['evidence_refs'])}\n"
        for item in blueprint["failure_patterns"]
    )
    validator_source = '''from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def run(command: list[str], cwd: Path) -> int:
    return subprocess.run(command, cwd=cwd, shell=False, check=False).returncode


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    repository = Path(sys.argv[1]).resolve()
    package = json.loads((repository / "package.json").read_text(encoding="utf-8"))
    install = ["npm", "ci" if (repository / "package-lock.json").exists() else "install", "--offline", "--ignore-scripts", "--no-audit", "--no-fund"]
    commands = [install, ["npm", "run", "build"], ["npm", "test"]]
    if not all(name in package.get("scripts", {}) for name in ("build", "test")):
        return 2
    for command in commands:
        if run(command, repository) != 0:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    cases = {
        "schema_version": "0.3",
        "cases": [
            {"name": item["name"], "failure_class": item["failure_class"], "expected": "validator-pass"}
            for item in blueprint["eval_scenarios"]
        ],
    }
    metadata = {
        "name": SKILL_NAME,
        "version": V1,
        "status": "candidate",
        "experience_id": experience["experience_id"],
        "source_trace_ids": experience["trace_ids"],
        "generation_model": role_spec.model,
        "prompt_hash": role_spec.content_hash,
        "evidence_refs": refs,
    }
    return {
        "name": SKILL_NAME,
        "version": V1,
        "files": {
            "SKILL.md": skill_md,
            "references/decision-tree.md": decision_tree,
            "references/failure-patterns.md": failure_patterns,
            "validators/validate.py": validator_source,
            "evals/cases.json": json.dumps(cases, ensure_ascii=False, indent=2) + "\n",
            "trace2skill.json": json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        },
    }


def build_v2_spec(
    previous: SkillPackage,
    failure_reports: list[dict[str, Any]],
    role_spec: AgentSpec,
    task_id: str,
) -> str:
    refinement_refs = sorted(
        str(ref) for report in failure_reports for ref in report.get("evidence_refs") or []
    )
    return f"""# Trace2Skill v0.3 Skill v2 refinement

TASK ID: {task_id}
ROLE SPEC HASH: {role_spec.content_hash}
VERSION: {V2_CANDIDATE}

Revise the validated v1 Skill blueprint using only the two observable failure reports. Add package-manager detection and separate npm/pnpm command mappings. Preserve every v1 safety guardrail. Do not infer dependency versions, patches, fixture contents, or answers.

Return exactly `{V2_MARKER} ` followed by one JSON object with these fields:

- task_id (must equal {task_id})
- description
- package_manager_detection (non-empty string array)
- trigger_conditions, preconditions, diagnostic_workflow, tool_requirements
- prohibited_actions, validation_rules
- decision_tree, failure_patterns, eval_scenarios, evidence_refs

Use decision_tree and failure_patterns object shapes from v1. Evidence refs must use the v1 refs or both failure refs. The result must explicitly contain these four operational strings: package-lock.json, pnpm-lock.yaml, npm ci, pnpm install. For pnpm, preserve frozen-lockfile behavior. Do not use Markdown fences or commentary.

V1 SKILL

{previous.files['SKILL.md']}

OBSERVABLE FAILURE REPORTS

{json.dumps(failure_reports, ensure_ascii=False, separators=(',', ':'))}

REQUIRED REFINEMENT REFS

{json.dumps(refinement_refs, ensure_ascii=False)}
"""


def validate_v2_blueprint(
    value: dict[str, Any],
    allowed_evidence_refs: set[str],
    refinement_refs: set[str],
    expected_task_id: str,
) -> dict[str, Any]:
    clean = sanitize(value)
    expected = {"task_id", "description", "package_manager_detection", *BLUEPRINT_LISTS}
    if set(clean) != expected or clean.get("task_id") != expected_task_id:
        raise ValueError("v2 blueprint identity or fields mismatch")
    for key in ("description",):
        if not isinstance(clean[key], str) or not clean[key].strip():
            raise ValueError(f"v2 blueprint requires {key}")
    for key in (*BLUEPRINT_LISTS, "package_manager_detection"):
        if not isinstance(clean[key], list) or not clean[key]:
            raise ValueError(f"v2 blueprint requires non-empty {key}")
    for item in clean["decision_tree"]:
        if set(item) != {"when", "then", "evidence_refs"}:
            raise ValueError("v2 decision node has invalid fields")
    for item in clean["failure_patterns"]:
        if set(item) != {"signature", "diagnosis", "repair", "evidence_refs"}:
            raise ValueError("v2 failure pattern has invalid fields")
    references = set(str(ref) for ref in clean["evidence_refs"])
    for item in clean["decision_tree"] + clean["failure_patterns"]:
        references.update(str(ref) for ref in item.get("evidence_refs") or [])
    if not references.issubset(allowed_evidence_refs) or not refinement_refs.issubset(references):
        raise ValueError("v2 blueprint is not grounded in both failure reports")
    serialized = json.dumps(clean, ensure_ascii=False).lower()
    for required in ("package-lock.json", "pnpm-lock.yaml", "npm ci", "pnpm install"):
        if required not in serialized:
            raise ValueError(f"v2 blueprint lacks package-manager branch: {required}")
    if any(term in serialized for term in ("reference_patch", "expected_package_json", "heldout")):
        raise ValueError("v2 blueprint contains hidden-answer material")
    clean["evidence_refs"] = sorted(references)
    return clean


def render_v2_candidate(
    blueprint: dict[str, Any],
    experience: dict[str, Any],
    role_spec: AgentSpec,
    previous: SkillPackage,
) -> dict[str, Any]:
    v1_guards = SkillPackageValidator._guardrails(previous.files["SKILL.md"])
    current_guards = {f"- {item}".lower() for item in blueprint["prohibited_actions"]}
    for guard in sorted(v1_guards - current_guards):
        blueprint["prohibited_actions"].append(guard[2:])
    skill_md = f"""---
name: {SKILL_NAME}
description: {json.dumps(blueprint['description'].strip(), ensure_ascii=False)}
---

# Diagnose CI dependency failures

Detect the repository package manager before choosing commands. Read [the decision tree](references/decision-tree.md) and [failure patterns](references/failure-patterns.md) for details.

## Trigger conditions

{_bullets(blueprint['trigger_conditions'])}

## Preconditions

{_bullets(blueprint['preconditions'])}

## Package manager detection

- package-lock.json or npm-shrinkwrap.json -> npm
- pnpm-lock.yaml or pnpm-workspace.yaml -> pnpm

## Diagnostic workflow

{_bullets(blueprint['package_manager_detection'])}
{_bullets(blueprint['diagnostic_workflow'])}
- For npm, use npm ci when a lockfile exists.
- For pnpm, use pnpm install --frozen-lockfile when a lockfile exists.

## Tool requirements

{_bullets(blueprint['tool_requirements'])}

## Prohibited actions

{_bullets(blueprint['prohibited_actions'])}

## Validation rules

{_bullets(blueprint['validation_rules'])}
"""
    decision_tree = "# Decision tree\n\n" + "\n".join(
        f"- When: {item['when']}\n  Then: {item['then']}\n  Evidence: {', '.join(item['evidence_refs'])}"
        for item in blueprint["decision_tree"]
    ) + "\n"
    failure_patterns = "# Failure patterns\n\n" + "\n".join(
        f"## {item['signature']}\n\n- Diagnosis: {item['diagnosis']}\n- Repair: {item['repair']}\n- Evidence: {', '.join(item['evidence_refs'])}\n"
        for item in blueprint["failure_patterns"]
    )
    validator_source = '''from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    repo = Path(sys.argv[1]).resolve()
    package = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    if (repo / "pnpm-lock.yaml").exists() or (repo / "pnpm-workspace.yaml").exists():
        install = ["pnpm", "install", "--offline", "--frozen-lockfile", "--ignore-scripts"]
        run = "pnpm"
    elif (repo / "package-lock.json").exists() or (repo / "npm-shrinkwrap.json").exists():
        install = ["npm", "ci", "--offline", "--ignore-scripts", "--no-audit", "--no-fund"]
        run = "npm"
    else:
        return 2
    if not all(name in package.get("scripts", {}) for name in ("build", "test")):
        return 2
    for command in (install, [run, "run", "build"], [run, "test"]):
        if subprocess.run(command, cwd=repo, shell=False, check=False).returncode != 0:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    metadata = {
        "name": SKILL_NAME,
        "version": V2_CANDIDATE,
        "status": "candidate",
        "experience_id": experience["experience_id"],
        "source_trace_ids": experience["trace_ids"],
        "generation_model": role_spec.model,
        "prompt_hash": role_spec.content_hash,
        "evidence_refs": blueprint["evidence_refs"],
    }
    cases = {
        "schema_version": "0.3",
        "cases": [
            {"name": item["name"], "failure_class": item["failure_class"], "expected": "validator-pass"}
            for item in blueprint["eval_scenarios"]
        ],
    }
    return {
        "name": SKILL_NAME,
        "version": V2_CANDIDATE,
        "files": {
            "SKILL.md": skill_md,
            "references/decision-tree.md": decision_tree,
            "references/failure-patterns.md": failure_patterns,
            "validators/validate.py": validator_source,
            "evals/cases.json": json.dumps(cases, ensure_ascii=False, indent=2) + "\n",
            "trace2skill.json": json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        },
    }
class SkillGenerationRunner:
    def __init__(
        self,
        agentteams: AgentTeamsClient,
        matrix: MatrixAuditClient,
        role_spec: AgentSpec,
    ) -> None:
        self.agentteams = agentteams
        self.matrix = matrix
        self.role_spec = role_spec

    def run_v1(
        self,
        experience: dict[str, Any],
        worker: WorkerHandle,
        timeout_seconds: int = 300,
    ) -> tuple[dict[str, Any], SkillPackage]:
        task_id = f"v03-skill-v1-{uuid.uuid4().hex[:8]}"
        started_at = datetime.now(UTC).isoformat()
        spec = build_v1_spec(experience, self.role_spec, task_id)
        assignment = self.agentteams.create_finite_task(
            worker, task_id, "generate Skill v1 from validated Experience", spec, inline_spec=True
        )
        try:
            raw_blueprint, events = self.matrix.wait_for_worker_result(
                task_id, SKILL_MARKER, timeout_seconds=timeout_seconds
            )
            blueprint = validate_v1_blueprint(
                raw_blueprint, experience_evidence_refs(experience), task_id
            )
            candidate = render_v1_candidate(blueprint, experience, self.role_spec)
            package = SkillPackageValidator().validate_candidate(
                candidate,
                expected_version=V1,
                allowed_evidence_refs=experience_evidence_refs(experience),
            )
            self.agentteams.finalize_task(task_id, "completed")
            artifact = sanitize(
                {
                    "schema_version": "0.3",
                    "generation_id": f"skillgen_{uuid.uuid4().hex}",
                    "task_id": task_id,
                    "role": "skill-engineer",
                    "role_spec_hash": self.role_spec.content_hash,
                    "experience_id": experience["experience_id"],
                    "experience_hash": hashlib.sha256(
                        json.dumps(experience, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest(),
                    "assignment": assignment,
                    "messages": events,
                    "skill_name": package.name,
                    "version": package.version,
                    "manifest_hash": package.manifest_hash,
                    "blueprint": blueprint,
                    "started_at": started_at,
                    "ended_at": datetime.now(UTC).isoformat(),
                    "status": "succeeded",
                }
            )
            return artifact, package
        except Exception:
            try:
                self.agentteams.finalize_task(task_id, "failed")
            except Exception:
                pass
            raise

    def recover_v1(
        self,
        task_id: str,
        experience: dict[str, Any],
        timeout_seconds: int = 10,
    ) -> tuple[dict[str, Any], SkillPackage]:
        raw_blueprint, events = self.matrix.wait_for_worker_result(
            task_id, SKILL_MARKER, timeout_seconds=timeout_seconds
        )
        blueprint = validate_v1_blueprint(
            raw_blueprint, experience_evidence_refs(experience), task_id
        )
        candidate = render_v1_candidate(blueprint, experience, self.role_spec)
        package = SkillPackageValidator().validate_candidate(
            candidate,
            expected_version=V1,
            allowed_evidence_refs=experience_evidence_refs(experience),
        )
        self.agentteams.finalize_task(task_id, "completed")
        now = datetime.now(UTC).isoformat()
        artifact = sanitize(
            {
                "schema_version": "0.3",
                "generation_id": f"skillgen_{uuid.uuid4().hex}",
                "task_id": task_id,
                "role": "skill-engineer",
                "role_spec_hash": self.role_spec.content_hash,
                "experience_id": experience["experience_id"],
                "experience_hash": hashlib.sha256(
                    json.dumps(experience, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "assignment": {"recovered": True},
                "messages": events,
                "skill_name": package.name,
                "version": package.version,
                "manifest_hash": package.manifest_hash,
                "blueprint": blueprint,
                "started_at": None,
                "ended_at": now,
                "status": "succeeded",
            }
        )
        return artifact, package

    def run_v2(
        self,
        experience: dict[str, Any],
        previous: SkillPackage,
        failure_reports: list[dict[str, Any]],
        worker: WorkerHandle,
        timeout_seconds: int = 180,
    ) -> tuple[dict[str, Any], SkillPackage]:
        if len(failure_reports) != 2:
            raise ValueError("v2 refinement requires exactly two v1 failure reports")
        task_id = f"v03-skill-v2-{uuid.uuid4().hex[:8]}"
        started_at = datetime.now(UTC).isoformat()
        refinement_refs = {
            str(ref) for report in failure_reports for ref in report.get("evidence_refs") or []
        }
        allowed = experience_evidence_refs(experience) | refinement_refs
        spec = build_v2_spec(previous, failure_reports, self.role_spec, task_id)
        assignment = self.agentteams.create_finite_task(
            worker, task_id, "refine Skill v2 from v1 failures", spec, inline_spec=True
        )
        try:
            raw, events = self.matrix.wait_for_worker_result(
                task_id, V2_MARKER, timeout_seconds=timeout_seconds
            )
            blueprint = validate_v2_blueprint(raw, allowed, refinement_refs, task_id)
            candidate = render_v2_candidate(blueprint, experience, self.role_spec, previous)
            package = SkillPackageValidator().validate_candidate(
                candidate,
                expected_version=V2_CANDIDATE,
                allowed_evidence_refs=allowed,
                previous=previous,
                refinement_refs=refinement_refs,
            )
            self.agentteams.finalize_task(task_id, "completed")
            artifact = sanitize(
                {
                    "schema_version": "0.3",
                    "generation_id": f"skillgen_{uuid.uuid4().hex}",
                    "task_id": task_id,
                    "role": "skill-engineer",
                    "role_spec_hash": self.role_spec.content_hash,
                    "experience_id": experience["experience_id"],
                    "assignment": assignment,
                    "messages": events,
                    "failure_reports": failure_reports,
                    "skill_name": package.name,
                    "version": package.version,
                    "manifest_hash": package.manifest_hash,
                    "blueprint": blueprint,
                    "started_at": started_at,
                    "ended_at": datetime.now(UTC).isoformat(),
                    "status": "succeeded",
                }
            )
            return artifact, package
        except Exception:
            try:
                self.agentteams.finalize_task(task_id, "failed")
            except Exception:
                pass
            raise


def persist_skill_candidate(
    artifact: dict[str, Any], package: SkillPackage, version_root: Path
) -> dict[str, Any]:
    version_root = Path(version_root)
    version_root.mkdir(parents=True, exist_ok=False)
    package_root = version_root / "package"
    installed_hash = install_package(package, package_root)
    archive_path = version_root / f"{package.name}-{package.version}.zip"
    archive_path.write_bytes(package.zip_bytes)
    manifest = {
        "schema_version": "0.3",
        "name": package.name,
        "version": package.version,
        "manifest_hash": package.manifest_hash,
        "installed_hash": installed_hash,
        "zip_sha256": hashlib.sha256(package.zip_bytes).hexdigest(),
        "files": {
            path: hashlib.sha256(content.encode()).hexdigest()
            for path, content in sorted(package.files.items())
        },
    }
    (version_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (version_root / "generation.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest
