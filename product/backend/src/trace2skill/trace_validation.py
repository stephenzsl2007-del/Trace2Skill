from __future__ import annotations

import json
from typing import Any

from .security import REDACTED, sanitize


class TraceValidationError(ValueError):
    pass


def validate_execution_trace(trace: dict[str, Any]) -> dict[str, Any]:
    clean = sanitize(trace)
    if clean != trace or REDACTED in json.dumps(clean, ensure_ascii=False):
        raise TraceValidationError("Trace contains sensitive content")
    required = {
        "schema_version", "trace_id", "run_id", "task_id", "fixture_id", "fixture_hash",
        "agent_role", "worker", "assignment", "messages", "tool_calls", "commands",
        "command_results", "agent_result", "changed_files", "validator_result",
        "reproduction_receipt", "started_at", "ended_at", "status", "error",
    }
    optional = {"condition", "skill_version", "repair_steps"}
    if not required.issubset(clean) or not set(clean).issubset(required | optional):
        raise TraceValidationError(
            f"Trace fields differ from contract: {sorted((required - set(clean)) | (set(clean) - required - optional))}"
        )
    if clean["schema_version"] != "0.3" or clean["agent_role"] != "execution":
        raise TraceValidationError("Trace schema or role mismatch")
    if clean["run_id"] != clean["task_id"]:
        raise TraceValidationError("Trace run/task identity mismatch")
    if "condition" in clean:
        if clean["condition"] not in {"baseline", "skill-assisted"}:
            raise TraceValidationError("Trace condition is invalid")
        if (clean["condition"] == "baseline") != (clean.get("skill_version") is None):
            raise TraceValidationError("Trace condition and Skill version are inconsistent")
    if "repair_steps" in clean and not isinstance(clean["repair_steps"], list):
        raise TraceValidationError("Trace repair_steps must be a list")
    tool_calls = clean["tool_calls"]
    if not isinstance(tool_calls, list):
        raise TraceValidationError("Trace tool_calls must be a list")
    for call in tool_calls:
        if not isinstance(call, dict):
            raise TraceValidationError("Trace tool call must be an object")
        if call.get("tool") == "read_file":
            if call.get("status") != "succeeded" or call.get("exit_code") != 0:
                raise TraceValidationError("read_file tool call lacks a successful receipt")
            if len(str(call.get("output_hash", ""))) != 64:
                raise TraceValidationError("read_file tool call output hash is invalid")
    if len(clean["fixture_hash"]) != 64:
        raise TraceValidationError("Trace fixture hash is invalid")
    messages = clean["messages"]
    if not isinstance(messages, list) or not messages:
        raise TraceValidationError("Trace has no AgentTeams messages")
    event_ids = [event.get("source_event_id") for event in messages]
    if None in event_ids or len(event_ids) != len(set(event_ids)):
        raise TraceValidationError("Trace messages have missing or duplicate event IDs")
    timestamps = [int(event["timestamp_ms"]) for event in messages]
    if timestamps != sorted(timestamps):
        raise TraceValidationError("Trace messages are out of order")
    if clean["status"] == "succeeded":
        result = clean["agent_result"]
        validator = clean["validator_result"]
        if not isinstance(result, dict) or not isinstance(validator, dict):
            raise TraceValidationError("successful Trace lacks Agent result or Validator report")
        if result.get("task_id") != clean["task_id"] or result.get("fixture_id") != clean["fixture_id"]:
            raise TraceValidationError("Agent result identity does not match Trace")
        if not validator.get("passed") or not validator.get("original_error_absent"):
            raise TraceValidationError("successful Trace was not accepted by the host Validator")
        stages = validator.get("stages") or {}
        if any((stages.get(stage) or {}).get("status") != "passed" for stage in ("install", "build", "test")):
            raise TraceValidationError("successful Trace lacks passing install/build/test stages")
        if clean["error"] is not None:
            raise TraceValidationError("successful Trace contains an error")
        if not clean["changed_files"]:
            raise TraceValidationError("successful repair Trace contains no changed files")
    elif clean["status"] == "failed":
        if not isinstance(clean["error"], dict):
            raise TraceValidationError("failed Trace lacks a structured error")
    else:
        raise TraceValidationError("Trace status must be succeeded or failed")
    return clean
