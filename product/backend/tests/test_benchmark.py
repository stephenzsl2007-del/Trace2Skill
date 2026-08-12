from __future__ import annotations

import copy
import unittest

from trace2skill.benchmark import BenchmarkGate, HELD_OUT, QualificationError


class BenchmarkTests(unittest.TestCase):
    def test_exact_two_loop_release_gate(self) -> None:
        records = self._records("loop-1") + self._records("loop-2")
        loops = [
            {
                "loop_id": "loop-1",
                "config_hash": "config",
                "fixture_hashes": {fixture: fixture + "-hash" for fixture in HELD_OUT},
                "worker_instance_overlap": False,
            },
            {
                "loop_id": "loop-2",
                "config_hash": "config",
                "fixture_hashes": {fixture: fixture + "-hash" for fixture in HELD_OUT},
                "worker_instance_overlap": False,
            },
        ]
        result = BenchmarkGate().qualify_release(loops, records)
        self.assertEqual([(item.v2_passed, item.v2_total) for item in result], [(9, 9), (9, 9)])
        self.assertEqual(sum(item.qualification_count for item in result), 36)

    def test_missing_repeat_duplicate_and_missing_telemetry_fail(self) -> None:
        records = self._records("loop")
        records.pop()
        with self.assertRaises(QualificationError):
            BenchmarkGate().qualify_loop("loop", records)
        records = self._records("loop")
        records[3]["metrics"]["token_usage"] = None
        with self.assertRaises(QualificationError):
            BenchmarkGate().qualify_loop("loop", records)

    def test_v1_must_expose_pnpm_and_v2_must_be_nine_of_nine(self) -> None:
        records = self._records("loop")
        next(
            item
            for item in records
            if item["stage"] == "v1-probe" and item["fixture_id"].startswith("heldout-pnpm")
        )["passed"] = True
        with self.assertRaises(QualificationError):
            BenchmarkGate().qualify_loop("loop", records)
        records = self._records("loop")
        next(
            item
            for item in records
            if item["stage"] == "qualification" and item["condition"] == "skill-v2"
        )["passed"] = False
        with self.assertRaises(QualificationError):
            BenchmarkGate().qualify_loop("loop", records)

    @staticmethod
    def _records(loop_id: str) -> list[dict]:
        metrics = {"token_usage": 10, "tool_calls": 2, "duration_ms": 100, "invalid_attempts": 0}
        records: list[dict] = []
        for fixture in HELD_OUT:
            records.append(
                {
                    "loop_id": loop_id,
                    "stage": "v1-probe",
                    "fixture_id": fixture,
                    "fixture_hash": fixture + "-hash",
                    "condition": "skill-v1",
                    "repeat_index": 1,
                    "passed": fixture == "heldout-npm-peer-conflict",
                    "config_hash": "config",
                    "answer_leak": False,
                    "unauthorized_change": False,
                    "metrics": copy.deepcopy(metrics),
                }
            )
        for fixture in HELD_OUT:
            for condition in ("baseline", "skill-v2"):
                for repeat in (1, 2, 3):
                    records.append(
                        {
                            "loop_id": loop_id,
                            "stage": "qualification",
                            "fixture_id": fixture,
                            "fixture_hash": fixture + "-hash",
                            "condition": condition,
                            "repeat_index": repeat,
                            "passed": condition == "skill-v2" or repeat != 3,
                            "config_hash": "config",
                            "answer_leak": False,
                            "unauthorized_change": False,
                            "metrics": copy.deepcopy(metrics),
                        }
                    )
        return records


if __name__ == "__main__":
    unittest.main()
