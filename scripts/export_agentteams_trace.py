#!/usr/bin/env python3
"""Export a sanitized AgentTeams Matrix task trace using only the stdlib."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any
import urllib.parse
import urllib.request


SCHEMA_VERSION = "0.1.0"
SECRET_PATTERNS = (
    re.compile(r"\bsk-(?:ws-)?[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(
        r'''(?ix)(["']?(?:api[_-]?key|access[_-]?token|password|secret|token)["']?\s*[:=]\s*["'])([^"']+)(["'])'''
    ),
    re.compile(r"(?i)(https?://)([^/@\s:]+):([^/@\s]+)@"),
)


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"').strip("'")
    return values


def matrix_request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        base_url + path,
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def redact_text(value: str) -> tuple[str, int]:
    redactions = 0

    def replace_secret(match: re.Match[str]) -> str:
        nonlocal redactions
        redactions += 1
        if match.lastindex == 3:
            return f"{match.group(1)}<redacted>{match.group(3)}"
        return "<redacted>"

    result = value
    for pattern in SECRET_PATTERNS:
        result = pattern.sub(replace_secret, result)
    return result, redactions


def redact_value(value: Any) -> tuple[Any, int]:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        output: list[Any] = []
        count = 0
        for item in value:
            cleaned, item_count = redact_value(item)
            output.append(cleaned)
            count += item_count
        return output, count
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        count = 0
        for key, item in value.items():
            if re.search(r"(?i)(api[_-]?key|access[_-]?token|password|secret|token)$", key):
                output[key] = "<redacted>"
                count += 1
            else:
                output[key], item_count = redact_value(item)
                count += item_count
        return output, count
    return value, 0


def actor_from_sender(sender: str, worker_name: str) -> tuple[str, str] | None:
    if sender.startswith("@admin:"):
        return "admin", "human"
    if sender.startswith("@manager:"):
        return "manager", "manager"
    if sender.startswith(f"@{worker_name}:"):
        return worker_name, "worker"
    return None


def timestamp_iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def fenced_json(body: str) -> Any | None:
    match = re.search(r"```\s*\n(.*?)\n```", body, flags=re.DOTALL)
    if not match:
        return None
    candidate = match.group(1).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return candidate


def tool_name(body: str) -> str | None:
    match = re.match(r"^[🔧✅]\s*\*\*([^*]+)\*\*", body)
    return match.group(1).strip() if match else None


def classify_event(body: str, sender: str, worker_name: str, task_id: str) -> str:
    lowered = body.lower()
    if sender.startswith("@admin:") and "multi-agent smoke task" in lowered:
        return "task_received"
    if sender.startswith("@manager:") and "task assigned" in lowered and task_id in body:
        return "task_dispatched"
    if sender.startswith(f"@{worker_name}:") and '"action": "ack_task"' in body and '"ok": true' in body:
        return "task_acknowledged"
    if sender.startswith(f"@{worker_name}:") and '"action": "submit_task"' in body and '"ok": true' in body:
        return "task_submitted"
    if sender.startswith(f"@{worker_name}:") and "TASK_COMPLETED:" in body and task_id in body:
        return "task_completed"
    if body.startswith("🔧 **"):
        return "tool_call"
    if body.startswith("✅ **"):
        if re.search(r"(?i)(\"ok\"\s*:\s*false|error:|failed|exit code [1-9])", body):
            return "tool_error"
        return "tool_result"
    return "agent_message"


def event_payload(event_type: str, body: str) -> dict[str, Any]:
    if event_type == "tool_call":
        return {"tool_name": tool_name(body), "arguments": fenced_json(body)}
    if event_type in {"tool_result", "tool_error", "task_acknowledged", "task_submitted"}:
        parsed = fenced_json(body)
        if parsed is None and ":\n" in body:
            tail = body.split(":\n", 1)[1].strip()
            try:
                parsed = json.loads(tail)
            except json.JSONDecodeError:
                parsed = tail
        return {"tool_name": tool_name(body), "result": parsed}
    return {"content": body}


def collect_recent_messages(config: dict[str, str], matrix_base: str) -> list[dict[str, Any]]:
    login = matrix_request(
        matrix_base,
        "/_matrix/client/v3/login",
        method="POST",
        body={
            "type": "m.login.password",
            "identifier": {
                "type": "m.id.user",
                "user": config["HICLAW_ADMIN_USER"],
            },
            "password": config["HICLAW_ADMIN_PASSWORD"],
        },
    )
    sync = matrix_request(
        matrix_base,
        "/_matrix/client/v3/sync?timeout=0",
        token=login["access_token"],
    )
    messages: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()

    def append_events(room_id: str, events: list[dict[str, Any]]) -> None:
        for event in events:
            if event.get("type") != "m.room.message":
                continue
            source_event_id = str(event.get("event_id", ""))
            if not source_event_id or source_event_id in seen_event_ids:
                continue
            seen_event_ids.add(source_event_id)
            messages.append(
                {
                    "room_id": room_id,
                    "source_event_id": source_event_id,
                    "timestamp_ms": int(event.get("origin_server_ts", 0)),
                    "sender": str(event.get("sender", "")),
                    "body": str(event.get("content", {}).get("body", "")),
                }
            )

    for room_id, room in sync.get("rooms", {}).get("join", {}).items():
        timeline = room.get("timeline", {})
        append_events(room_id, timeline.get("events", []))
        pagination_token = timeline.get("prev_batch")
        for _ in range(5):
            if not pagination_token:
                break
            query = urllib.parse.urlencode(
                {"from": pagination_token, "dir": "b", "limit": 100}
            )
            encoded_room = urllib.parse.quote(room_id, safe="")
            page = matrix_request(
                matrix_base,
                f"/_matrix/client/v3/rooms/{encoded_room}/messages?{query}",
                token=login["access_token"],
            )
            chunk = page.get("chunk", [])
            append_events(room_id, chunk)
            next_token = page.get("end")
            if not chunk or not next_token or next_token == pagination_token:
                break
            pagination_token = next_token
    return sorted(messages, key=lambda item: (item["timestamp_ms"], item["source_event_id"]))


def select_task_window(messages: list[dict[str, Any]], task_id: str, worker_name: str) -> list[dict[str, Any]]:
    actors = ("@admin:", "@manager:", f"@{worker_name}:")
    relevant = [item for item in messages if item["sender"].startswith(actors)]
    anchors = [item for item in relevant if task_id in item["body"]]
    if not anchors:
        raise ValueError(f"No recent Matrix events contain task id {task_id!r}.")
    rooms = {item["room_id"] for item in anchors}
    start = min(item["timestamp_ms"] for item in anchors) - 120_000
    end = max(item["timestamp_ms"] for item in anchors) + 120_000
    return [item for item in relevant if item["room_id"] in rooms and start <= item["timestamp_ms"] <= end]


def build_trace(messages: list[dict[str, Any]], task_id: str, worker_name: str) -> dict[str, Any]:
    selected = select_task_window(messages, task_id, worker_name)
    events: list[dict[str, Any]] = []
    pending_calls: dict[tuple[str, str], list[str]] = defaultdict(list)
    redaction_count = 0

    for source in selected:
        actor = actor_from_sender(source["sender"], worker_name)
        if not actor:
            continue
        actor_id, _ = actor
        kind = classify_event(source["body"], source["sender"], worker_name, task_id)
        event_id = f"evt-{len(events) + 1:04d}"
        call_id: str | None = None
        key = (actor_id, source["room_id"])
        if kind == "tool_call":
            call_id = f"call-{len(events) + 1:04d}"
            pending_calls[key].append(call_id)
        elif kind in {"tool_result", "tool_error", "task_acknowledged", "task_submitted"} and pending_calls[key]:
            call_id = pending_calls[key].pop(0)
        payload, count = redact_value(event_payload(kind, source["body"]))
        redaction_count += count
        events.append(
            {
                "sequence": len(events) + 1,
                "event_id": event_id,
                "timestamp": timestamp_iso(source["timestamp_ms"]),
                "type": kind,
                "actor_id": actor_id,
                "task_id": task_id,
                "call_id": call_id,
                "payload": payload,
                "provenance": {
                    "source": "matrix",
                    "room_id": source["room_id"],
                    "source_event_id": source["source_event_id"],
                },
            }
        )

    types = defaultdict(list)
    for event in events:
        types[event["type"]].append(event["event_id"])
    serialized = json.dumps(events, ensure_ascii=False)
    secret_scan_passed = not any(pattern.search(serialized) for pattern in SECRET_PATTERNS)
    checks = [
        {"name": "manager_dispatched_to_worker", "passed": bool(types["task_dispatched"]), "evidence_event_ids": types["task_dispatched"]},
        {"name": "worker_acknowledged", "passed": bool(types["task_acknowledged"]), "evidence_event_ids": types["task_acknowledged"]},
        {"name": "worker_submitted", "passed": bool(types["task_submitted"]), "evidence_event_ids": types["task_submitted"]},
        {"name": "worker_completed", "passed": bool(types["task_completed"]), "evidence_event_ids": types["task_completed"]},
        {
            "name": "submission_verified",
            "passed": any('"verified": true' in source["body"] for source in selected),
            "evidence_event_ids": types["task_submitted"],
        },
        {
            "name": "semantic_package_manager",
            "passed": any("npm" in source["body"].lower() for source in selected),
            "evidence_event_ids": types["task_completed"] or types["task_submitted"],
            "details": "The trace contains npm-specific diagnostic evidence.",
        },
        {"name": "secret_scan", "passed": secret_scan_passed, "evidence_event_ids": []},
    ]
    started_ms = min(item["timestamp_ms"] for item in selected)
    ended_ms = max(item["timestamp_ms"] for item in selected)
    trace = {
        "schema_version": SCHEMA_VERSION,
        "trace_id": f"agentteams:{task_id}:day2",
        "task": {
            "id": task_id,
            "title": "Diagnose npm ERESOLVE",
            "kind": "javascript_dependency_failure",
            "input": {"package_manager": "npm", "failure": "ERESOLVE"},
            "source": "agentteams",
        },
        "run": {
            "framework": "AgentTeams",
            "framework_version": "v1.1.2",
            "started_at": timestamp_iso(started_ms),
            "ended_at": timestamp_iso(ended_ms),
            "status": "success" if all(check["passed"] for check in checks[:5]) else "partial",
        },
        "actors": [
            {"id": "admin", "role": "human", "runtime": None, "model": None},
            {"id": "manager", "role": "manager", "runtime": "copaw", "model": "qwen-plus"},
            {"id": worker_name, "role": "worker", "runtime": "copaw", "model": "qwen-plus"},
        ],
        "events": events,
        "artifacts": [
            {"kind": "task_meta", "uri": f"minio://hiclaw-storage/shared/tasks/{task_id}/meta.json", "content_hash": None, "produced_by_event_id": None},
            {"kind": "task_spec", "uri": f"minio://hiclaw-storage/shared/tasks/{task_id}/spec.md", "content_hash": None, "produced_by_event_id": None},
            {"kind": "result", "uri": f"minio://hiclaw-storage/shared/tasks/{task_id}/result.md", "content_hash": None, "produced_by_event_id": (types["task_submitted"] or [None])[0]},
        ],
        "validation": {"passed": all(check["passed"] for check in checks), "checks": checks},
        "metrics": {
            "event_count": len(events),
            "message_count": sum(event["type"] in {"task_received", "task_dispatched", "agent_message", "task_completed"} for event in events),
            "tool_call_count": sum(event["type"] == "tool_call" for event in events),
            "tool_error_count": sum(event["type"] == "tool_error" for event in events),
            "duration_ms": max(0, ended_ms - started_ms),
        },
        "security": {
            "contains_chain_of_thought": False,
            "redaction_applied": redaction_count > 0,
            "secret_scan_passed": secret_scan_passed,
        },
    }
    return trace


def validate_trace(trace: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {"schema_version", "trace_id", "task", "run", "actors", "events", "artifacts", "validation", "metrics", "security"}
    missing = required - trace.keys()
    if missing:
        errors.append(f"missing top-level fields: {sorted(missing)}")
    sequences = [event.get("sequence") for event in trace.get("events", [])]
    if sequences != list(range(1, len(sequences) + 1)):
        errors.append("event sequences are not contiguous")
    timestamps = [event.get("timestamp") for event in trace.get("events", [])]
    if timestamps != sorted(timestamps):
        errors.append("event timestamps are not ordered")
    actor_ids = {actor["id"] for actor in trace.get("actors", [])}
    event_ids = [event.get("event_id") for event in trace.get("events", [])]
    if len(event_ids) != len(set(event_ids)):
        errors.append("event IDs are not unique")
    source_event_ids = [event.get("provenance", {}).get("source_event_id") for event in trace.get("events", [])]
    if len(source_event_ids) != len(set(source_event_ids)):
        errors.append("source event IDs are not unique")
    for event in trace.get("events", []):
        if event.get("actor_id") not in actor_ids:
            errors.append(f"event {event.get('event_id')} references an unknown actor")
    call_ids = [event.get("call_id") for event in trace.get("events", []) if event.get("type") == "tool_call"]
    result_ids = [
        event.get("call_id")
        for event in trace.get("events", [])
        if event.get("type") in {"tool_result", "tool_error", "task_acknowledged", "task_submitted"}
    ]
    if None in call_ids or len(call_ids) != len(set(call_ids)):
        errors.append("tool call IDs are missing or duplicated")
    if None in result_ids or sorted(call_ids) != sorted(result_ids):
        errors.append("tool calls and results are not paired one-to-one")
    metrics = trace.get("metrics", {})
    events = trace.get("events", [])
    expected_metrics = {
        "event_count": len(events),
        "message_count": sum(event.get("type") in {"task_received", "task_dispatched", "agent_message", "task_completed"} for event in events),
        "tool_call_count": sum(event.get("type") == "tool_call" for event in events),
        "tool_error_count": sum(event.get("type") == "tool_error" for event in events),
    }
    for name, expected in expected_metrics.items():
        if metrics.get(name) != expected:
            errors.append(f"metric {name} is {metrics.get(name)!r}, expected {expected}")
    known_event_ids = set(event_ids)
    for check in trace.get("validation", {}).get("checks", []):
        unknown = set(check.get("evidence_event_ids", [])) - known_event_ids
        if unknown:
            errors.append(f"validation check {check.get('name')} references unknown events")
    for artifact in trace.get("artifacts", []):
        producer = artifact.get("produced_by_event_id")
        if producer is not None and producer not in known_event_ids:
            errors.append(f"artifact {artifact.get('uri')} references an unknown producer event")
    if not trace.get("security", {}).get("secret_scan_passed"):
        errors.append("secret scan failed")
    if not trace.get("validation", {}).get("passed"):
        failed = [check["name"] for check in trace["validation"]["checks"] if not check["passed"]]
        errors.append(f"validation checks failed: {failed}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", default="trace-task-001")
    parser.add_argument("--worker", default="trace-worker")
    parser.add_argument("--matrix-base", default="http://127.0.0.1:18080")
    parser.add_argument("--env-file", type=Path, default=Path.home() / "hiclaw-manager.env")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    config = load_env(args.env_file)
    messages = collect_recent_messages(config, args.matrix_base)
    trace = build_trace(messages, args.task_id, args.worker)
    errors = validate_trace(trace)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if errors:
        for error in errors:
            print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    print(f"Trace exported: {args.output}")
    print(f"events={trace['metrics']['event_count']} tools={trace['metrics']['tool_call_count']} duration_ms={trace['metrics']['duration_ms']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
