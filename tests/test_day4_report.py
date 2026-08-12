from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Day4ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reporter = load_module("day4_report_tests", ROOT / "scripts" / "build_day4_report.py")
        cls.input_dir = ROOT / "evidence" / "day4"

    def test_report_is_deterministic_and_does_not_overclaim(self):
        first = self.reporter.build_report(self.input_dir)
        second = self.reporter.build_report(self.input_dir)
        self.assertEqual(self.reporter.render_report(first), self.reporter.render_report(second))
        self.assertEqual("no_measured_improvement", first["conclusion"])
        self.assertEqual(1, first["aggregate"]["baseline_success_rate"])
        self.assertEqual(1, first["aggregate"]["skill_success_rate"])
        self.assertGreater(first["aggregate"]["duration_delta_ms"], 0)

    def test_source_hashes_attest_exact_evidence_files(self):
        report = self.reporter.build_report(self.input_dir)
        for source in report["sources"]:
            actual = hashlib.sha256((self.input_dir / source["file"]).read_bytes()).hexdigest()
            self.assertEqual(actual, source["sha256"])

    def test_versioned_evidence_contains_no_raw_matrix_identifiers(self):
        paths = [*self.input_dir.glob("*.json"), ROOT / "evidence" / "day4-report.json"]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("matrix-local.hiclaw.io", text)
            self.assertNotIn("@admin:", text)
            self.assertNotIn("C:\\Users\\", text)


if __name__ == "__main__":
    unittest.main()
