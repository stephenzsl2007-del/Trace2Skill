from __future__ import annotations

import base64
import json
import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from .agent_specs import AgentSpec
from .security import sanitize


AGENTTEAMS_VERSION = "v1.1.2"
AGENTTEAMS_COMMIT = "a994578"
CONTROLLER = "hiclaw-controller"
MANAGER = "hiclaw-manager"


class CommandRunner(Protocol):
    def run(self, arguments: list[str], timeout: int = 60, input_text: str | None = None) -> str: ...


class WorkerProvisioner(Protocol):
    def request_worker(self, name: str, runtime: str, model: str, identity: str) -> str: ...

    def verify_human_visible_worker_room(self, room_id: str, worker_name: str) -> bool: ...

    def send(self, room_id: str, body: str) -> str: ...


class SubprocessCommandRunner:
    def run(self, arguments: list[str], timeout: int = 60, input_text: str | None = None) -> str:
        completed = subprocess.run(
            arguments,
            input=input_text,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            shell=False,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"AgentTeams command failed with exit {completed.returncode}: {arguments[:4]}"
            )
        return completed.stdout


@dataclass(frozen=True, slots=True)
class WorkerHandle:
    name: str
    role: str
    model: str
    runtime: str
    matrix_user_id: str
    room_id: str
    external_state: str
    spec_hash: str


