from __future__ import annotations

import unittest

from trace2skill.analysis import compact_trace


class AnalysisTests(unittest.TestCase):
    def test_compact_view_preserves_real_event_ids_and_removes_repeated_spec(self) -> None:
        trace = {
            "trace_id": "trace-one",
            "fixture_id": "fixture",
            "fixture_hash": "a" * 64,
            "commands": [{"command": ["npm", "ci"]}],
            "reproduction_receipt": {"exit_code": 1},
            "agent_result": {"diagnosis": "lock drift"},
            "changed_files": ["package.json"],
            "validator_result": {
                "passed": True,
                "stages": {},
                "original_error_absent": True,
            },
            "messages": [
                {"source_event_id": "dispatch", "sender_role": "human", "body": "large spec"},
                {
                    "source_event_id": "result",
                    "sender_role": "worker",
                    "body": 'TRACE2SKILL_RESULT {"ok":true}',
                },
            ],
        }
        compact = compact_trace(trace)
        self.assertEqual(compact["package_manager"], "npm")
        self.assertEqual(
            [event["event_id"] for event in compact["evidence_events"]],
            ["dispatch", "result"],
        )
        self.assertNotIn("large spec", str(compact))


if __name__ == "__main__":
    unittest.main()
