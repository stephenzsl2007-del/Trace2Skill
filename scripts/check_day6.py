#!/usr/bin/env python3
"""Strict completion gate for Trace2Skill Day 6 qualification and release staging."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).parents[1]
NAME = "diagnose-javascript-dependency-failures"
VERSION = "2.0.0"
SKILL = ROOT / "skills" / NAME
RELEASE = ROOT / "release-bundles" / NAME / VERSION
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


def run(*args: str) -> None:
    print("+", " ".join(args))
    subprocess.run(args, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--npm")
    parser.add_argument("--pnpm")
    args = parser.parse_args()
    builder = load_module("day6_gate_builder", ROOT / "scripts" / "build_day6_release.py")

    with tempfile.TemporaryDirectory(prefix="trace2skill-day6-") as directory:
        generated = Path(directory)
        manifest = builder.build_release(generated)
        expected_manifest = builder.render(manifest)
        stored_manifest = RELEASE / "release-manifest.json"
        stored_zip = RELEASE / f"{NAME}-{VERSION}.zip"
        if stored_manifest.read_bytes() != expected_manifest:
            raise RuntimeError("Day 6 release manifest is stale")
        generated_zip = generated / stored_zip.name
        if stored_zip.read_bytes() != generated_zip.read_bytes():
            raise RuntimeError("Day 6 release ZIP is not byte-deterministic or is stale")
        if hashlib.sha256(stored_zip.read_bytes()).hexdigest() != manifest["package"]["sha256"]:
            raise RuntimeError("Day 6 release ZIP hash mismatch")
        if manifest["registry"]["external_write_performed"] or manifest["registry"]["state"] != "staged-local":
            raise RuntimeError("Day 6 must not claim an external registry write")
        if manifest["claims"]["efficiency_improvement"] != "not-claimed":
            raise RuntimeError("Day 6 evidence does not support an efficiency claim")

    sys.path.insert(0, str(LOCAL_TOOLS))
    validator = load_module("day6_skill_validator", QUICK_VALIDATE)
    valid, message = validator.validate_skill(SKILL)
    print(message)
    if not valid:
        raise RuntimeError(message)

    for path in [*sorted((ROOT / "evidence" / "day6").glob("*.json")), *sorted(RELEASE.glob("*"))]:
        if path.suffix.lower() not in {".json", ".zip"}:
            continue
        data = path.read_bytes()
        leaked = [token for token in FORBIDDEN if token.encode("ascii") in data]
        if leaked:
            raise RuntimeError(f"Release artifact contains local identifier {leaked[0]}: {path}")

    run(sys.executable, str(ROOT / "scripts" / "check_day5.py"), *(
        (["--npm", args.npm] if args.npm else []) + (["--pnpm", args.pnpm] if args.pnpm else [])
    ))
    run("powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "publish-day6-draft.ps1"))
    print("[PASS] Day 6 gate: independent transfer verified, deterministic bundle staged, registry write not performed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(1)
