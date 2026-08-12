from __future__ import annotations

import hashlib
import json
from typing import Any

from .experience import validate_failure_report


def failure_report_from_trace(trace: dict[str, Any]) -> dict[str, Any]:
    validation = trace.get("validator_result") or {}
    stages = validation.get("stages") or {}
    failed_stage = "safety"
    command: list[str] = []
    exit_code = 1
    output_hash = hashlib.sha256(b"validator-safety-failure").hexdigest()
    for stage in ("install", "build", "test"):
        detail = stages.get(stage) or {}
        if detail.get("status") == "failed":
            failed_stage = stage
            command = list(detail.get("command") or [])
            exit_code = int(detail.get("exit_code", 1))
            output_hash = str(detail.get("output_hash"))
            break
    else:
        error = trace.get("error") or {}
        if not validation and error:
            failed_stage = "reproduce"
            output_hash = hashlib.sha256(
                json.dumps(error, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
    result_events = [
        event for event in trace.get("messages") or []
        if str(event.get("body", "")).startswith("TRACE2SKILL_RESULT")
    ]
    evidence_refs = [
        f"matrix:{event['source_event_id']}" for event in result_events if event.get("source_event_id")
    ] or [f"sha256:{output_hash}"]
    manager = (trace.get("reproduction_receipt") or {}).get("command", [""])[0]
    category = (
        "skill-package-manager-scope-gap"
        if manager == "pnpm" and trace.get("skill_version") == "1.0.0-candidate.1"
        else f"validator-{failed_stage}-failure"
    )
    return validate_failure_report(
        {
            "schema_version": "0.3",
            "fixture_id": trace["fixture_id"],
            "failed_stage": failed_stage,
            "command": command,
            "exit_code": exit_code,
            "error_category": category,
            "output_hash": output_hash,
            "evidence_refs": evidence_refs,
        }
    )
