from __future__ import annotations

import unittest

from trace2skill.matrix_audit import MatrixAuditClient, normalize_task_events


class MatrixAuditTests(unittest.TestCase):
    def test_duplicate_out_of_order_events_are_sanitized_and_sorted(self) -> None:
        events = [
            {"source_event_id": "b", "timestamp_ms": 2, "body": "token=abcdefghijklmnop"},
            {"source_event_id": "a", "timestamp_ms": 1, "body": "ok"},
            {"source_event_id": "a", "timestamp_ms": 1, "body": "ok"},
        ]
        result = normalize_task_events(events)
        self.assertEqual([item["source_event_id"] for item in result], ["a", "b"])
        self.assertNotIn("abcdefghijklmnop", result[1]["body"])

    def test_marker_in_acknowledged_spec_is_not_a_final_result(self) -> None:
        body = '✅ taskflow {"spec":"reply with TRACE2SKILL_RESULT"}'
        self.assertFalse(body.lstrip().startswith("TRACE2SKILL_RESULT"))

    def test_result_parser_tolerates_plain_text_after_one_json_object(self) -> None:
        client = object.__new__(MatrixAuditClient)
        client.task_events = lambda task_id: [  # type: ignore[method-assign]
            {
                "sender_role": "worker",
                "body": 'RESULT {"ok":true}\nDone.',
            }
        ]
        value, _ = client.wait_for_worker_result("task", "RESULT", timeout_seconds=1)
        self.assertEqual(value, {"ok": True})

    def test_result_parser_tolerates_fresh_worker_explanation_before_marker(self) -> None:
        client = object.__new__(MatrixAuditClient)
        client.task_events = lambda task_id: [  # type: ignore[method-assign]
            {
                "sender_role": "worker",
                "body": 'I checked the lockfile first.\nRESULT {"ok":true}',
            }
        ]
        value, _ = client.wait_for_worker_result("task", "RESULT", timeout_seconds=1)
        self.assertEqual(value, {"ok": True})

    def test_result_parser_rejects_a_second_json_object(self) -> None:
        client = object.__new__(MatrixAuditClient)
        client.task_events = lambda task_id: [  # type: ignore[method-assign]
            {
                "sender_role": "worker",
                "body": 'RESULT {"ok":true} {"second":true}',
            }
        ]
        with self.assertRaisesRegex(ValueError, "more than one structured payload"):
            client.wait_for_worker_result("task", "RESULT", timeout_seconds=1)


if __name__ == "__main__":
    unittest.main()
