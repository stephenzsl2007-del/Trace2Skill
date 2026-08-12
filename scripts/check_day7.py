#!/usr/bin/env python3
"""Strict completion gate for the Trace2Skill Day 7 competition package."""

from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).parents[1]
SHOWCASE = ROOT / "showcase" / "trace2skill-demo.html"
MANIFEST = ROOT / "showcase" / "showcase-manifest.json"
SOLUTION = ROOT / "docs" / "competition-solution.md"
FORBIDDEN = ("matrix-local.hiclaw.io", "@admin:", "@manager:", "C:\\Users\\", "HICLAW_ADMIN_PASSWORD")
REQUIRED_SOLUTION_TERMS = (
    "AgentTeams",
    "角色编排",
    "任务拆解",
    "上下文传递",
    "协同执行",
    "状态追踪",
    "Skill",
    "MCP",
    "RAG",
    "Nacos",
    "Higress",
    "PolarDB",
    "RocketMQ",
    "LoongSuite",
    "AgentScope Studio",
    "AgentLoop",
    "UnifiedModel",
    "权限",
    "可替换性",
    "迁移成本",
    "staged-local",
)


class StructureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: dict[str, int] = {}
        self.external_attributes: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags[tag] = self.tags.get(tag, 0) + 1
        for name, value in attrs:
            if name in {"src", "href", "action"} and value and ("://" in value or value.startswith("//")):
                self.external_attributes.append(value)


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


def validate_showcase() -> None:
    builder = load_module("day7_gate_builder", ROOT / "scripts" / "build_day7_showcase.py")
    with tempfile.TemporaryDirectory(prefix="trace2skill-day7-") as directory:
        generated = Path(directory)
        expected_manifest = builder.build_showcase(generated)
        generated_html = generated / SHOWCASE.name
        generated_manifest = generated / MANIFEST.name
        if SHOWCASE.read_bytes() != generated_html.read_bytes():
            raise RuntimeError("Day 7 showcase is stale or not byte-deterministic")
        if MANIFEST.read_bytes() != generated_manifest.read_bytes():
            raise RuntimeError("Day 7 showcase manifest is stale or not byte-deterministic")

    html_bytes = SHOWCASE.read_bytes()
    html = html_bytes.decode("utf-8")
    if len(html_bytes) > 50_000:
        raise RuntimeError("Day 7 showcase unexpectedly exceeds 50 KB")
    if hashlib.sha256(html_bytes).hexdigest() != expected_manifest["artifact"]["sha256"]:
        raise RuntimeError("Day 7 showcase hash mismatch")
    if any(token in html for token in FORBIDDEN):
        raise RuntimeError("Day 7 showcase leaks a local identifier")
    parser = StructureParser()
    parser.feed(html)
    if parser.external_attributes:
        raise RuntimeError(f"Day 7 showcase has external dependencies: {parser.external_attributes}")
    if parser.tags.get("html") != 1 or parser.tags.get("main") != 1 or parser.tags.get("h1") != 1:
        raise RuntimeError("Day 7 showcase semantic structure is incomplete")
    if parser.tags.get("script", 0) != 0:
        raise RuntimeError("Day 7 showcase must not contain scripts")

    stored_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    claims = stored_manifest.get("contract", {}).get("claims", {})
    if claims.get("efficiency_improvement") != "not-claimed" or claims.get("statistical_significance") != "not-claimed":
        raise RuntimeError("Day 7 showcase overstates the available evidence")
    if claims.get("registry_publication") != "staged-local":
        raise RuntimeError("Day 7 showcase must not claim public publication")
    for source in stored_manifest.get("sources", []):
        path = ROOT / source["file"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
            raise RuntimeError(f"Day 7 source attestation mismatch: {source['file']}")
        if source["file"].startswith("work/"):
            raise RuntimeError("Day 7 manifest must not depend on ignored runtime evidence")


def validate_solution() -> None:
    text = SOLUTION.read_text(encoding="utf-8")
    missing = [term for term in REQUIRED_SOLUTION_TERMS if term not in text]
    if missing:
        raise RuntimeError(f"Competition solution is missing required coverage: {', '.join(missing)}")
    if "当前不使用" not in text or "MVP 不使用" not in text:
        raise RuntimeError("Competition solution does not explain optional-component boundaries")
    if "不宣称统计显著性" not in text or "不交给 Agent 自证" not in text:
        raise RuntimeError("Competition solution does not preserve evidence boundaries")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--npm")
    parser.add_argument("--pnpm")
    args = parser.parse_args()
    validate_showcase()
    validate_solution()
    run(sys.executable, "-m", "unittest", "tests.test_day7_showcase", "-v")
    day6_args = [sys.executable, str(ROOT / "scripts" / "check_day6.py")]
    if args.npm:
        day6_args.extend(["--npm", args.npm])
    if args.pnpm:
        day6_args.extend(["--pnpm", args.pnpm])
    run(*day6_args)
    print("[PASS] Day 7 gate: evidence-backed offline showcase and competition package verified")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError, FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(1)