class AgentTeamsClient:
    def __init__(
        self,
        runner: CommandRunner | None = None,
        provisioner: WorkerProvisioner | None = None,
    ):
        self.runner = runner or SubprocessCommandRunner()
        self.provisioner = provisioner

    def health(self) -> dict[str, Any]:
        manager = self._json(
            ["docker", "exec", CONTROLLER, "hiclaw", "get", "managers", "default", "-o", "json"]
        )
        workers = self._json(
            ["docker", "exec", CONTROLLER, "hiclaw", "get", "workers", "-o", "json"]
        )
        image = str(manager.get("image", ""))
        passed = (
            manager.get("phase") == "Running"
            and manager.get("model") == "qwen-plus"
            and image.endswith(f":{AGENTTEAMS_VERSION}")
        )
        return sanitize(
            {
                "passed": passed,
                "framework": "AgentTeams",
                "pinned_version": AGENTTEAMS_VERSION,
                "pinned_commit": AGENTTEAMS_COMMIT,
                "manager": {
                    "phase": manager.get("phase"),
                    "model": manager.get("model"),
                    "runtime": manager.get("runtime"),
                    "image": image,
                    "welcome_sent": manager.get("welcomeSent"),
                },
                "worker_count": workers.get("total", 0),
            }
        )

    def get_worker(self, name: str, role: str, spec_hash: str = "fixed-worker") -> WorkerHandle:
        self.runner.run(
            ["docker", "exec", CONTROLLER, "hiclaw", "worker", "ensure-ready", "--name", name],
            timeout=120,
        )
        current = self._json(
            [
                "docker", "exec", CONTROLLER, "hiclaw", "worker", "status",
                "--name", name, "-o", "json",
            ]
        )
        record = current.get("worker", current)
        if (
            record.get("containerState") != "running"
            or record.get("phase") not in {"Ready", "Running"}
            or not record.get("matrixUserID")
            or not record.get("roomID")
        ):
            raise RuntimeError(f"fixed Worker is not task-ready: {name}")
        if self.provisioner and not self.provisioner.verify_human_visible_worker_room(
            str(record["roomID"]), name
        ):
            raise RuntimeError(f"fixed Worker room is not visible to the human administrator: {name}")
        return WorkerHandle(
            name=name,
            role=role,
            model=str(record.get("model", "")),
            runtime=str(record.get("runtime", "")),
            matrix_user_id=str(record["matrixUserID"]),
            room_id=str(record["roomID"]),
            external_state=str(record["phase"]),
            spec_hash=spec_hash,
        )

    def create_worker(self, spec: AgentSpec, run_id: str) -> WorkerHandle:
        role_slug = spec.role.replace("trace-", "trace-").replace("skill-", "skill-")
        suffix = uuid.uuid4().hex[:6]
        run_slug = re.sub(r"[^a-z0-9]", "", run_id.lower())[-8:] or "run"
        name = f"t2s-{role_slug[:12]}-{run_slug}-{suffix}"
        if self.provisioner:
            self.provisioner.request_worker(name, spec.runtime, spec.model, spec.prompt)
            value: dict[str, Any] = {}
        else:
            value = self._json(
                [
                    "docker", "exec", CONTROLLER, "hiclaw", "create", "worker",
                    "--name", name, "--runtime", spec.runtime, "--model", spec.model,
                    "--identity", spec.prompt, "--wait-timeout", f"{spec.timeout_seconds}s",
                    "-o", "json",
                ],
                timeout=spec.timeout_seconds + 30,
            )
        deadline = time.monotonic() + spec.timeout_seconds
        record = value.get("worker", value)
        ensure_requested = False
        while time.monotonic() < deadline:
            try:
                current = self._json(
                    [
                        "docker", "exec", CONTROLLER, "hiclaw", "get", "workers", name,
                        "-o", "json",
                    ]
                )
            except RuntimeError:
                time.sleep(1)
                continue
            record = current.get("worker", current)
            if not ensure_requested:
                self.runner.run(
                    [
                        "docker", "exec", CONTROLLER, "hiclaw", "worker", "ensure-ready",
                        "--name", name,
                    ],
                    timeout=spec.timeout_seconds,
                )
                ensure_requested = True
            phase = str(record.get("phase", record.get("status", "")))
            if (
                phase == "Running"
                and record.get("containerState", "running") == "running"
                and record.get("matrixUserID")
                and record.get("roomID")
            ):
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"fresh Worker did not become task-ready: {name}")
        if self.provisioner and not self.provisioner.verify_human_visible_worker_room(
            str(record["roomID"]), name
        ):
            raise RuntimeError(f"fresh Worker room is not visible to the human administrator: {name}")
        return WorkerHandle(
            name=name,
            role=spec.role,
            model=spec.model,
            runtime=spec.runtime,
            matrix_user_id=str(record.get("matrixUserID", "")),
            room_id=str(record.get("roomID", "")),
            external_state="Running",
            spec_hash=spec.content_hash,
        )

    def delete_worker(self, worker: WorkerHandle) -> None:
        self.runner.run(
            ["docker", "exec", CONTROLLER, "hiclaw", "delete", "worker", worker.name],
            timeout=90,
        )

    def create_finite_task(
        self,
        worker: WorkerHandle,
        task_id: str,
        title: str,
        specification: str,
        *,
        inline_spec: bool = False,
    ) -> dict[str, str]:
        if not worker.room_id or not worker.matrix_user_id:
            raise ValueError("Worker has no Matrix routing information")
        assigned_at = datetime.now(UTC).isoformat()
        metadata = {
            "task_id": task_id,
            "project_id": "trace2skill-v0.3",
            "task_title": title,
            "assigned_to": worker.name,
            "room_id": worker.room_id,
            "status": "assigned",
            "depends_on": [],
            "assigned_at": assigned_at,
        }
        writer = (
            "import base64,sys;from pathlib import Path;"
            "p=Path('/root/hiclaw-fs/shared/tasks')/sys.argv[1];p.mkdir(parents=True,exist_ok=True);"
            "(p/'meta.json').write_bytes(base64.b64decode(sys.argv[2]));"
            "(p/'spec.md').write_bytes(base64.b64decode(sys.argv[3]))"
        )
        self.runner.run(
            [
                "docker", "exec", MANAGER, "python", "-c", writer, task_id,
                base64.b64encode(json.dumps(metadata, sort_keys=True).encode()).decode(),
                base64.b64encode(specification.encode()).decode(),
            ],
            timeout=60,
        )
        remote = f"hiclaw/hiclaw-storage/shared/tasks/{task_id}"
        local = f"/root/hiclaw-fs/shared/tasks/{task_id}"
        self.runner.run(["docker", "exec", MANAGER, "mc", "cp", f"{local}/meta.json", f"{remote}/meta.json"])
        self.runner.run(["docker", "exec", MANAGER, "mc", "cp", f"{local}/spec.md", f"{remote}/spec.md"])
        self.runner.run(
            [
                "docker", "exec", MANAGER, "bash",
                "/opt/hiclaw/agent/skills/task-management/scripts/manage-state.sh",
                "--action", "add-finite", "--task-id", task_id, "--title", title,
                "--assigned-to", worker.name, "--room-id", worker.room_id,
            ]
        )
        if inline_spec:
            notice = (
                f"{worker.matrix_user_id} New finite task [{task_id}]: {title}. "
                "The exact shared task specification is included below. Execute it now and return the "
                "required bounded result; taskflow acknowledgement is optional for this MVP.\n\n"
                f"{specification}"
            )
        else:
            notice = (
                f"{worker.matrix_user_id} New finite task [{task_id}]: {title}. "
                "Use taskflow ack_task to pull the shared spec and return the requested bounded result."
            )
        if self.provisioner:
            request_event_id = self.provisioner.send(worker.room_id, notice)
        else:
            output = self.runner.run(
                [
                    "docker", "exec", MANAGER, "copaw", "channels", "send",
                    "--agent-id", "default", "--channel", "matrix",
                    "--target-session", worker.room_id, "--target-user", worker.matrix_user_id,
                    "--text", notice,
                ]
            )
            if not output.strip():
                raise RuntimeError("AgentTeams message command returned no delivery evidence")
            request_event_id = output.strip()
        return sanitize(
            {
                "task_id": task_id,
                "room_id": worker.room_id,
                "assigned_at": assigned_at,
                "request_event_id": request_event_id,
            }
        )

    def nudge_submit(self, worker: WorkerHandle, task_id: str) -> str:
        if not self.provisioner:
            raise RuntimeError("bounded task nudge requires an auditable Matrix provisioner")
        body = (
            f"{worker.matrix_user_id} Continue finite task [{task_id}] from the acknowledged state. "
            "Do not narrate or repeat the diagnosis. Call taskflow submit_task now with the exact JSON "
            "result required by the task spec, then report TASK_COMPLETED."
        )
        return self.provisioner.send(worker.room_id, body)

    def wait_for_result(
        self, task_id: str, timeout_seconds: int, poll_seconds: float = 2.0
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        reader = (
            "import base64,sys;from pathlib import Path;"
            "r=Path('/root/hiclaw-fs/shared/tasks')/sys.argv[1];"
            "ps=[r/'workspace'/'result.json',r/'result.md'];"
            "p=next((x for x in ps if x.exists()),None);"
            "print(base64.b64encode(p.read_bytes()).decode()) if p else sys.exit(3)"
        )
        while time.monotonic() < deadline:
            try:
                remote = f"hiclaw/hiclaw-storage/shared/tasks/{task_id}/"
                local = f"/root/hiclaw-fs/shared/tasks/{task_id}/"
                try:
                    self.runner.run(
                        ["docker", "exec", MANAGER, "mc", "mirror", remote, local, "--overwrite"],
                        timeout=20,
                    )
                except RuntimeError:
                    pass
                encoded = self.runner.run(
                    ["docker", "exec", MANAGER, "python", "-c", reader, task_id], timeout=20
                ).strip()
                value = self._parse_result(base64.b64decode(encoded).decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("Worker result must be a JSON object")
                return sanitize(value)
            except (RuntimeError, ValueError, json.JSONDecodeError):
                time.sleep(poll_seconds)
        raise TimeoutError(f"AgentTeams task timed out: {task_id}")

    def finalize_task(self, task_id: str, status: str) -> None:
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError(f"invalid finite task terminal status: {status}")
        updater = (
            "import json,sys;from datetime import datetime,timezone;from pathlib import Path;"
            "p=Path('/root/hiclaw-fs/shared/tasks')/sys.argv[1]/'meta.json';"
            "x=json.loads(p.read_text());x['status']=sys.argv[2];"
            "x['completed_at']=datetime.now(timezone.utc).isoformat();"
            "p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\\n')"
        )
        self.runner.run(
            ["docker", "exec", MANAGER, "python", "-c", updater, task_id, status], timeout=20
        )
        self.runner.run(
            [
                "docker", "exec", MANAGER, "mc", "cp",
                f"/root/hiclaw-fs/shared/tasks/{task_id}/meta.json",
                f"hiclaw/hiclaw-storage/shared/tasks/{task_id}/meta.json",
            ],
            timeout=20,
        )
        self.runner.run(
            [
                "docker", "exec", MANAGER, "bash",
                "/opt/hiclaw/agent/skills/task-management/scripts/manage-state.sh",
                "--action", "complete", "--task-id", task_id,
            ],
            timeout=20,
        )

    def _json(self, arguments: list[str], timeout: int = 60) -> dict[str, Any]:
        value = json.loads(self.runner.run(arguments, timeout=timeout))
        if not isinstance(value, dict):
            raise ValueError("AgentTeams CLI did not return a JSON object")
        return value

    @staticmethod
    def _parse_result(text: str) -> dict[str, Any]:
        stripped = text.strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            lines = stripped.splitlines()
            stripped = "\n".join(lines[1:-1]).strip()
        marker = "TRACE2SKILL_RESULT"
        if marker in stripped:
            prefix, stripped = stripped.split(marker, 1)
            if prefix.strip():
                raise ValueError("unexpected text before result marker")
            stripped = stripped.lstrip(" :\t\r\n")
        value = json.loads(stripped)
        if not isinstance(value, dict):
            raise ValueError("Worker result must be one JSON object")
        return value
