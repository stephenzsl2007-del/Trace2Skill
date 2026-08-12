from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock
from contextlib import redirect_stdout
import io


ROOT = Path(__file__).parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Day4FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = load_module("day4_fixtures", ROOT / "scripts" / "validate_day4_fixtures.py")
        cls.trial = load_module("day4_trial", ROOT / "scripts" / "run_day4_agentteams_trial.py")

    def test_inventory_is_balanced_and_answer_free(self):
        inventory = self.fixtures.fixture_inventory()
        self.assertEqual(7, len(inventory))
        self.assertEqual(3, sum(task["split"] == "training" for _, task in inventory))
        self.assertEqual(4, sum(task["split"] == "held-out" for _, task in inventory))
        for root, task in inventory:
            self.fixtures.validate_answer_boundary(root, task)
            serialized = json.dumps(task).lower()
            self.assertNotIn('"repair"', serialized)
            self.assertFalse((root / "repo" / "task.json").exists())

    def test_commands_match_manager_and_do_not_use_shell(self):
        for _, task in self.fixtures.fixture_inventory():
            for key in ("reproduce", "verify"):
                self.assertEqual(task["package_manager"], task[key]["program"])
                self.assertNotIn("--force", task[key]["args"])
                self.assertNotIn("--legacy-peer-deps", task[key]["args"])

    def test_package_manager_cache_is_isolated(self):
        env = self.fixtures.isolated_package_manager_env(Path("isolated"))
        self.assertEqual(str(Path("isolated") / "npm-cache"), env["npm_config_cache"])
        self.assertEqual(str(Path("isolated") / "pnpm-store"), env["npm_config_store_dir"])

    def test_answer_field_is_rejected(self):
        root, task = self.fixtures.fixture_inventory()[0]
        poisoned = copy.deepcopy(task)
        poisoned["repair"] = {"to": "secret-answer"}
        with self.assertRaisesRegex(ValueError, "answer key"):
            self.fixtures.validate_answer_boundary(root, poisoned)

    def test_result_marker_parser_is_bounded(self):
        parsed = self.trial.parse_result('DAY4_RESULT {"task_id":"x"}')
        self.assertEqual({"task_id": "x"}, parsed)
        self.assertIsNone(self.trial.parse_result("no marker"))
        self.assertIsNone(self.trial.parse_result('prefix DAY4_RESULT {"task_id":"x"}'))
        self.assertIsNone(self.trial.parse_result('DAY4_RESULT {"task_id":"x"} trailing'))

    def test_worker_result_parser_accepts_object_or_json_fence_only(self):
        self.assertEqual({"task_id": "x"}, self.trial.parse_json_document('{"task_id":"x"}'))
        self.assertEqual({"task_id": "x"}, self.trial.parse_json_document('```json\n{"task_id":"x"}\n```'))
        with self.assertRaises(json.JSONDecodeError):
            self.trial.parse_json_document('prefix {"task_id":"x"}')

    def test_explicit_skill_path_is_hash_attested(self):
        root, task = self.trial.load_fixture("train-pnpm-workspace-core")
        skill = ROOT / "skill-versions" / "diagnose-javascript-dependency-failures" / "v1" / "SKILL.md"
        prompt = self.trial.build_worker_spec(root, task, "skill-assisted", "worker", "task", skill)
        self.assertIn("Diagnose JavaScript Dependency Failures", prompt)
        trace = self.trial.bounded_trace(
            task, "skill-assisted", "worker", "request", 1, [], {"ok": True},
            {"original_failure_reproduced": True, "verification_passed": True}, prompt, skill,
        )
        self.assertEqual(self.trial.digest(skill.read_bytes()), trace["skill_sha256"])

    def test_manager_room_selection_requires_a_unique_direct_message(self):
        rooms = [
            ("!worker:local", ["@admin:local", "@manager:local", "@worker:local"]),
            ("!dm:local", ["@admin:local", "@manager:local"]),
        ]
        self.assertEqual("!dm:local", self.trial.select_manager_dm(rooms, "@admin:local"))
        with self.assertRaisesRegex(RuntimeError, "not found"):
            self.trial.select_manager_dm(rooms[:1], "@admin:local")
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            self.trial.select_manager_dm([rooms[1], ("!dm2:local", rooms[1][1])], "@admin:local")

    def test_task_meta_matches_agentteams_contract(self):
        meta = self.trial.build_task_meta("task", "title", "worker", "!room", "2026-01-01T00:00:00Z")
        self.assertEqual(
            {"task_id", "project_id", "task_title", "assigned_to", "room_id", "status", "depends_on", "assigned_at"},
            set(meta),
        )
        self.assertEqual("", meta["project_id"])
        self.assertEqual("!room", meta["room_id"])

    def test_protocol_collector_initializes_nudge_counter(self):
        class Client:
            def sync(self, since, timeout):
                return {"next_batch": "next", "rooms": {"join": {}}}

        class Runtime:
            worker = "worker"
            nudges = 0

            def read_result(self, task_id):
                return None

            def nudge(self, task_id, room_id):
                self.nudges += 1

        runtime = Runtime()
        with redirect_stdout(io.StringIO()):
            with mock.patch.object(self.trial.time, "monotonic", side_effect=[0, 0, 0, 121, 121, 2]):
                with self.assertRaises(TimeoutError):
                    self.trial.collect_protocol_trial(Client(), runtime, "s", 0, "task", "room", 1, [])
        self.assertEqual(1, runtime.nudges)

    def test_proposal_rejects_protected_metadata_and_unsafe_specs(self):
        original = {"name": "x", "private": True, "dependencies": {"a": "1.0.0"}}
        with self.assertRaisesRegex(ValueError, "protected metadata"):
            self.trial.validate_proposal(original, {**original, "name": "y", "dependencies": {"a": "2.0.0"}})
        with self.assertRaisesRegex(ValueError, "unsafe dependency"):
            self.trial.validate_proposal(original, {**original, "dependencies": {"a": "https://example.invalid/a.tgz"}})
        with self.assertRaisesRegex(ValueError, "unsafe dependency"):
            self.trial.validate_proposal(original, {**original, "pnpm": {"overrides": {"a": "git+ssh://bad"}}})

    def test_trial_requires_reproduced_failure_and_successful_verification(self):
        self.assertTrue(self.trial.trial_passed({"original_failure_reproduced": True, "verification_passed": True}))
        self.assertFalse(self.trial.trial_passed({"original_failure_reproduced": False, "verification_passed": True}))
        self.assertFalse(self.trial.trial_passed({"original_failure_reproduced": True, "verification_passed": False}))

    def test_rejection_category_is_bounded(self):
        self.assertEqual(
            "protected-metadata-or-no-dependency-change",
            self.trial.rejection_category(ValueError("changed protected metadata")),
        )
        self.assertEqual("unsafe-dependency-spec", self.trial.rejection_category(ValueError("unsafe dependency")))
        self.assertEqual("agentteams-timeout", self.trial.rejection_category(TimeoutError("late")))
        self.assertEqual("invalid-worker-result", self.trial.rejection_category(ValueError("other")))

    def test_timeout_without_worker_result_has_versionable_failure_validation(self):
        validation = self.trial.rejected_validation(
            {"exit_code": 1, "failure_pattern_matched": True}, None, TimeoutError("late"),
        )
        self.assertTrue(validation["original_failure_reproduced"])
        self.assertFalse(validation["verification_passed"])
        self.assertEqual("agentteams-timeout", validation["proposal_rejection"])
        self.assertEqual(64, len(validation["proposed_package_json_sha256"]))


if __name__ == "__main__":
    unittest.main()
