from __future__ import annotations

import unittest

from trace2skill.evaluation import failure_report_from_trace


class EvaluationTests(unittest.TestCase):
    def test_failure_report_contains_only_observable_fields(self) -> None:
        trace = {
            "fixture_id": "heldout-pnpm-frozen-lockfile",
            "skill_version": "1.0.0-candidate.1",
            "reproduction_receipt": {"command": ["pnpm", "install"]},
            "messages": [{"source_event_id": "event", "body": "TRACE2SKILL_RESULT {}"}],
            "validator_result": {
                "stages": {
                    "install": {
                        "status": "failed",
                        "command": ["pnpm", "install", "--frozen-lockfile"],
                        "exit_code": 1,
                        "output_hash": "a" * 64,
                    }
                }
            },
        }
        report = failure_report_from_trace(trace)
        self.assertEqual(report["error_category"], "skill-package-manager-scope-gap")
        self.assertNotIn("proposed_package_json", report)
        self.assertEqual(report["evidence_refs"], ["matrix:event"])


if __name__ == "__main__":
    unittest.main()
