from __future__ import annotations

import copy
import unittest

from trace2skill.trace_validation import TraceValidationError, validate_execution_trace


class ExecutionTraceTests(unittest.TestCase):
    def test_success_requires_agentteams_result_and_all_validator_stages(self) -> None:
        trace = self._trace()
        self.assertEqual(validate_execution_trace(trace)["status"], "succeeded")
        broken = copy.deepcopy(trace)
        broken["validator_result"]["stages"]["test"]["status"] = "failed"
        with self.assertRaises(TraceValidationError):
            validate_execution_trace(broken)

    def test_duplicate_message_and_secret_are_rejected(self) -> None:
        duplicate = self._trace()
        duplicate["messages"].append(copy.deepcopy(duplicate["messages"][0]))
        with self.assertRaises(TraceValidationError):
            validate_execution_trace(duplicate)
        secret = self._trace()
        secret["messages"][0]["body"] = "api_key=abcdefghijklmnop"
        with self.assertRaises(TraceValidationError):
            validate_execution_trace(secret)

    def test_optional_skill_execution_fields_are_validated(self) -> None:
        trace = self._trace()
        trace.update({"condition": "skill-assisted", "skill_version": "2.0.0", "repair_steps": []})
        self.assertEqual(validate_execution_trace(trace)["condition"], "skill-assisted")
        trace["skill_version"] = None
        with self.assertRaises(TraceValidationError):
            validate_execution_trace(trace)

    def test_failed_trace_requires_structured_validator_failure(self) -> None:
        trace = self._trace()
        trace["status"] = "failed"
        trace["validator_result"]["passed"] = False
        trace["error"] = {"type": "ValidatorFailure", "message": "build failed"}
        self.assertEqual(validate_execution_trace(trace)["status"], "failed")

    @staticmethod
    def _trace() -> dict:
        return {
            "schema_version": "0.3",
            "trace_id": "trace_test",
            "run_id": "task",
            "task_id": "task",
            "fixture_id": "fixture",
            "fixture_hash": "a" * 64,
            "agent_role": "execution",
            "worker": {"name": "worker"},
            "assignment": {"request_event_id": "event"},
            "messages": [{"source_event_id": "event", "timestamp_ms": 1, "body": "ok"}],
            "tool_calls": [],
            "commands": [],
            "command_results": [],
            "agent_result": {"task_id": "task", "fixture_id": "fixture"},
            "changed_files": ["package.json"],
            "validator_result": {
                "passed": True,
                "original_error_absent": True,
                "stages": {
                    "install": {"status": "passed"},
                    "build": {"status": "passed"},
                    "test": {"status": "passed"},
                },
            },
            "reproduction_receipt": {},
            "started_at": "2026-01-01T00:00:00Z",
            "ended_at": "2026-01-01T00:01:00Z",
            "status": "succeeded",
            "error": None,
        }


if __name__ == "__main__":
    unittest.main()
