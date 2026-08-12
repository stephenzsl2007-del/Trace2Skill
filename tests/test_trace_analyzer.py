from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).parents[1]


def load_analyzer():
    path = ROOT / "scripts" / "analyze_trace.py"
    spec = importlib.util.spec_from_file_location("trace_analyzer", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TraceAnalyzerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analyzer = load_analyzer()
        cls.trace = json.loads((ROOT / "work" / "traces" / "trace-task-001.json").read_text(encoding="utf-8"))

    def test_real_trace_generates_low_confidence_candidate(self):
        candidate = self.analyzer.build_candidate(copy.deepcopy(self.trace))
        self.assertEqual("candidate", candidate["status"])
        self.assertEqual("low", candidate["confidence"]["level"])
        self.assertLess(candidate["confidence"]["score"], 0.6)
        self.assertIn("Only one training trace supports this candidate", candidate["confidence"]["limitations"])

    def test_conflicting_recommendations_are_preserved(self):
        candidate = self.analyzer.build_candidate(copy.deepcopy(self.trace))
        recommendations = candidate["evidence"]["diagnostic_recommendations"]
        self.assertEqual({"npm ls", "npm install --dry-run"}, {item["command"] for item in recommendations})
        selected = [item for item in recommendations if item["selected"]]
        self.assertEqual(1, len(selected))
        self.assertEqual("npm ls", selected[0]["command"])
        self.assertEqual("worker", selected[0]["actor_role"])

    def test_tool_errors_are_retained_as_bounded_lessons(self):
        candidate = self.analyzer.build_candidate(copy.deepcopy(self.trace))
        lessons = candidate["evidence"]["failure_lessons"]
        self.assertEqual(self.trace["metrics"]["tool_error_count"], sum(item["count"] for item in lessons))
        serialized = json.dumps(candidate)
        self.assertNotIn("matrix-local.hiclaw.io", serialized)
        self.assertNotIn("/root/hiclaw-fs", serialized)

    def test_failed_run_is_rejected(self):
        trace = copy.deepcopy(self.trace)
        trace["run"]["status"] = "failed"
        with self.assertRaisesRegex(self.analyzer.TraceEligibilityError, "successful traces"):
            self.analyzer.build_candidate(trace)

    def test_failed_semantic_validation_is_rejected(self):
        trace = copy.deepcopy(self.trace)
        trace["validation"]["passed"] = False
        with self.assertRaisesRegex(self.analyzer.TraceEligibilityError, "validation.passed"):
            self.analyzer.build_candidate(trace)

    def test_failed_secret_scan_is_rejected(self):
        trace = copy.deepcopy(self.trace)
        trace["security"]["secret_scan_passed"] = False
        with self.assertRaisesRegex(self.analyzer.TraceEligibilityError, "security boundary"):
            self.analyzer.build_candidate(trace)

    def test_missing_lifecycle_evidence_is_rejected(self):
        trace = copy.deepcopy(self.trace)
        trace["validation"]["checks"] = [item for item in trace["validation"]["checks"] if item["name"] != "worker_completed"]
        with self.assertRaisesRegex(self.analyzer.TraceEligibilityError, "required evidence"):
            self.analyzer.build_candidate(trace)

    def test_mislabeled_lifecycle_evidence_is_rejected(self):
        trace = copy.deepcopy(self.trace)
        check = next(item for item in trace["validation"]["checks"] if item["name"] == "worker_completed")
        check["evidence_event_ids"] = ["evt-0038"]
        with self.assertRaisesRegex(self.analyzer.TraceEligibilityError, "does not reference"):
            self.analyzer.build_candidate(trace)

    def test_unsupported_package_manager_is_rejected(self):
        trace = copy.deepcopy(self.trace)
        trace["task"]["input"]["package_manager"] = "bun"
        with self.assertRaisesRegex(self.analyzer.TraceEligibilityError, "Unsupported"):
            self.analyzer.build_candidate(trace)

    def test_cross_manager_recommendation_is_rejected(self):
        trace = copy.deepcopy(self.trace)
        trace["task"]["input"]["package_manager"] = "pnpm"
        with self.assertRaisesRegex(self.analyzer.TraceEligibilityError, "declared package manager pnpm"):
            self.analyzer.build_candidate(trace)

    def test_matching_pnpm_read_only_recommendation_is_accepted(self):
        trace = copy.deepcopy(self.trace)
        trace["task"]["input"]["package_manager"] = "pnpm"
        for event in trace["events"]:
            if event["event_id"] in {"evt-0002", "evt-0069"}:
                event["payload"]["content"] = '{"first_diagnostic_step": "pnpm list"}'
        candidate = self.analyzer.build_candidate(trace)
        selected = next(item for item in candidate["evidence"]["diagnostic_recommendations"] if item["selected"])
        self.assertEqual("pnpm", candidate["task_signature"]["package_manager"])
        self.assertEqual("pnpm list", selected["command"])

    def test_non_package_manager_command_is_rejected(self):
        trace = copy.deepcopy(self.trace)
        for event in trace["events"]:
            if event["event_id"] in {"evt-0002", "evt-0069"}:
                event["payload"]["content"] = '{"first_diagnostic_step": "Remove-Item -Recurse -Force project"}'
        with self.assertRaisesRegex(self.analyzer.TraceEligibilityError, "declared package manager npm"):
            self.analyzer.build_candidate(trace)

    def test_shell_chaining_is_rejected(self):
        trace = copy.deepcopy(self.trace)
        for event in trace["events"]:
            if event["event_id"] in {"evt-0002", "evt-0069"}:
                event["payload"]["content"] = '{"first_diagnostic_step": "npm ls; Remove-Item project"}'
        with self.assertRaisesRegex(self.analyzer.TraceEligibilityError, "shell control"):
            self.analyzer.build_candidate(trace)

    def test_multiline_command_injection_is_rejected(self):
        self.assertRaisesRegex(
            self.analyzer.TraceEligibilityError,
            "shell control",
            self.analyzer._normalize_diagnostic_command,
            "npm ls\nRemove-Item project",
            "npm",
        )

    def test_environment_expansion_is_rejected(self):
        self.assertRaisesRegex(
            self.analyzer.TraceEligibilityError,
            "shell control",
            self.analyzer._normalize_diagnostic_command,
            "npm ls $env:SECRET",
            "npm",
        )

    def test_mutating_subcommand_is_rejected(self):
        trace = copy.deepcopy(self.trace)
        for event in trace["events"]:
            if event["event_id"] in {"evt-0002", "evt-0069"}:
                event["payload"]["content"] = '{"first_diagnostic_step": "npm uninstall lodash"}'
        with self.assertRaisesRegex(self.analyzer.TraceEligibilityError, "mutating npm"):
            self.analyzer.build_candidate(trace)

    def test_forbidden_bypass_flag_is_rejected(self):
        trace = copy.deepcopy(self.trace)
        for event in trace["events"]:
            if event["event_id"] in {"evt-0002", "evt-0069"}:
                event["payload"]["content"] = '{"first_diagnostic_step": "npm install --dry-run --legacy-peer-deps"}'
        with self.assertRaisesRegex(self.analyzer.TraceEligibilityError, "forbidden flags"):
            self.analyzer.build_candidate(trace)

    def test_generation_is_byte_deterministic(self):
        candidate = self.analyzer.build_candidate(copy.deepcopy(self.trace))
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.json"
            second = Path(directory) / "second.json"
            self.analyzer.write_outputs(candidate, first, None)
            self.analyzer.write_outputs(candidate, second, None)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_uninitialized_skill_dir_fails_before_output_write(self):
        candidate = self.analyzer.build_candidate(copy.deepcopy(self.trace))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "candidate.json"
            with self.assertRaisesRegex(ValueError, "initialized"):
                self.analyzer.write_outputs(candidate, output, Path(directory) / "not-a-skill")
            self.assertFalse(output.exists())

    def test_candidate_schema_rejects_published_status(self):
        candidate = self.analyzer.build_candidate(copy.deepcopy(self.trace))
        candidate["status"] = "published"
        with self.assertRaisesRegex(ValueError, "schema validation"):
            self.analyzer.validate_candidate(candidate)

    def test_candidate_schema_rejects_confidence_above_one(self):
        candidate = self.analyzer.build_candidate(copy.deepcopy(self.trace))
        candidate["confidence"]["score"] = 1.1
        with self.assertRaisesRegex(ValueError, "schema validation"):
            self.analyzer.validate_candidate(candidate)

    def test_malformed_json_has_contextual_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.json"
            path.write_text('{"events": [', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "valid UTF-8 JSON"):
                self.analyzer.load_json(path)


if __name__ == "__main__":
    unittest.main()
