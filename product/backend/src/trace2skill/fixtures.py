from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .security import sanitize


EXPECTED_FIXTURES = {
    "train-npm-peer-conflict": "training",
    "train-npm-lockfile-drift": "training",
    "train-npm-missing-dev-dependency": "training",
    "heldout-npm-peer-conflict": "held-out",
    "heldout-pnpm-frozen-lockfile": "held-out",
    "heldout-pnpm-missing-build-dependency": "held-out",
}
IGNORED_PARTS = {"node_modules", ".git", ".trace2skill-cache"}
ALLOWED_PROGRAMS = {"npm", "pnpm", "node"}


def snapshot_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def file_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        result[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


@dataclass(frozen=True, slots=True)
class Fixture:
    root: Path
    manifest: dict[str, Any]

    @property
    def fixture_id(self) -> str:
        return str(self.manifest["fixture_id"])

    @property
    def repository(self) -> Path:
        return self.root / "repo"


@dataclass(frozen=True, slots=True)
class ReproductionReceipt:
    receipt_id: str
    fixture_id: str
    snapshot_hash: str
    command: list[str]
    exit_code: int
    matched_patterns: list[str]
    output_hash: str


@dataclass(frozen=True, slots=True)
class PreparedFixture:
    fixture: Fixture
    workspace: Path
    cache: Path
    initial_files: dict[str, str]
    receipt: ReproductionReceipt


class FixtureCatalog:
    def __init__(self, root: Path):
        self.root = Path(root)

    def load(self) -> dict[str, Fixture]:
        fixtures: dict[str, Fixture] = {}
        for manifest_path in sorted(self.root.glob("*/*/host.json")):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            fixture = Fixture(manifest_path.parent, manifest)
            if fixture.fixture_id in fixtures:
                raise ValueError(f"duplicate fixture id: {fixture.fixture_id}")
            if manifest.get("schema_version") != "0.3":
                raise ValueError(f"unsupported fixture schema: {fixture.fixture_id}")
            if snapshot_hash(fixture.repository) != manifest["initial_snapshot_hash"]:
                raise ValueError(f"fixture snapshot changed: {fixture.fixture_id}")
            fixtures[fixture.fixture_id] = fixture
        if {key: item.manifest["split"] for key, item in fixtures.items()} != EXPECTED_FIXTURES:
            raise ValueError("fixture catalog must contain the exact six planned tasks")
        return fixtures


class FixtureRunner:
    def __init__(self, runtime_root: Path, timeout_seconds: int = 60):
        self.runtime_root = Path(runtime_root)
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = timeout_seconds

    def prepare(self, fixture: Fixture) -> PreparedFixture:
        run_root = Path(tempfile.mkdtemp(prefix=f"{fixture.fixture_id}-", dir=self.runtime_root))
        workspace = run_root / "workspace"
        cache = run_root / "cache"
        shutil.copytree(fixture.repository, workspace)
        cache.mkdir()
        initial_files = file_hashes(workspace)
        materialized_hash = snapshot_hash(workspace)
        if materialized_hash != fixture.manifest["initial_snapshot_hash"]:
            raise RuntimeError(f"materialized fixture hash mismatch: {fixture.fixture_id}")
        result = self._run(workspace, cache, fixture.manifest["commands"]["reproduce"])
        output = f"{result.stdout}\n{result.stderr}"
        matched = [pattern for pattern in fixture.manifest["error_patterns"] if pattern.lower() in output.lower()]
        if result.returncode == 0 or not matched:
            raise RuntimeError(
                f"fixture {fixture.fixture_id} did not reproduce its declared failure: exit={result.returncode}"
            )
        receipt = ReproductionReceipt(
            receipt_id=f"repro_{uuid.uuid4().hex}",
            fixture_id=fixture.fixture_id,
            snapshot_hash=materialized_hash,
            command=list(fixture.manifest["commands"]["reproduce"]),
            exit_code=result.returncode,
            matched_patterns=matched,
            output_hash=hashlib.sha256(output.encode()).hexdigest(),
        )
        return PreparedFixture(fixture, workspace, cache, initial_files, receipt)

    def validate(self, prepared: PreparedFixture) -> dict[str, Any]:
        if prepared.receipt.fixture_id != prepared.fixture.fixture_id:
            raise ValueError("reproduction receipt belongs to another fixture")
        safety = self._validate_changes(prepared)
        stages: dict[str, Any] = {}
        passed = safety["passed"]
        for stage in ("install", "build", "test"):
            if not passed:
                stages[stage] = {"status": "skipped", "reason": "safety gate failed"}
                continue
            result = self._run(
                prepared.workspace,
                prepared.cache,
                prepared.fixture.manifest["commands"][stage],
            )
            output = f"{result.stdout}\n{result.stderr}"
            stages[stage] = {
                "status": "passed" if result.returncode == 0 else "failed",
                "command": list(prepared.fixture.manifest["commands"][stage]),
                "exit_code": result.returncode,
                "output_hash": hashlib.sha256(output.encode()).hexdigest(),
            }
            passed = passed and result.returncode == 0
        final_reproduction = self._run(
            prepared.workspace,
            prepared.cache,
            prepared.fixture.manifest["commands"]["reproduce"],
        )
        final_output = f"{final_reproduction.stdout}\n{final_reproduction.stderr}"
        original_error_absent = not any(
            pattern.lower() in final_output.lower()
            for pattern in prepared.fixture.manifest["error_patterns"]
        )
        passed = passed and final_reproduction.returncode == 0 and original_error_absent
        return sanitize(
            {
                "schema_version": "0.3",
                "fixture_id": prepared.fixture.fixture_id,
                "passed": passed,
                "reproduction": prepared.receipt.__dict__
                if hasattr(prepared.receipt, "__dict__")
                else {
                    key: getattr(prepared.receipt, key)
                    for key in prepared.receipt.__dataclass_fields__
                },
                "safety": safety,
                "stages": stages,
                "original_error_absent": original_error_absent,
                "final_reproduce_exit_code": final_reproduction.returncode,
            }
        )

    def synchronize_pnpm_lockfile(self, prepared: PreparedFixture) -> dict[str, Any]:
        if prepared.fixture.manifest["package_manager"] != "pnpm":
            raise ValueError("lockfile synchronization is only valid for pnpm fixtures")
        command = ["pnpm", "install", "--offline", "--lockfile-only", "--ignore-scripts"]
        result = self._run(prepared.workspace, prepared.cache, command, ci=False)
        output = f"{result.stdout}\n{result.stderr}"
        return sanitize(
            {
                "stage": "lockfile-sync",
                "command": command,
                "exit_code": result.returncode,
                "output_hash": hashlib.sha256(output.encode()).hexdigest(),
                "passed": result.returncode == 0,
            }
        )

    def execute_agent_diagnostics(
        self, prepared: PreparedFixture, commands: list[list[str]], *, limit: int = 16
    ) -> list[dict[str, Any]]:
        """Execute the MVP's bounded read-file tool requests and return audit receipts.

        Execution Agents currently communicate through Matrix rather than receiving an
        unrestricted host shell.  A diagnostic request is therefore treated as a tool
        call only after the host has checked it against this narrow contract and really
        read the file from the isolated workspace.
        """
        if len(commands) > limit:
            raise ValueError(f"too many diagnostic tool calls: {len(commands)} > {limit}")
        receipts: list[dict[str, Any]] = []
        workspace = prepared.workspace.resolve()
        for sequence, command in enumerate(commands, start=1):
            if not isinstance(command, list) or not command or not all(
                isinstance(argument, str) for argument in command
            ):
                raise ValueError(f"invalid diagnostic tool call: {command}")
            program = command[0].lower()
            if program in {"npm", "pnpm"}:
                operation = command[1].lower() if len(command) > 1 else ""
                forbidden = {"--force", "--legacy-peer-deps", "--no-frozen-lockfile"}
                modifying_without_dry_run = operation in {"ci", "install"} and "--dry-run" not in command
                if (
                    operation not in {"ci", "install", "list"}
                    or forbidden.intersection(command)
                    or modifying_without_dry_run
                ):
                    raise ValueError(f"diagnostic package-manager command is not allowed: {command}")
                result = self._run(prepared.workspace, prepared.cache, command)
                output = f"{result.stdout}\n{result.stderr}".encode()
                receipts.append(
                    sanitize(
                        {
                            "sequence": sequence,
                            "tool": program,
                            "arguments": command[1:],
                            "status": "succeeded" if result.returncode == 0 else "failed",
                            "exit_code": result.returncode,
                            "output_hash": hashlib.sha256(output).hexdigest(),
                            "output_bytes": len(output),
                        }
                    )
                )
                continue
            if len(command) != 2 or program not in {"cat", "type", "get-content", "read_file"}:
                raise ValueError(f"diagnostic command is outside the bounded contract: {command}")
            relative = Path(command[1])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"unsafe diagnostic path: {command[1]}")
            target = (workspace / relative).resolve()
            try:
                target.relative_to(workspace)
            except ValueError as exc:
                raise ValueError(f"diagnostic path escapes the workspace: {command[1]}") from exc
            if not target.is_file() or any(part in IGNORED_PARTS for part in relative.parts):
                raise ValueError(f"diagnostic file is unavailable: {command[1]}")
            content = target.read_bytes()
            receipts.append(
                sanitize(
                    {
                        "sequence": sequence,
                        "tool": "read_file",
                        "arguments": [relative.as_posix()],
                        "status": "succeeded",
                        "exit_code": 0,
                        "output_hash": hashlib.sha256(content).hexdigest(),
                        "output_bytes": len(content),
                    }
                )
            )
        return receipts

    def _validate_changes(self, prepared: PreparedFixture) -> dict[str, Any]:
        current_files = file_hashes(prepared.workspace)
        changed = sorted(
            path
            for path in set(prepared.initial_files) | set(current_files)
            if prepared.initial_files.get(path) != current_files.get(path)
        )
        safety = prepared.fixture.manifest["safety"]
        disallowed = [
            path
            for path in changed
            if not any(fnmatch.fnmatch(path, pattern) for pattern in safety["allowed_files"])
        ]
        field_violations: dict[str, list[str]] = {}
        for path in changed:
            if Path(path).name != "package.json" or path not in prepared.initial_files or path not in current_files:
                continue
            before = json.loads((prepared.fixture.repository / path).read_text(encoding="utf-8"))
            after = json.loads((prepared.workspace / path).read_text(encoding="utf-8"))
            changed_fields = sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))
            invalid = [key for key in changed_fields if key not in safety["allowed_package_fields"]]
            if invalid:
                field_violations[path] = invalid
        return {
            "passed": not disallowed and not field_violations,
            "changed_files": changed,
            "disallowed_files": disallowed,
            "field_violations": field_violations,
            "reference_patch_visible": any("reference" in part.lower() for part in prepared.workspace.parts),
        }

    def _run(
        self, workspace: Path, cache: Path, command: list[str], *, ci: bool = True
    ) -> subprocess.CompletedProcess[str]:
        if not command or command[0] not in ALLOWED_PROGRAMS:
            raise ValueError(f"program not allowed: {command[0] if command else '<empty>'}")
        executable = self._resolve_program(command[0])
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "TEMP": str(cache),
            "TMP": str(cache),
            "npm_config_cache": str(cache / "npm"),
            "npm_config_ignore_scripts": "true",
            "npm_config_audit": "false",
            "npm_config_fund": "false",
            "npm_config_offline": "true",
            "PNPM_HOME": str(cache / "pnpm-home"),
            "XDG_CACHE_HOME": str(cache / "xdg"),
            "CI": "true" if ci else "false",
        }
        return subprocess.run(
            [executable, *command[1:]],
            cwd=workspace,
            env=environment,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=self.timeout_seconds,
            shell=False,
            check=False,
        )

    @staticmethod
    def _resolve_program(program: str) -> str:
        override = os.environ.get(f"TRACE2SKILL_{program.upper()}")
        if override:
            return override
        resolved = shutil.which(program)
        if resolved:
            return resolved
        known = {
            "npm": Path(r"C:\Program Files\nodejs\npm.cmd"),
            "node": Path(r"C:\Program Files\nodejs\node.exe"),
            "pnpm": Path.home()
            / ".cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback/pnpm.cmd",
        }[program]
        if known.exists():
            return str(known)
        raise RuntimeError(
            f"required program is unavailable: {program}; add it to PATH or set "
            f"TRACE2SKILL_{program.upper()}"
        )
