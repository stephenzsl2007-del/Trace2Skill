#!/usr/bin/env python3
"""Strict offline release gate for Trace2Skill Day 4 evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).parents[1]
EVIDENCE = ROOT / "evidence" / "day4"
REPORT = ROOT / "evidence" / "day4-report.json"
SKILL = ROOT / "skill-versions" / "diagnose-javascript-dependency-failures" / "v1" / "SKILL.md"
FORBIDDEN = ("matrix-local.hiclaw.io", "@admin:", "@manager:", "C:\\Users\\", "HICLAW_ADMIN_PASSWORD")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--npm")
    parser.add_argument("--pnpm")
    args = parser.parse_args()
    fixtures = load_module("day4_gate_fixtures", ROOT / "scripts" / "validate_day4_fixtures.py")
    report_builder = load_module("day4_gate_report", ROOT / "scripts" / "build_day4_report.py")

    failures = []
    for root, task in fixtures.fixture_inventory():
        result = fixtures.reproduce(root, task, args.npm, args.pnpm)
        if result["exit_code"] == 0 or not result["failure_pattern_matched"]:
            failures.append(task["fixture_id"])
    if failures:
        raise RuntimeError("Fixture reproduction failed: " + ", ".join(failures))

    report = report_builder.build_report(EVIDENCE)
    expected = report_builder.render_report(report)
    if not REPORT.is_file() or REPORT.read_bytes() != expected:
        raise RuntimeError("Day 4 report is stale; rerun build_day4_report.py")
    if report["aggregate"]["skill_sha256"] != hashlib.sha256(SKILL.read_bytes()).hexdigest():
        raise RuntimeError("Day 4 evidence Skill hash does not match current SKILL.md")
    if report["conclusion"] == "measured_improvement":
        raise RuntimeError("Current pilot evidence does not support an improvement claim")

    for path in [*sorted(EVIDENCE.glob("*.json")), REPORT]:
        text = path.read_text(encoding="utf-8")
        leaked = [token for token in FORBIDDEN if token in text]
        if leaked:
            raise RuntimeError(f"Versioned evidence contains local identifier {leaked[0]}: {path}")

    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=ROOT, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("Unit test suite failed")
    print(f"[PASS] Day 4 gate: {len(fixtures.fixture_inventory())} fixtures reproducible, 4 Day 4 trials valid, report current, no improvement overclaimed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
