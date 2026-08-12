import hashlib
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]


def load_builder():
    path = ROOT / "scripts" / "build_day5_report.py"
    spec = importlib.util.spec_from_file_location("test_day5_builder", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class Day5ReportTests(unittest.TestCase):
    def test_report_preserves_negative_result_and_no_overclaim(self):
        report = load_builder().build_report()
        self.assertEqual(report["refinement"]["v2_initial_heldout_status"], "failed")
        self.assertEqual(report["conclusion"], "refinement_closed_not_independent_transfer")
        self.assertNotIn("measured_improvement", str(report))

    def test_report_sources_and_v2_snapshot_are_attested(self):
        report = load_builder().build_report()
        for source in report["sources"]:
            path = ROOT / "evidence" / "day5" / source["file"]
            self.assertEqual(source["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        v2 = ROOT / "skill-versions" / "diagnose-javascript-dependency-failures" / "v2" / "SKILL.md"
        self.assertEqual(report["versions"]["v2_final_sha256"], hashlib.sha256(v2.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
