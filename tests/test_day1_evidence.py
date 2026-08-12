from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]


class Day1EvidenceTests(unittest.TestCase):
    def test_versionable_attestation_matches_sanitized_trace(self):
        evidence = json.loads((ROOT / "evidence" / "day1-evidence.json").read_text(encoding="utf-8"))
        trace_path = ROOT / evidence["sanitized_trace"]["path"]
        digest = hashlib.sha256(trace_path.read_bytes()).hexdigest()
        self.assertEqual("passed", evidence["status"])
        self.assertEqual(evidence["sanitized_trace"]["sha256"], digest)
        self.assertEqual(102, evidence["sanitized_trace"]["event_count"])
        self.assertTrue(evidence["sanitized_trace"]["secret_scan_passed"])
        self.assertFalse(evidence["sanitized_trace"]["contains_chain_of_thought"])

    def test_attestation_contains_no_local_matrix_identifiers(self):
        content = (ROOT / "evidence" / "day1-evidence.json").read_text(encoding="utf-8")
        self.assertNotIn("matrix-local.hiclaw.io", content)
        self.assertNotIn("room_id", content)
        self.assertNotIn("event_id", content)
        self.assertFalse(json.loads(content)["security"]["contains_credentials"])


if __name__ == "__main__":
    unittest.main()
