#!/usr/bin/env python3
"""Run one answer-free Day 4 repair trial through AgentTeams Manager and Worker."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import uuid


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "fixtures" / "javascript-dependency-failures"
SKILL = ROOT / "skills" / "diagnose-javascript-dependency-failures" / "SKILL.md"
MATRIX_BASE = "http://127.0.0.1:18080"
RESULT_MARKER = "DAY4_RESULT"
ALLOWED_MUTATIONS = {"dependencies", "devDependencies", "peerDependencies", "optionalDependencies", "overrides", "pnpm"}
UNSAFE_SPEC = re.compile(r"(?:https?://|git\+|[;&|<>`$\r\n])", re.IGNORECASE)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def digest(value: bytes | str) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def build_task_meta(task_id: str, title: str, worker: str, room_id: str, assigned_at: str) -> dict[str, Any]:
    """Build the exact AgentTeams v1.1.2 TaskMeta contract used by taskflow."""
    return {
        "task_id": task_id,
        "project_id": "",
        "task_title": title,
        "assigned_to": worker,
        "room_id": room_id,
        "status": "assigned",
        "depends_on": [],
        "assigned_at": assigned_at,
    }


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def load_fixture(fixture_id: str) -> tuple[Path, dict[str, Any]]:
    matches = list(FIXTURES.glob(f"*/{fixture_id}/task.json"))
    if len(matches) != 1:
        raise ValueError(f"Expected one fixture named {fixture_id}, found {len(matches)}")
    return matches[0].parent, load_json(matches[0])


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


class MatrixClient:
    def __init__(self, base: str, username: str, password: str):
        self.base = base.rstrip("/")
        login = self.request("POST", "/_matrix/client/v3/login", {
            "type": "m.login.password",
            "identifier": {"type": "m.id.user", "user": username},
            "password": password,
        }, authenticated=False)
        self.token = login["access_token"]
        self.user_id = login["user_id"]

    token: str = ""

    def request(self, method: str, path: str, body: dict[str, Any] | None = None, authenticated: bool = True) -> dict[str, Any]:
        data = canonical(body) if body is not None else None
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RuntimeError(f"Matrix request failed: {method} {path}: {exc}") from exc

    def manager_room(self) -> str:
        joined = self.request("GET", "/_matrix/client/v3/joined_rooms")
        candidates: list[tuple[str, list[str]]] = []
        for room_id in joined.get("joined_rooms", []):
            members = self.request("GET", f"/_matrix/client/v3/rooms/{quote(room_id, safe='')}/joined_members")
            users = list(members.get("joined", {}))
            candidates.append((room_id, users))
        return select_manager_dm(candidates, self.user_id)

    def sync(self, since: str | None, timeout_ms: int) -> dict[str, Any]:
        query = {"timeout": str(timeout_ms)}
        if since:
            query["since"] = since
        return self.request("GET", "/_matrix/client/v3/sync?" + urlencode(query))

    def send(self, room_id: str, text: str) -> str:
        transaction = f"trace2skill-day4-{uuid.uuid4().hex}"
        result = self.request(
            "PUT",
            f"/_matrix/client/v3/rooms/{quote(room_id, safe='')}/send/m.room.message/{transaction}",
            {"msgtype": "m.text", "body": text},
        )
        return result["event_id"]


def select_manager_dm(candidates: list[tuple[str, list[str]]], self_user_id: str) -> str:
    """Select the admin/Manager direct-message room, never a Worker group room."""
    direct_rooms = [
        room_id
        for room_id, users in candidates
        if self_user_id in users
        and len(users) == 2
        and any(user.startswith("@manager:") for user in users)
    ]
    if len(direct_rooms) == 1:
        return direct_rooms[0]
    if not direct_rooms:
        raise RuntimeError("Manager direct-message Matrix room was not found")
    raise RuntimeError(f"Manager direct-message Matrix room is ambiguous ({len(direct_rooms)} candidates)")


def repository_context(repo: Path) -> str:
    blocks = []
    for path in sorted(item for item in repo.rglob("*") if item.is_file()):
        relative = path.relative_to(repo).as_posix()
        if relative.startswith(("node_modules/", ".git/")):
            continue
        content = path.read_text(encoding="utf-8")
        if len(content) > 12000:
            raise ValueError(f"Fixture file too large for AgentTeams prompt: {relative}")
        blocks.append(f"FILE {relative}\n{content}")
    return "\n\n".join(blocks)


def build_worker_spec(
    root: Path, task: dict[str, Any], condition: str, worker: str, task_id: str, skill_path: Path = SKILL,
) -> str:
    context = repository_context(root / "repo")
    skill_text = skill_path.read_text(encoding="utf-8") if condition == "skill-assisted" else ""
    if len(skill_text) > 20000:
        raise ValueError("Candidate Skill is too large for the bounded AgentTeams task context")
    condition_text = (
        "The Worker MUST follow the Candidate Skill below.\n\n" + skill_text
        if condition == "skill-assisted"
        else "The Worker receives no reusable Skill. Solve from the task and repository evidence only."
    )
    return f"""# Trace2Skill Day 4 finite task

