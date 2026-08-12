#!/usr/bin/env python3
"""Run the complete Day 3 release gate."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).parents[1]
TRACE = ROOT / "work" / "traces" / "trace-task-001.json"
SKILL = ROOT / "skills" / "diagnose-javascript-dependency-failures"
LOCAL_TOOLS = ROOT / ".trace2skill-tools"
QUICK_VALIDATE = Path.home() / ".codex" / "skills" / ".system" / "skill-creator" / "scripts" / "quick_validate.py"


def run(*args: str) -> None:
    print("+", " ".join(args))
    subprocess.run(args, cwd=ROOT, check=True)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_skill(skill_dir: Path) -> None:
    if not QUICK_VALIDATE.is_file():
        raise RuntimeError(f"skill-creator quick validator not found: {QUICK_VALIDATE}")
    sys.path.insert(0, str(LOCAL_TOOLS))
    try:
        spec = importlib.util.spec_from_file_location("skill_quick_validate", QUICK_VALIDATE)
        if not spec or not spec.loader:
            raise RuntimeError("Could not load skill-creator quick validator")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        valid, message = module.validate_skill(skill_dir)
        import yaml
        metadata = yaml.safe_load((skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8"))
    except ModuleNotFoundError as exc:
        if exc.name == "yaml":
            raise RuntimeError("PyYAML is missing; run: python -m pip install -r requirements-dev.txt --target .trace2skill-tools") from exc
        raise
    print(message)
    if not valid:
        raise RuntimeError(message)
    interface = metadata.get("interface", {}) if isinstance(metadata, dict) else {}
    required = {"display_name", "short_description", "default_prompt"}
    if not required.issubset(interface):
        raise RuntimeError("agents/openai.yaml is missing required interface metadata")
    if not 25 <= len(interface["short_description"]) <= 64:
        raise RuntimeError("agents/openai.yaml short_description must be 25-64 characters")
    if f"${skill_dir.name}" not in interface["default_prompt"]:
        raise RuntimeError("agents/openai.yaml default_prompt must explicitly invoke the Skill")


def tree_digest(root: Path) -> str:
    hasher = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        hasher.update(path.relative_to(root).as_posix().encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def main() -> int:
    canonical_skill_before = tree_digest(SKILL)
    run(sys.executable, str(ROOT / "scripts" / "validate_trace.py"), str(TRACE))
    run(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v")
    with tempfile.TemporaryDirectory(prefix="trace2skill-day3-") as directory:
        first = Path(directory) / "first.json"
        second = Path(directory) / "second.json"
        generated_candidate = Path(directory) / "generated.json"
        generated_skill = Path(directory) / SKILL.name
        shutil.copytree(SKILL, generated_skill)
        analyzer = str(ROOT / "scripts" / "analyze_trace.py")
        run(sys.executable, analyzer, str(TRACE), "--output", str(first))
        run(sys.executable, analyzer, str(TRACE), "--output", str(second))
        if digest(first) != digest(second):
            raise RuntimeError("Candidate generation is not byte deterministic")
        run(sys.executable, analyzer, str(TRACE), "--output", str(generated_candidate), "--skill-dir", str(generated_skill))
        if digest(generated_candidate) != digest(generated_skill / "references" / "evidence.json"):
            raise RuntimeError("Generated Skill evidence does not match generated candidate")
        validate_skill(generated_skill)
    if tree_digest(SKILL) != canonical_skill_before:
        raise RuntimeError("Day 3 gate mutated the canonical Skill")
    print("[PASS] Day 3 release gate")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(1)
