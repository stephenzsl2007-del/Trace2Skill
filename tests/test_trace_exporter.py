from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "export_agentteams_trace.py"
SPEC = importlib.util.spec_from_file_location("trace_exporter", MODULE_PATH)
assert SPEC and SPEC.loader
trace_exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trace_exporter)

VALIDATOR_PATH = Path(__file__).parents[1] / "scripts" / "validate_trace.py"
VALIDATOR_SPEC = importlib.util.spec_from_file_location("trace_validator", VALIDATOR_PATH)
assert VALIDATOR_SPEC and VALIDATOR_SPEC.loader
trace_validator = importlib.util.module_from_spec(VALIDATOR_SPEC)
VALIDATOR_SPEC.loader.exec_module(trace_validator)


class TraceExporterTests(unittest.TestCase):
    def test_redacts_common_secret_shapes(self) -> None:
        raw = 'Authorization: Bearer abcdefghijklmnop; "password":"hunter2"; key=sk-ws-abcdefghijk'
        cleaned, count = trace_exporter.redact_text(raw)
        self.assertGreaterEqual(count, 3)
        self.assertNotIn("hunter2", cleaned)
        self.assertNotIn("abcdefghijklmnop", cleaned)
        self.assertNotIn("sk-ws-abcdefghijk", cleaned)

    def test_builds_verified_worker_trace_and_pairs_tools(self) -> None:
        room = "!worker:matrix"
        messages = [
            {
                "room_id": "!admin:matrix",
                "source_event_id": "$1",
                "timestamp_ms": 1_000,
                "sender": "@admin:matrix",
                "body": "Trace2Skill Day 1 multi-agent smoke task trace-task-001",
            },
            {
                "room_id": room,
                "source_event_id": "$2",
                "timestamp_ms": 2_000,
                "sender": "@manager:matrix",
                "body": "trace-worker Task assigned: trace-task-001 package manager npm",
            },
            {
                "room_id": room,
                "source_event_id": "$3",
                "timestamp_ms": 3_000,
                "sender": "@trace-worker:matrix",
                "body": '🔧 **taskflow**\n```\n{"action":"ack_task","payload":{"taskId":"trace-task-001"}}\n```',
            },
            {
                "room_id": room,
                "source_event_id": "$4",
                "timestamp_ms": 4_000,
                "sender": "@trace-worker:matrix",
                "body": '✅ **taskflow**:\n{"ok": true, "action": "ack_task", "task": {"task_id":"trace-task-001"}}',
            },
            {
                "room_id": room,
                "source_event_id": "$5",
                "timestamp_ms": 5_000,
                "sender": "@trace-worker:matrix",
                "body": '🔧 **taskflow**\n```\n{"action":"submit_task","payload":{"taskId":"trace-task-001"}}\n```',
            },
            {
                "room_id": room,
                "source_event_id": "$6",
                "timestamp_ms": 6_000,
                "sender": "@trace-worker:matrix",
                "body": '✅ **taskflow**:\n{"ok": true, "action": "submit_task", "verified": true, "result":{"package_manager":"npm"}}',
            },
            {
                "room_id": room,
                "source_event_id": "$7",
                "timestamp_ms": 7_000,
                "sender": "@trace-worker:matrix",
                "body": "manager TASK_COMPLETED: trace-task-001 - npm ERESOLVE diagnosed",
            },
        ]
        trace = trace_exporter.build_trace(messages, "trace-task-001", "trace-worker")
        self.assertEqual([], trace_exporter.validate_trace(trace))
        self.assertEqual("success", trace["run"]["status"])
        self.assertTrue(trace["validation"]["passed"])

        calls = {event["call_id"] for event in trace["events"] if event["type"] == "tool_call"}
        results = {
            event["call_id"]
            for event in trace["events"]
            if event["type"] in {"tool_result", "tool_error", "task_acknowledged", "task_submitted"}
        }
        self.assertTrue(calls)
        self.assertTrue(calls.issubset(results))

    def test_real_schema_file_is_valid_json(self) -> None:
        schema_path = Path(__file__).parents[1] / "schemas" / "trace.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual("https://json-schema.org/draft/2020-12/schema", schema["$schema"])
        self.assertEqual("0.1.0", schema["properties"]["schema_version"]["const"])

    def test_schema_validator_rejects_missing_required_property(self) -> None:
        schema_path = Path(__file__).parents[1] / "schemas" / "trace.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        errors = trace_validator.validate_schema_instance({}, schema)
        self.assertTrue(any("missing required property" in error for error in errors))

    def test_invariant_validator_rejects_unpaired_tool_call(self) -> None:
        trace = {
            "schema_version": "0.1.0",
            "trace_id": "broken",
            "task": {},
            "run": {},
            "actors": [{"id": "manager"}],
            "events": [
                {
                    "sequence": 1,
                    "event_id": "evt-1",
                    "timestamp": "2026-08-09T00:00:00Z",
                    "type": "tool_call",
                    "actor_id": "manager",
                    "call_id": "call-1",
                    "provenance": {"source_event_id": "$1"},
                }
            ],
            "artifacts": [],
            "validation": {"passed": True, "checks": []},
            "metrics": {"event_count": 1, "message_count": 0, "tool_call_count": 1, "tool_error_count": 0},
            "security": {"secret_scan_passed": True},
        }
        errors = trace_exporter.validate_trace(trace)
        self.assertIn("tool calls and results are not paired one-to-one", errors)


if __name__ == "__main__":
    unittest.main()
