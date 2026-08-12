#!/usr/bin/env python3
"""Strict completion gate for Trace2Skill Day 5."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).parents[1]
SKILL = ROOT / "skills" / "diagnose-javascript-dependency-failures"
V2 = ROOT / "skill-versions" / "diagnose-javascript-dependency-failures" / "v2" / "SKILL.md"
REPORT = ROOT / "evidence" / "day5-report.json"
LOCAL_TOOLS = ROOT / ".trace2skill-tools"
QUICK_VALIDATE = Path.home() / ".codex" / "skills" / ".system" / "skill-creator" / "scripts" / "quick_validate.py"
FORBIDDEN = ("matrix-local.hiclaw.io", "@admin:", "@manager:", "C:\\Users\\", "HICLAW_ADMIN_PASSWORD")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tree_digest(root: Path) -> str:
    hasher = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        hasher.update(path.relative_to(root).as_posix().encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def run(*args: str) -> None:
    print("+", " ".join(args))
    subprocess.run(args, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--npm")
    parser.add_argument("--pnpm")
    args = parser.parse_args()
    builder = load_module("day5_gate_report", ROOT / "scripts" / "build_day5_report.py")
    expected = builder.render_report(builder.build_report())
    if not REPORT.is_file() or REPORT.read_bytes() != expected:
        raise RuntimeError("Day 5 report is stale; rerun build_day5_report.py")
    report = builder.build_report()
    if report["versions"]["v2_final_sha256"] != hashlib.sha256(V2.read_bytes()).hexdigest():
        raise RuntimeError("Day 5 report no longer attests the immutable evaluated v2 snapshot")

    sys.path.insert(0, str(LOCAL_TOOLS))
    validator = load_module("day5_skill_validator", QUICK_VALIDATE)
    valid, message = validator.validate_skill(SKILL)
    print(message)
    if not valid:
        raise RuntimeError(message)

    for path in [*sorted((ROOT / "evidence" / "day5").glob("*.json")), REPORT]:
        text = path.read_text(encoding="utf-8")
        leaked = [token for token in FORBIDDEN if token in text]
        if leaked:
            raise RuntimeError(f"Versioned evidence contains local identifier {leaked[0]}: {path}")

    before = tree_digest(SKILL)
    run(sys.executable, str(ROOT / "scripts" / "check_day3.py"))
    day4 = [sys.executable, str(ROOT / "scripts" / "check_day4.py")]
    if args.npm:
        day4.extend(["--npm", args.npm])
    if args.pnpm:
        day4.extend(["--pnpm", args.pnpm])
    run(*day4)
    if tree_digest(SKILL) != before:
        raise RuntimeError("A prerequisite gate mutated the current v2 Skill")
    print("[PASS] Day 5 gate: failure preserved, v2 correction verified, no transfer or efficiency overclaim")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(1)