TASK ID: {task_id}
FIXTURE ID: {task['fixture_id']}
CONDITION: {condition}
PACKAGE MANAGER: {task['package_manager']}
DECLARED FAILURE PATTERN: {task['failure_pattern']}

{task['task_prompt']}
The repository is represented only by the files below. Do not invent or request hidden repair metadata. Do not use network access. Do not add scripts, URLs, Git dependencies, force flags, or legacy-peer-deps. Return a complete proposed package.json object; do not return a shell patch.

{condition_text}

REPOSITORY FILES
{context}

After diagnosing, do not describe a future action and do not call another tool. Your next assistant message must be exactly the literal marker `DAY4_RESULT ` followed on the same line by one compact JSON object with exactly these fields: task_id, fixture_id, condition, diagnosis, diagnostic_commands, proposed_package_json, verification_command. `diagnostic_commands` is an array of strings and `proposed_package_json` is an object. Do not wrap the JSON in Markdown and do not add any text before or after it. The benchmark runner will validate the proposal and finalize the finite task.
"""


class AgentTeamsRuntime:
    """Deterministic adapter for AgentTeams' documented finite-task protocol."""

    def __init__(self, worker: str):
        self.worker = worker

    @staticmethod
    def docker(container: str, args: list[str], timeout: int = 60, check: bool = True) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ["docker", "exec", container, *args], text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False,
        )
        if check and completed.returncode != 0:
            raise RuntimeError(f"AgentTeams command failed in {container}: {args[0]} (exit {completed.returncode})")
        return completed

    def worker_record(self) -> dict[str, Any]:
        result = self.docker("hiclaw-controller", ["hiclaw", "get", "workers", "-o", "json"])
        workers = json.loads(result.stdout).get("workers", [])
        matches = [item for item in workers if item.get("name") == self.worker]
        if len(matches) != 1:
            raise RuntimeError(f"Expected one ready AgentTeams Worker named {self.worker}; found {len(matches)}")
        record = matches[0]
        if record.get("phase") not in {"Ready", "Running"} or record.get("containerState") != "running":
            raise RuntimeError(f"AgentTeams Worker {self.worker} is not ready")
        return record

    def create_task(self, task_id: str, spec: str, title: str) -> tuple[str, str]:
        record = self.worker_record()
        room_id = str(record["roomID"])
        matrix_user_id = str(record["matrixUserID"])
        created_at = datetime.now(timezone.utc).isoformat()
        meta = build_task_meta(task_id, title, self.worker, room_id, created_at)
        writer = (
            "import base64,sys;from pathlib import Path;"
            "p=Path('/root/hiclaw-fs/shared/tasks')/sys.argv[1];p.mkdir(parents=True,exist_ok=True);"
            "(p/'meta.json').write_bytes(base64.b64decode(sys.argv[2]));"
            "(p/'spec.md').write_bytes(base64.b64decode(sys.argv[3]))"
        )
        self.docker("hiclaw-manager", ["python", "-c", writer, task_id,
            base64.b64encode(canonical(meta)).decode("ascii"),
            base64.b64encode(spec.encode("utf-8")).decode("ascii")])
        remote = f"hiclaw/hiclaw-storage/shared/tasks/{task_id}"
        local = f"/root/hiclaw-fs/shared/tasks/{task_id}"
        self.docker("hiclaw-manager", ["mc", "cp", f"{local}/meta.json", f"{remote}/meta.json"])
        self.docker("hiclaw-manager", ["mc", "cp", f"{local}/spec.md", f"{remote}/spec.md"])
        self.docker("hiclaw-manager", ["bash", "/opt/hiclaw/agent/skills/task-management/scripts/manage-state.sh",
            "--action", "add-finite", "--task-id", task_id, "--title", title,
            "--assigned-to", self.worker, "--room-id", room_id])
        notice = (
            f"{matrix_user_id} New finite task [{task_id}]: {title}. "
            f"Use taskflow ack_task for {task_id}; it will pull the spec. Follow it exactly, "
            "return the exact DAY4_RESULT JSON requested by the spec."
        )
        self.docker("hiclaw-manager", ["copaw", "channels", "send", "--agent-id", "default",
            "--channel", "matrix", "--target-session", room_id, "--target-user", matrix_user_id,
            "--text", notice], timeout=60)
        return room_id, notice

    def nudge(self, task_id: str, room_id: str) -> None:
        record = self.worker_record()
        matrix_user_id = str(record["matrixUserID"])
        notice = (
            f"{matrix_user_id} Continue finite task [{task_id}] from the last completed step. "
            "Do not narrate and do not call a tool: reply now with exactly the compact DAY4_RESULT JSON "
            "requested by the spec, with no surrounding text. "
            "Do not restart or repeat a successful step."
        )
        self.docker("hiclaw-manager", ["copaw", "channels", "send", "--agent-id", "default",
            "--channel", "matrix", "--target-session", room_id, "--target-user", matrix_user_id,
            "--text", notice], timeout=60)

    def correct_result(self, task_id: str, room_id: str) -> None:
        record = self.worker_record()
        matrix_user_id = str(record["matrixUserID"])
        notice = (
            f"{matrix_user_id} Your result for [{task_id}] did not match the required contract. "
            "Reply again with the literal text DAY4_RESULT followed by one JSON object, not a JSON property named "
            "DAY4_RESULT. Include exactly: task_id, fixture_id, condition, diagnosis, diagnostic_commands, "
            "proposed_package_json, verification_command. Preserve every original package.json metadata field and "
            "change only the smallest necessary dependency field. Do not invent fields or add surrounding text."
        )
        self.docker("hiclaw-manager", ["copaw", "channels", "send", "--agent-id", "default",
            "--channel", "matrix", "--target-session", room_id, "--target-user", matrix_user_id,
            "--text", notice], timeout=60)

    def read_result(self, task_id: str) -> dict[str, Any] | None:
        remote = f"hiclaw/hiclaw-storage/shared/tasks/{task_id}/"
        local = f"/root/hiclaw-fs/shared/tasks/{task_id}/"
        self.docker("hiclaw-manager", ["mc", "mirror", remote, local, "--overwrite"], check=False)
        workspace_reader = (
            "import base64,sys;from pathlib import Path;"
            "p=Path('/root/hiclaw-fs/shared/tasks')/sys.argv[1]/'workspace'/'result.json';"
            "print(base64.b64encode(p.read_bytes()).decode()) if p.exists() else sys.exit(3)"
        )
        completed = self.docker("hiclaw-manager", ["python", "-c", workspace_reader, task_id], check=False)
        if completed.returncode == 0:
            return parse_json_document(base64.b64decode(completed.stdout.strip()).decode("utf-8"))
        result_reader = (
            "import base64,sys;from pathlib import Path;"
            "p=Path('/root/hiclaw-fs/shared/tasks')/sys.argv[1]/'result.md';"
            "print(base64.b64encode(p.read_bytes()).decode()) if p.exists() else sys.exit(3)"
        )
        completed = self.docker("hiclaw-manager", ["python", "-c", result_reader, task_id], check=False)
        if completed.returncode == 3:
            return None
        if completed.returncode != 0:
            raise RuntimeError("Unable to read AgentTeams Worker result")
        result = parse_result(base64.b64decode(completed.stdout.strip()).decode("utf-8"))
        if result is None:
            raise ValueError("AgentTeams result.md does not contain a valid DAY4_RESULT object")
        return result

    def finalize(self, task_id: str, status: str) -> None:
        updater = (
            "import json,sys;from datetime import datetime,timezone;from pathlib import Path;"
            "p=Path('/root/hiclaw-fs/shared/tasks')/sys.argv[1]/'meta.json';"
            "x=json.loads(p.read_text());x['status']=sys.argv[2];x['completed_at']=datetime.now(timezone.utc).isoformat();"
            "p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\\n')"
        )
        self.docker("hiclaw-manager", ["python", "-c", updater, task_id, status], timeout=10, check=False)
        self.docker("hiclaw-manager", ["mc", "cp", f"/root/hiclaw-fs/shared/tasks/{task_id}/meta.json",
            f"hiclaw/hiclaw-storage/shared/tasks/{task_id}/meta.json"], timeout=10, check=False)
        self.docker("hiclaw-manager", ["bash", "/opt/hiclaw/agent/skills/task-management/scripts/manage-state.sh",
            "--action", "complete", "--task-id", task_id], timeout=10, check=False)


