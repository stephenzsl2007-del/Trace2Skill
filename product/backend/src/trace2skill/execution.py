from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .agent_specs import AgentSpec
from .agentteams import AgentTeamsClient, WorkerHandle
from .fixtures import Fixture, FixtureRunner, PreparedFixture, file_hashes
from .matrix_audit import MatrixAuditClient
from .security import sanitize


RESULT_MARKER = "TRACE2SKILL_RESULT"


def repository_context(repository: Path) -> str:
    blocks: list[str] = []
    for path in sorted(item for item in repository.rglob("*") if item.is_file()):
        relative = path.relative_to(repository).as_posix()
        if relative.startswith(("node_modules/", ".git/")):
            continue
        content = path.read_text(encoding="utf-8")
        if len(content) > 12_000:
            raise ValueError(f"fixture file is too large for the bounded task: {relative}")
        blocks.append(f"FILE {relative}\n{content}")
    return "\n\n".join(blocks)


def build_execution_spec(
    fixture: Fixture,
    prepared: PreparedFixture,
    task_id: str,
    role_spec: AgentSpec,
    skill_md: str | None = None,
    skill_version: str | None = None,
) -> str:
    manifest = fixture.manifest
    skill_context = ""
    if skill_md is not None:
        skill_context = f"""

LOADED SKILL VERSION: {skill_version}

Use the loaded Skill below as the bounded operating procedure. Do not add package-manager knowledge or commands that are absent from it. If its Preconditions do not cover this repository, report that limitation and return the original package.json unchanged so the host Validator can expose the capability gap.

<loaded-skill>
{skill_md}
</loaded-skill>
"""
    return f"""# Trace2Skill v0.3 execution task

TASK ID: {task_id}
FIXTURE ID: {fixture.fixture_id}
ROLE SPEC HASH: {role_spec.content_hash}
PACKAGE MANAGER: {manifest['package_manager']}
EXPECTED FAILURE STAGE: {manifest['expected_failure_stage']}
OBSERVED ERROR PATTERNS: {json.dumps(manifest['error_patterns'])}
REPRODUCTION EXIT CODE: {prepared.receipt.exit_code}
REPRODUCTION OUTPUT HASH: {prepared.receipt.output_hash}

Diagnose this JavaScript CI dependency failure from the repository files below. Propose the smallest safe package.json repair. Preserve every unrelated field. Do not use network access, lifecycle scripts, force, legacy-peer-deps, or hidden host data. The host will apply the proposal and the deterministic Validator will decide success.

Taskflow acknowledgement is optional for this MVP. Do not call submit_task and do not narrate. Your response must be exactly the literal marker `{RESULT_MARKER} ` followed by one compact JSON object with exactly these fields:

- task_id
- fixture_id
- diagnosis
- diagnostic_commands
- proposed_package_json
- verification_commands

`diagnostic_commands` and `verification_commands` must be arrays of argument arrays. `proposed_package_json` must be the complete package.json object. Do not use Markdown fences or add text before or after the marker.
{skill_context}

REPOSITORY FILES

{repository_context(prepared.workspace)}
"""


