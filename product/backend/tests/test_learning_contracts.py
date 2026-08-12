from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from trace2skill.experience import (
    ExperienceValidationError,
    ExperienceValidator,
    validate_failure_report,
)
from trace2skill.skill_packages import (
    SKILL_NAME,
    V1,
    V2_CANDIDATE,
    SkillPackageValidator,
    install_package,
)


class LearningContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.traces = [
            {
                "trace_id": f"training-{index}",
                "events": [
                    {"event_id": "detect", "package_manager": "npm"},
                    {"event_id": "failure", "package_manager": "npm"},
                    {"event_id": "repair", "package_manager": "npm"},
                    {"event_id": "validate", "package_manager": "npm"},
                ],
            }
            for index in range(1, 4)
        ]
        self.refs = {
            f"trace:training-{index}#{event}"
            for index in range(1, 4)
            for event in ("detect", "failure", "repair", "validate")
        }

    def test_three_trace_experience_requires_real_evidence(self) -> None:
        candidate = self._experience()
        result = ExperienceValidator().validate(candidate, self.traces)
        self.assertEqual(result["scope"]["ecosystems"], ["npm"])
        broken = copy.deepcopy(candidate)
        broken["success_paths"][0]["evidence_refs"] = ["trace:training-1#missing"]
        with self.assertRaises(ExperienceValidationError):
            ExperienceValidator().validate(broken, self.traces)

    def test_experience_rejects_scope_expansion_and_secrets(self) -> None:
        expanded = self._experience()
        expanded["scope"]["ecosystems"].append("pnpm")
        with self.assertRaises(ExperienceValidationError):
            ExperienceValidator().validate(expanded, self.traces)
        secret = self._experience()
        secret["conclusions"][0]["statement"] = "token=abcdefghijklmnop"
        with self.assertRaises(ExperienceValidationError):
            ExperienceValidator().validate(secret, self.traces)

    def test_failure_report_is_observable_only(self) -> None:
        report = {
            "schema_version": "0.3",
            "fixture_id": "heldout-pnpm-frozen-lockfile",
            "failed_stage": "install",
            "command": ["pnpm", "install", "--frozen-lockfile"],
            "exit_code": 1,
            "error_category": "package-manager-command-mismatch",
            "output_hash": "a" * 64,
            "evidence_refs": ["sha256:" + "b" * 64],
        }
        self.assertEqual(validate_failure_report(report), report)
        leaked = {**report, "reference_patch": "change package.json"}
        with self.assertRaises(ValueError):
            validate_failure_report(leaked)

    def test_v1_and_evidence_driven_v2_packages(self) -> None:
        validator = SkillPackageValidator()
        v1 = validator.validate_candidate(
            self._skill_candidate(V1, include_pnpm=False, evidence=[next(iter(self.refs))]),
            expected_version=V1,
            allowed_evidence_refs=self.refs,
        )
        failure_ref = "sha256:" + "f" * 64
        allowed = self.refs | {failure_ref}
        v2 = validator.validate_candidate(
            self._skill_candidate(
                V2_CANDIDATE,
                include_pnpm=True,
                evidence=[next(iter(self.refs)), failure_ref],
            ),
            expected_version=V2_CANDIDATE,
            allowed_evidence_refs=allowed,
            previous=v1,
            refinement_refs={failure_ref},
        )
        self.assertNotEqual(v1.manifest_hash, v2.manifest_hash)
        self.assertEqual(v2.zip_bytes, validator.validate_candidate(
            self._skill_candidate(
                V2_CANDIDATE,
                include_pnpm=True,
                evidence=[next(iter(self.refs)), failure_ref],
            ),
            expected_version=V2_CANDIDATE,
            allowed_evidence_refs=allowed,
            previous=v1,
            refinement_refs={failure_ref},
        ).zip_bytes)
        with tempfile.TemporaryDirectory() as directory:
            digest = install_package(v2, Path(directory) / SKILL_NAME)
            self.assertEqual(len(digest), 64)

    def test_v2_rejects_guardrail_regression_and_ungrounded_refinement(self) -> None:
        validator = SkillPackageValidator()
        reference = next(iter(self.refs))
        v1 = validator.validate_candidate(
            self._skill_candidate(V1, False, [reference]),
            expected_version=V1,
            allowed_evidence_refs=self.refs,
        )
        failure_ref = "sha256:" + "f" * 64
        candidate = self._skill_candidate(V2_CANDIDATE, True, [reference, failure_ref])
        candidate["files"]["SKILL.md"] = candidate["files"]["SKILL.md"].replace(
            "- Never use force or legacy-peer-deps.\n", ""
        )
        with self.assertRaises(ValueError):
            validator.validate_candidate(
                candidate,
                expected_version=V2_CANDIDATE,
                allowed_evidence_refs=self.refs | {failure_ref},
                previous=v1,
                refinement_refs={failure_ref},
            )

    def _experience(self) -> dict:
        def item(item_id: str, statement: str, event: str) -> dict:
            return {
                "id": item_id,
                "statement": statement,
                "evidence_refs": [f"trace:training-1#{event}"],
            }

        return {
            "schema_version": "0.3",
            "experience_id": "experience-loop-1",
            "trace_ids": ["training-1", "training-2", "training-3"],
            "scope": {
                "domain": "javascript-typescript-ci-dependencies",
                "ecosystems": ["npm"],
                "failure_classes": ["peer-conflict", "lock-drift", "missing-dev-dependency"],
                "exclusions": ["deployment and runtime incidents"],
            },
            "task_signatures": [item("signature", "Identify dependency error signature", "failure")],
            "package_manager_detection": [item("manager", "package-lock.json indicates npm", "detect")],
            "success_paths": [item("success", "Apply the smallest dependency repair", "repair")],
            "failed_attempts": [item("failed", "Do not bypass peer resolution", "failure")],
            "preconditions": [item("precondition", "Repository snapshot is isolated", "detect")],
            "tools_permissions": [item("tools", "Use npm and node only", "detect")],
            "prohibited_actions": [item("guard", "Do not use force flags", "failure")],
            "validator_rules": [item("validator", "Install build and tests must pass", "validate")],
            "conclusions": [
                {
                    **item("conclusion", "The npm workflow is supported", "validate"),
                    "confidence": 0.8,
                    "limitations": ["pnpm is unobserved in training traces"],
                    "conflicts": [],
                }
            ],
        }

    @staticmethod
    def _skill_candidate(version: str, include_pnpm: bool, evidence: list[str]) -> dict:
        manager = (
            "Inspect package-lock.json and pnpm-lock.yaml before choosing a command.\n"
            "- npm: run npm ci.\n- pnpm: run pnpm install --frozen-lockfile.\n"
            if include_pnpm
            else "Inspect package-lock.json and run npm ci.\n"
        )
        skill_md = f"""---
name: {SKILL_NAME}
description: Diagnose and safely repair npm CI dependency failures.
---

# CI dependency diagnosis

## Trigger conditions

- Dependency install or module resolution fails in CI.

## Preconditions

- Work in an isolated repository snapshot.

## Diagnostic workflow

{manager}- Reproduce the original failure before editing.
- Make the smallest dependency or lockfile repair.

## Tool requirements

- Package manager and Node.js commands supplied by the host.

## Prohibited actions

- Never use force or legacy-peer-deps.
- Never enable lifecycle scripts or network access.

## Validation rules

- Host install, build, and tests must all pass.
- The original error must disappear.
"""
        metadata = {
            "name": SKILL_NAME,
            "version": version,
            "evidence_refs": evidence,
            "experience_id": "experience-loop-1",
            "source_trace_ids": ["training-1", "training-2", "training-3"],
            "generation_model": "qwen-plus",
            "prompt_hash": "a" * 64,
            "validation_tasks": [],
            "success_rate": None,
            "token_usage": None,
            "tool_calls": None,
            "duration_ms": None,
            "status": "candidate",
            "nacos": None,
        }
        return {
            "name": SKILL_NAME,
            "version": version,
            "files": {
                "SKILL.md": skill_md,
                "references/decision-tree.md": "# Decision tree\n\nUse observable repository evidence.\n",
                "references/failure-patterns.md": "# Failure patterns\n\nPreserve failure evidence.\n",
                "validators/validate.py": "def validate(report):\n    return bool(report.get('passed'))\n",
                "evals/cases.json": json.dumps({"cases": [{"fixture_id": "heldout"}]}),
                "trace2skill.json": json.dumps(metadata, sort_keys=True),
            },
        }


if __name__ == "__main__":
    unittest.main()