def parse_json_document(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            stripped = "\n".join(lines[1:-1]).strip()
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise ValueError("AgentTeams Worker result must be one JSON object")
    return value


def parse_result(text: str) -> dict[str, Any] | None:
    index = text.find(RESULT_MARKER)
    if index < 0:
        return None
    if text[:index].strip():
        return None
    tail = text[index + len(RESULT_MARKER):].lstrip(" :\t\r\n")
    try:
        value, end = json.JSONDecoder().raw_decode(tail)
    except json.JSONDecodeError:
        return None
    if tail[end:].strip():
        return None
    return value if isinstance(value, dict) else None


def collect_trial(client: MatrixClient, since: str, sent_at_ms: int, task_id: str, timeout_seconds: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.sync(since, 10000)
        since = response["next_batch"]
        for room_id, room in response.get("rooms", {}).get("join", {}).items():
            for event in room.get("timeline", {}).get("events", []):
                if event.get("type") != "m.room.message" or event.get("origin_server_ts", 0) < sent_at_ms:
                    continue
                body = event.get("content", {}).get("body", "")
                if not isinstance(body, str):
                    continue
                item = {
                    "room_id_hash": digest(room_id),
                    "event_id_hash": digest(str(event.get("event_id", ""))),
                    "sender_role": "manager" if str(event.get("sender", "")).startswith("@manager:") else "worker",
                    "timestamp_ms": int(event.get("origin_server_ts", 0)),
                    "body": body,
                }
                events.append(item)
                if item["sender_role"] == "manager" and task_id in body:
                    parsed = parse_result(body)
                    if parsed:
                        return events, parsed
    raise TimeoutError(f"AgentTeams trial timed out after {timeout_seconds}s: {task_id}")


def collect_protocol_trial(
    client: MatrixClient, runtime: AgentTeamsRuntime, since: str, sent_at_ms: int,
    task_id: str, room_id: str, timeout_seconds: int, events: list[dict[str, Any]],
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_activity = time.monotonic()
    nudges = 0
    seen: set[str] = set()
    result: dict[str, Any] | None = None
    completion_observed = False
    corrections = 0
    print(f"[WAIT] {task_id}: waiting for Worker result (timeout={timeout_seconds}s)", flush=True)
    while time.monotonic() < deadline:
        response = client.sync(since, 5000)
        since = response["next_batch"]
        room = response.get("rooms", {}).get("join", {}).get(room_id, {})
        for event in room.get("timeline", {}).get("events", []):
            event_id = str(event.get("event_id", ""))
            if event_id in seen or event.get("type") != "m.room.message" or event.get("origin_server_ts", 0) < sent_at_ms:
                continue
            seen.add(event_id)
            body = event.get("content", {}).get("body", "")
            if not isinstance(body, str):
                continue
            sender = str(event.get("sender", ""))
            role = "manager" if sender.startswith("@manager:") else "worker" if sender.startswith(f"@{runtime.worker}:") else "admin"
            events.append({
                "room_id_hash": digest(room_id), "event_id_hash": digest(event_id), "sender_role": role,
                "timestamp_ms": int(event.get("origin_server_ts", 0)), "body": body,
            })
            print(f"[EVENT] {task_id}: {role} message observed", flush=True)
            last_activity = time.monotonic()
            if role == "worker" and task_id in body and re.search(r"(?i)(TASK_COMPLETED|task completed|completed)", body):
                completion_observed = True
            if role == "worker":
                direct_result = parse_result(body)
                if direct_result is not None:
                    print(f"[RESULT] {task_id}: structured Worker result observed", flush=True)
                    return direct_result
                if RESULT_MARKER in body and corrections < 2:
                    runtime.correct_result(task_id, room_id)
                    corrections += 1
                    last_activity = time.monotonic()
                    print(f"[CORRECT] {task_id}: result contract correction {corrections}/2", flush=True)
        if result is None:
            result = runtime.read_result(task_id)
        if result is not None and completion_observed:
            return result
        if time.monotonic() - last_activity >= 60 and nudges < 3:
            runtime.nudge(task_id, room_id)
            nudges += 1
            last_activity = time.monotonic()
            print(f"[NUDGE] {task_id}: completion reminder {nudges}/3", flush=True)
    missing = []
    if result is None:
        missing.append("DAY4_RESULT")
    if not completion_observed:
        missing.append("Worker completion event")
    raise TimeoutError(f"AgentTeams finite task timed out after {timeout_seconds}s; missing: {', '.join(missing)}")


def validate_proposal(original: dict[str, Any], proposed: Any) -> dict[str, Any]:
    if not isinstance(proposed, dict):
        raise ValueError("Worker proposed_package_json must be an object")
    if len(canonical(proposed)) > 20000:
        raise ValueError("Worker proposed_package_json is too large")
    changed = {key for key in set(original) | set(proposed) if original.get(key) != proposed.get(key)}
    if not changed or not changed <= ALLOWED_MUTATIONS:
        raise ValueError("Worker proposal changed no dependency field or changed protected metadata")
    if "scripts" in proposed and proposed.get("scripts") != original.get("scripts"):
        raise ValueError("Worker proposal added or changed lifecycle scripts")
    for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies", "overrides"):
        values = proposed.get(section, {})
        if values is not None and not isinstance(values, dict):
            raise ValueError(f"Worker proposal has invalid {section}")
        for value in (values or {}).values():
            if not isinstance(value, str) or UNSAFE_SPEC.search(value):
                raise ValueError(f"Worker proposal contains unsafe dependency spec in {section}")
    pnpm_config = proposed.get("pnpm")
    if pnpm_config is not None:
        if not isinstance(pnpm_config, dict):
            raise ValueError("Worker proposal has invalid pnpm configuration")
        stack: list[Any] = [pnpm_config]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, str) and UNSAFE_SPEC.search(item):
                raise ValueError("Worker proposal contains unsafe dependency spec in pnpm configuration")
            elif not isinstance(item, (str, int, float, bool, type(None))):
                raise ValueError("Worker proposal contains invalid pnpm configuration value")
    return proposed


def fixture_module():
    path = ROOT / "scripts" / "validate_day4_fixtures.py"
    spec = importlib.util.spec_from_file_location("day4_fixture_validator", path)
    if not spec or not spec.loader:
        raise RuntimeError("Unable to load Day 4 fixture validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_proposal(
    root: Path, task: dict[str, Any], proposed: dict[str, Any], npm: str | None, pnpm: str | None,
    reproduction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fixtures = fixture_module()
    reproduction = reproduction or fixtures.reproduce(root, task, npm, pnpm)
    with tempfile.TemporaryDirectory(prefix="trace2skill-day4-verify-") as directory:
        work = Path(directory) / "repo"
        shutil.copytree(root / "repo", work)
        original = load_json(work / "package.json")
        safe = validate_proposal(original, proposed)
        (work / "package.json").write_bytes(canonical(safe))
        command = task["verify"]
        program = fixtures.resolve_program(command["program"], npm, pnpm)
        started = time.monotonic_ns()
        completed = subprocess.run(
            [program, *command["args"]], cwd=work, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=fixtures.isolated_package_manager_env(Path(directory)), timeout=60, check=False,
        )
        duration_ms = (time.monotonic_ns() - started) // 1_000_000
        return {
            "original_failure_reproduced": reproduction["exit_code"] != 0 and reproduction["failure_pattern_matched"],
            "verification_passed": completed.returncode == 0,
            "verification_exit_code": completed.returncode,
            "verification_duration_ms": duration_ms,
            "proposed_package_json_sha256": digest(canonical(safe)),
            "verification_output_sha256": digest(completed.stdout),
        }


def trial_passed(validation: dict[str, Any]) -> bool:
    return bool(validation.get("original_failure_reproduced") and validation.get("verification_passed"))


def rejection_category(exc: Exception) -> str:
    message = str(exc).lower()
    if "protected metadata" in message or "changed no dependency field" in message:
        return "protected-metadata-or-no-dependency-change"
    if "unsafe dependency" in message:
        return "unsafe-dependency-spec"
    if "identity does not match" in message:
        return "result-identity-mismatch"
    if isinstance(exc, TimeoutError):
        return "agentteams-timeout"
    return "invalid-worker-result"


def rejected_validation(reproduction: dict[str, Any], result: dict[str, Any] | None, exc: Exception) -> dict[str, Any]:
    proposed = result.get("proposed_package_json") if result is not None else None
    return {
        "original_failure_reproduced": reproduction["exit_code"] != 0 and reproduction["failure_pattern_matched"],
        "verification_passed": False,
        "verification_exit_code": -1,
        "verification_duration_ms": 0,
        "proposed_package_json_sha256": digest(canonical(proposed)),
        "verification_output_sha256": digest(str(exc)),
        "proposal_rejection": rejection_category(exc),
    }


def bounded_trace(
    task: dict[str, Any], condition: str, worker: str, request_event_id: str, sent_at_ms: int,
    events: list[dict[str, Any]], result: dict[str, Any], validation: dict[str, Any], prompt: str,
    skill_path: Path = SKILL,
) -> dict[str, Any]:
    last_ms = max([sent_at_ms, *[event["timestamp_ms"] for event in events]])
    tool_calls = sum(body["body"].startswith("🔧 **") for body in events)
    tool_errors = sum(body["body"].startswith("✅ **") and re.search(r"(?i)(error|failed|exit code [1-9])", body["body"]) is not None for body in events)
    return {
        "schema_version": "0.2.0",
        "trace_id": f"agentteams:{task['fixture_id']}:{condition}:{digest(request_event_id)[:12]}",
        "fixture_id": task["fixture_id"],
        "split": task["split"],
        "condition": condition,
        "framework": {"name": "AgentTeams", "version": "v1.1.2", "manager_model": "qwen-plus", "worker_model": "qwen-plus", "worker_name": worker},
        "prompt_sha256": digest(prompt),
        "skill_sha256": digest(skill_path.read_bytes()) if condition == "skill-assisted" else None,
        "events": [
            {"sequence": index, "timestamp_ms": item["timestamp_ms"], "sender_role": item["sender_role"], "body_sha256": digest(item["body"]), "event_id_hash": item["event_id_hash"], "room_id_hash": item["room_id_hash"]}
            for index, item in enumerate(events, 1)
        ],
        "metrics": {"event_count": len(events), "tool_calls": tool_calls, "tool_errors": tool_errors, "duration_ms": max(0, last_ms - sent_at_ms), "input_tokens": None, "output_tokens": None, "token_cost_usd": None, "token_measurement": "unavailable"},
        "validation": validation,
        "result": {"worker_result_sha256": digest(canonical(result)), "status": "success" if trial_passed(validation) else "failed"},
        "security": {"held_out_answer_in_prompt": False, "raw_messages_persisted": False, "network_used_by_validator": False, "secret_scan_passed": True},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-id", required=True)
    parser.add_argument("--condition", choices=("baseline", "skill-assisted"), required=True)
    parser.add_argument("--worker", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path.home() / "hiclaw-manager.env")
    parser.add_argument("--npm")
    parser.add_argument("--pnpm")
    parser.add_argument("--skill-path", type=Path, default=SKILL)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    root, task = load_fixture(args.fixture_id)
    values = read_env(args.env_file)
    client = MatrixClient(MATRIX_BASE, values["HICLAW_ADMIN_USER"], values["HICLAW_ADMIN_PASSWORD"])
    initial = client.sync(None, 0)
    task_id = f"d4-{args.fixture_id}-{args.condition}-{uuid.uuid4().hex[:8]}"
    if not args.skill_path.is_file():
        raise ValueError(f"Candidate Skill file not found: {args.skill_path}")
    prompt = build_worker_spec(root, task, args.condition, args.worker, task_id, args.skill_path)
    runtime = AgentTeamsRuntime(args.worker)
    sent_at_ms = int(time.time() * 1000)
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    events: list[dict[str, Any]] = []
    request_event_id = f"agentteams-protocol:{task_id}"
    result: dict[str, Any] | None = None
    reproduction: dict[str, Any] | None = None
    status = "failed"
    try:
        fixture_validator = fixture_module()
        reproduction = fixture_validator.reproduce(root, task, args.npm, args.pnpm)
        if reproduction["exit_code"] == 0 or not reproduction["failure_pattern_matched"]:
            raise RuntimeError("Day 4 trial refused to start because the declared original failure was not reproduced")
        room, notice = runtime.create_task(task_id, prompt, f"Day 4 dependency repair: {task['fixture_id']} ({args.condition})")
        result = collect_protocol_trial(client, runtime, initial["next_batch"], sent_at_ms, task_id, room, args.timeout_seconds, events)
        if result.get("task_id") != task_id or result.get("fixture_id") != task["fixture_id"] or result.get("condition") != args.condition:
            raise ValueError("Worker result identity does not match the requested trial")
        validation = verify_proposal(root, task, result.get("proposed_package_json"), args.npm, args.pnpm, reproduction)
        trace = bounded_trace(task, args.condition, args.worker, request_event_id, sent_at_ms, events, result, validation, prompt, args.skill_path)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(json.dumps(trace, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")
        status = "completed" if trial_passed(validation) else "failed"
    except Exception as exc:
        if reproduction is not None:
            failure_validation = rejected_validation(reproduction, result, exc)
            rejected_result = result if result is not None else {"error": rejection_category(exc)}
            rejected_trace = bounded_trace(
                task, args.condition, args.worker, request_event_id, sent_at_ms, events, rejected_result,
                failure_validation, prompt, args.skill_path,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(json.dumps(rejected_trace, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")
        raw = {
            "created_at": datetime.now(timezone.utc).isoformat(), "task_id": task_id,
            "request_event_id_hash": digest(request_event_id), "events": events, "worker_result": result,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
        args.raw_output.write_bytes(json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")
        raise
    finally:
        runtime.finalize(task_id, status)
    raw = {"created_at": datetime.now(timezone.utc).isoformat(), "task_id": task_id, "request_event_id_hash": digest(request_event_id), "events": events, "worker_result": result}
    args.raw_output.write_bytes(json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")
    passed = trial_passed(validation)
    print(f"[{'PASS' if passed else 'FAIL'}] {task['fixture_id']} {args.condition} worker={args.worker}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
