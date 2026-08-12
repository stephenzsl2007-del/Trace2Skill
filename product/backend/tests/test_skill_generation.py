from __future__ import annotations

import unittest

from trace2skill.agent_specs import AgentSpec
from trace2skill.skill_generation import (
    compact_experience_for_skill,
    render_v1_candidate,
    validate_v1_blueprint,
)
from trace2skill.skill_packages import V1, SkillPackageValidator


class SkillGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.refs = {"trace:t1#e1", "trace:t2#e2", "trace:t3#e3"}
        self.experience = {
            "experience_id": "experience-1",
            "trace_ids": ["t1", "t2", "t3"],
        }
        self.spec = AgentSpec(
            role="skill-engineer",
            runtime="copaw",
            model="qwen-plus",
            timeout_seconds=300,
            prompt="generate",
            allowed_tools=("taskflow",),
            permissions={},
            content_hash="a" * 64,
        )

    def test_blueprint_compiles_to_a_valid_six_file_skill(self) -> None:
        blueprint = validate_v1_blueprint(self._blueprint(), self.refs, "task-1")
        candidate = render_v1_candidate(blueprint, self.experience, self.spec)
        package = SkillPackageValidator().validate_candidate(
            candidate, expected_version=V1, allowed_evidence_refs=self.refs
        )
        self.assertEqual(package.version, V1)
        self.assertEqual(len(package.files), 6)
        self.assertNotIn("pnpm", package.files["SKILL.md"].lower())
        self.assertIn("hidden host data", package.files["SKILL.md"].lower())

    def test_blueprint_rejects_unobserved_package_manager(self) -> None:
        blueprint = self._blueprint()
        blueprint["diagnostic_workflow"].append("Run pnpm install")
        with self.assertRaisesRegex(ValueError, "unobserved"):
            validate_v1_blueprint(blueprint, self.refs, "task-1")

    def test_blueprint_preserves_unobserved_manager_as_generic_guardrail(self) -> None:
        blueprint = self._blueprint()
        blueprint["prohibited_actions"].append("Do not support pnpm or other unobserved managers.")
        clean = validate_v1_blueprint(blueprint, self.refs, "task-1")
        self.assertNotIn("pnpm", " ".join(clean["prohibited_actions"]).lower())
        self.assertIn("not observed", " ".join(clean["prohibited_actions"]).lower())

    def test_compact_experience_excludes_redundant_conclusions(self) -> None:
        experience = {
            **self.experience,
            "scope": {},
            "task_signatures": [],
            "success_paths": [],
            "preconditions": [],
            "tools_permissions": [],
            "prohibited_actions": [],
            "validator_rules": [],
            "conclusions": [{"statement": "redundant"}],
        }
        compact = compact_experience_for_skill(experience)
        self.assertNotIn("conclusions", compact)

    def _blueprint(self) -> dict:
        return {
            "task_id": "task-1",
            "description": "Diagnose npm dependency installation and build failures in CI.",
            "trigger_conditions": ["Use when npm install, build, or module resolution fails."],
            "preconditions": ["Work in an isolated repository snapshot with an offline cache."],
            "diagnostic_workflow": ["Reproduce the failure, inspect package files, and make the smallest repair."],
            "tool_requirements": ["Use npm, Node.js, and read-only file inspection."],
            "prohibited_actions": [
                "Never use force or legacy-peer-deps.",
                "Never enable lifecycle scripts or network access.",
                "Never read hidden host data or change unrelated files.",
            ],
            "validation_rules": ["Require install, build, and tests to pass."],
            "decision_tree": [
                {"when": "npm ci reports lock drift", "then": "Align the declared dependency", "evidence_refs": ["trace:t1#e1"]}
            ],
            "failure_patterns": [
                {"signature": "ERESOLVE", "diagnosis": "peer conflict", "repair": "align compatible versions", "evidence_refs": ["trace:t2#e2"]}
            ],
            "eval_scenarios": [
                {"name": "generic lock drift", "failure_class": "lockfile-drift"}
            ],
            "evidence_refs": ["trace:t1#e1", "trace:t2#e2"],
        }


if __name__ == "__main__":
    unittest.main()
