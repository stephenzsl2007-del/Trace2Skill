#!/usr/bin/env python3
"""Validate answer-free Day 4 fixtures and reproduce failures with real package managers."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "fixtures" / "javascript-dependency-failures"
SCHEMA = ROOT / "schemas" / "dependency-fixture.schema.json"
SHELL_CONTROL = re.compile(r"[;&|<>`$\r\n]")
FORBIDDEN_AGENT_KEYS = {"repair", "answer", "expected_value", "target_version", "solution"}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def validator_module():
    path = ROOT / "scripts" / "validate_trace.py"
    spec = importlib.util.spec_from_file_location("day4_schema_validator", path)
    if not spec or not spec.loader:
        raise RuntimeError("Unable to load schema validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_inventory() -> list[tuple[Path, dict[str, Any]]]:
    schema = load_json(SCHEMA)
    validator = validator_module()
    result: list[tuple[Path, dict[str, Any]]] = []
    ids: set[str] = set()
    for split in ("training", "held-out"):
        for task_path in sorted((FIXTURES / split).glob("*/task.json")):
            task = load_json(task_path)
            errors = validator.validate_schema_instance(task, schema)
            if errors:
                raise ValueError(f"{task_path}: " + "; ".join(errors))
            if task["split"] != split:
                raise ValueError(f"Split mismatch: {task_path}")
            if task["fixture_id"] in ids:
                raise ValueError(f"Duplicate fixture_id: {task['fixture_id']}")
            ids.add(task["fixture_id"])
            repo = task_path.parent / "repo"
            if not (repo / "package.json").is_file():
                raise ValueError(f"Missing repo/package.json: {task_path.parent}")
            result.append((task_path.parent, task))
    if len(result) != 7:
        raise ValueError(f"Expected 7 fixtures after the Day 6 transfer addition, found {len(result)}")
    return result


def validate_answer_boundary(root: Path, task: dict[str, Any]) -> None:
    serialized = json.dumps(task, ensure_ascii=False).lower()
    if FORBIDDEN_AGENT_KEYS & set(task):
        raise ValueError(f"Validator-only answer key leaked into {task['fixture_id']}")
    for token in FORBIDDEN_AGENT_KEYS:
        if f'"{token}"' in serialized:
            raise ValueError(f"Forbidden answer field {token} in {task['fixture_id']}")
    repo = root / "repo"
    forbidden_names = {"task.json", "fixture.json", "answer.json", "solution.json"}
    leaked = [path for path in repo.rglob("*") if path.is_file() and path.name.lower() in forbidden_names]
    if leaked:
        raise ValueError(f"Answer metadata is visible inside repo: {leaked[0]}")
    for key in ("reproduce", "verify"):
        command = task[key]
        if command["program"] != task["package_manager"]:
            raise ValueError(f"{key} program does not match package_manager in {task['fixture_id']}")
        if any(SHELL_CONTROL.search(argument) for argument in command["args"]):
            raise ValueError(f"Shell control syntax in {task['fixture_id']} {key} command")


def resolve_program(program: str, npm: str | None = None, pnpm: str | None = None) -> str:
    override = npm if program == "npm" else pnpm
    if override:
        path = Path(override)
        if not path.is_file():
            raise ValueError(f"Package manager executable not found: {path}")
        return str(path)
    candidates = [f"{program}.cmd", program] if os.name == "nt" else [program]
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    raise ValueError(f"Package manager executable not found: {program}")


def isolated_package_manager_env(cache_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "npm_config_cache": str(cache_root / "npm-cache"),
        "npm_config_update_notifier": "false",
        "npm_config_audit": "false",
        "npm_config_fund": "false",
        "npm_config_store_dir": str(cache_root / "pnpm-store"),
    })
    return env


def reproduce(root: Path, task: dict[str, Any], npm: str | None = None, pnpm: str | None = None) -> dict[str, Any]:
    validate_answer_boundary(root, task)
    with tempfile.TemporaryDirectory(prefix="trace2skill-day4-fixture-") as directory:
        work = Path(directory) / "repo"
        shutil.copytree(root / "repo", work)
        command = task["reproduce"]
        program = resolve_program(command["program"], npm, pnpm)
        completed = subprocess.run(
            [program, *command["args"]],
            cwd=work,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=isolated_package_manager_env(Path(directory)),
            timeout=45,
            check=False,
        )
        matched = re.search(task["failure_pattern"], completed.stdout, re.IGNORECASE) is not None
        return {"fixture_id": task["fixture_id"], "exit_code": completed.returncode, "failure_pattern_matched": matched}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--npm")
    parser.add_argument("--pnpm")
    parser.add_argument("--skip-reproduction", action="store_true")
    args = parser.parse_args()
    failures = []
    for root, task in fixture_inventory():
        validate_answer_boundary(root, task)
        if args.skip_reproduction:
            print(f"[PASS] contract {task['fixture_id']}")
            continue
        result = reproduce(root, task, args.npm, args.pnpm)
        passed = result["exit_code"] != 0 and result["failure_pattern_matched"]
        print(f"[{'PASS' if passed else 'FAIL'}] {task['fixture_id']} exit={result['exit_code']} pattern={result['failure_pattern_matched']}")
        if not passed:
            failures.append(task["fixture_id"])
    if failures:
        print("Failed fixtures: " + ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