class ExecutionRunner:
    def __init__(
        self,
        agentteams: AgentTeamsClient,
        matrix: MatrixAuditClient,
        fixture_runner: FixtureRunner,
        role_spec: AgentSpec,
    ) -> None:
        self.agentteams = agentteams
        self.matrix = matrix
        self.fixture_runner = fixture_runner
        self.role_spec = role_spec

    def run(
        self,
        fixture: Fixture,
        worker: WorkerHandle,
        timeout_seconds: int = 180,
        *,
        skill_md: str | None = None,
        skill_version: str | None = None,
    ) -> dict[str, Any]:
        prepared = self.fixture_runner.prepare(fixture)
        task_id = f"v03-{fixture.fixture_id}-{uuid.uuid4().hex[:8]}"
        started_at = datetime.now(UTC).isoformat()
        assignment: dict[str, Any] | None = None
        events: list[dict[str, Any]] = []
        result: dict[str, Any] | None = None
        validation: dict[str, Any] | None = None
        repair_steps: list[dict[str, Any]] = []
        agent_tool_calls: list[dict[str, Any]] = []
        terminal = "failed"
        error: dict[str, str] | None = None
        try:
            specification = build_execution_spec(
                fixture, prepared, task_id, self.role_spec, skill_md, skill_version
            )
            assignment = self.agentteams.create_finite_task(
                worker,
                task_id,
                f"repair {fixture.fixture_id}",
                specification,
                inline_spec=True,
            )
            result, events = self.matrix.wait_for_worker_result(
                task_id, RESULT_MARKER, timeout_seconds=timeout_seconds
            )
            self._validate_result(result, task_id, fixture.fixture_id)
            agent_tool_calls = self.fixture_runner.execute_agent_diagnostics(
                prepared, result["diagnostic_commands"]
            )
            package_path = prepared.workspace / "package.json"
            package_path.write_text(
                json.dumps(result["proposed_package_json"], ensure_ascii=False, separators=(",", ":"))
                + "\n",
                encoding="utf-8",
            )
            if skill_version and skill_version.startswith("2.") and fixture.manifest["package_manager"] == "pnpm":
                sync = self.fixture_runner.synchronize_pnpm_lockfile(prepared)
                repair_steps.append(sync)
                if not sync["passed"]:
                    raise RuntimeError("safe pnpm lockfile synchronization failed")
            validation = self.fixture_runner.validate(prepared)
            terminal = "completed" if validation["passed"] else "failed"
            if not validation["passed"]:
                error = {
                    "type": "ValidatorFailure",
                    "message": "host install/build/test acceptance gate failed",
                }
            return self._trace(
                task_id,
                fixture,
                prepared,
                worker,
                started_at,
                assignment,
                events,
                result,
                validation,
                terminal,
                error,
                skill_version,
                repair_steps,
                agent_tool_calls,
            )
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            events = events or self.matrix.task_events(task_id)
            return self._trace(
                task_id,
                fixture,
                prepared,
                worker,
                started_at,
                assignment,
                events,
                result,
                validation,
                terminal,
                error,
                skill_version,
                repair_steps,
                agent_tool_calls,
            )
        finally:
            try:
                self.agentteams.finalize_task(task_id, terminal)
            except Exception:
                pass

    def recover(
        self,
        fixture: Fixture,
        worker: WorkerHandle,
        task_id: str,
        assignment: dict[str, Any] | None,
        started_at: str,
    ) -> dict[str, Any]:
        prepared = self.fixture_runner.prepare(fixture)
        result, events = self.matrix.wait_for_worker_result(task_id, RESULT_MARKER, timeout_seconds=5)
        self._validate_result(result, task_id, fixture.fixture_id)
        (prepared.workspace / "package.json").write_text(
            json.dumps(result["proposed_package_json"], ensure_ascii=False, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        validation = self.fixture_runner.validate(prepared)
        terminal = "completed" if validation["passed"] else "failed"
        self.agentteams.finalize_task(task_id, terminal)
        return self._trace(
            task_id,
            fixture,
            prepared,
            worker,
            started_at,
            assignment,
            events,
            result,
            validation,
            terminal,
            None if validation["passed"] else {"type": "ValidatorFailure", "message": "host validation failed"},
            agent_tool_calls=self.fixture_runner.execute_agent_diagnostics(
                prepared, result["diagnostic_commands"]
            ),
        )

    @staticmethod
    def _validate_result(result: dict[str, Any], task_id: str, fixture_id: str) -> None:
        expected = {
            "task_id", "fixture_id", "diagnosis", "diagnostic_commands",
            "proposed_package_json", "verification_commands",
        }
        if set(result) != expected:
            raise ValueError(f"Worker result fields differ from contract: {sorted(set(result) ^ expected)}")
        if result["task_id"] != task_id or result["fixture_id"] != fixture_id:
            raise ValueError("Worker result identity mismatch")
        if not isinstance(result["proposed_package_json"], dict):
            raise ValueError("Worker proposal must be a package.json object")
        for field in ("diagnostic_commands", "verification_commands"):
            commands = result[field]
            if not isinstance(commands, list) or not all(
                isinstance(command, list) and command and all(isinstance(arg, str) for arg in command)
                for command in commands
            ):
                raise ValueError(f"Worker {field} must contain argument arrays")

    @staticmethod
    def _trace(
        task_id: str,
        fixture: Fixture,
        prepared: PreparedFixture,
        worker: WorkerHandle,
        started_at: str,
        assignment: dict[str, Any] | None,
        events: list[dict[str, Any]],
        result: dict[str, Any] | None,
        validation: dict[str, Any] | None,
        terminal: str,
        error: dict[str, str] | None,
        skill_version: str | None = None,
        repair_steps: list[dict[str, Any]] | None = None,
        agent_tool_calls: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        changed = sorted(
            path
            for path in set(prepared.initial_files) | set(file_hashes(prepared.workspace))
            if prepared.initial_files.get(path) != file_hashes(prepared.workspace).get(path)
        )
        trace_id = f"trace_{uuid.uuid4().hex}"
        commands: list[dict[str, Any]] = [
            {
                "stage": "reproduce",
                "command": prepared.receipt.command,
                "exit_code": prepared.receipt.exit_code,
                "output_hash": prepared.receipt.output_hash,
            }
        ]
        if validation:
            for stage, detail in validation["stages"].items():
                commands.append({"stage": stage, **detail})
        return sanitize(
            {
                "schema_version": "0.3",
                "trace_id": trace_id,
                "run_id": task_id,
                "task_id": task_id,
                "fixture_id": fixture.fixture_id,
                "fixture_hash": fixture.manifest["initial_snapshot_hash"],
                "agent_role": "execution",
                "condition": "skill-assisted" if skill_version else "baseline",
                "skill_version": skill_version,
                "worker": {
                    "name": worker.name,
                    "runtime": worker.runtime,
                    "model": worker.model,
                    "spec_hash": worker.spec_hash,
                },
                "assignment": assignment,
                "messages": events,
                "tool_calls": (agent_tool_calls or [])
                + [event for event in events if "🔧" in str(event.get("body", ""))],
                "commands": commands,
                "command_results": commands,
                "repair_steps": repair_steps or [],
                "agent_result": result,
                "changed_files": changed,
                "validator_result": validation,
                "reproduction_receipt": asdict(prepared.receipt),
                "started_at": started_at,
                "ended_at": datetime.now(UTC).isoformat(),
                "status": "succeeded" if terminal == "completed" else "failed",
                "error": error,
            }
        )
