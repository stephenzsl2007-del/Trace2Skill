import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).parents[1]


def load_builder():
    path = ROOT / "scripts" / "build_day7_showcase.py"
    spec = importlib.util.spec_from_file_location("test_day7_builder", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Day7ShowcaseTests(unittest.TestCase):
    def test_day2_summary_is_chained_to_original_trace_attestation(self):
        builder = load_builder()
        summary = json.loads((ROOT / "evidence" / "day2-trace-summary.json").read_text(encoding="utf-8"))
        day1 = json.loads((ROOT / "evidence" / "day1-evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["source"]["sha256"], day1["sanitized_trace"]["sha256"])
        raw = ROOT / summary["source"]["file"]
        if raw.is_file():
            self.assertEqual(summary["source"]["sha256"], hashlib.sha256(raw.read_bytes()).hexdigest())
        self.assertEqual(102, summary["metrics"]["event_count"])
        self.assertEqual(29, summary["metrics"]["tool_call_count"])

    def test_showcase_is_deterministic_and_source_attested(self):
        builder = load_builder()
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            one = builder.build_showcase(Path(first))
            two = builder.build_showcase(Path(second))
            first_html = Path(first) / builder.HTML_FILE.name
            second_html = Path(second) / builder.HTML_FILE.name
            self.assertEqual(first_html.read_bytes(), second_html.read_bytes())
            self.assertEqual(one, two)
            self.assertEqual(one["artifact"]["sha256"], hashlib.sha256(first_html.read_bytes()).hexdigest())
            for source in one["sources"]:
                path = ROOT / source["file"]
                self.assertEqual(source["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_showcase_is_offline_and_does_not_overclaim(self):
        builder = load_builder()
        with tempfile.TemporaryDirectory() as directory:
            manifest = builder.build_showcase(Path(directory))
            html = (Path(directory) / builder.HTML_FILE.name).read_text(encoding="utf-8")
        self.assertNotIn("https://", html)
        self.assertNotIn("http://", html)
        self.assertIn("AgentTeams", html)
        self.assertIn("失败也是产品数据", html)
        self.assertEqual("not-claimed", manifest["contract"]["claims"]["efficiency_improvement"])
        self.assertEqual("staged-local", manifest["contract"]["claims"]["registry_publication"])


if __name__ == "__main__":
    unittest.main()
