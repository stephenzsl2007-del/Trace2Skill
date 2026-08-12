from __future__ import annotations

import json
import re
from typing import Any

from .security import REDACTED, sanitize


EVIDENCED_COLLECTIONS = (
    "task_signatures",
    "package_manager_detection",
    "success_paths",
    "failed_attempts",
    "preconditions",
    "tools_permissions",
    "prohibited_actions",
    "validator_rules",
    "conclusions",
)
REF_PATTERN = re.compile(r"^trace:([^#]+)#([^#]+)$")
FORBIDDEN_SCOPE_TERMS = {"python", "pip", "maven", "gradle", "cargo", "production-deploy"}


class ExperienceValidationError(ValueError):
    pass


class ExperienceValidator:
    def validate(self, candidate: dict[str, Any], traces: list[dict[str, Any]]) -> dict[str, Any]:
        clean = sanitize(candidate)
        if clean != candidate or REDACTED in json.dumps(clean, ensure_ascii=False):
            raise ExperienceValidationError("Experience Model contains sensitive content")
        if clean.get("schema_version") != "0.3":
            raise ExperienceValidationError("unsupported Experience Model schema")
        if len(traces) != 3:
            raise ExperienceValidationError("Experience Model requires exactly three training traces")
        trace_index: dict[str, set[str]] = {}
        observed_managers: set[str] = set()
        for trace in traces:
            trace_id = str(trace.get("trace_id", ""))
            if not trace_id or trace_id in trace_index:
                raise ExperienceValidationError("training traces require unique trace_id values")
            events = trace.get("events") or trace.get("messages")
            if not isinstance(events, list) or not events:
                raise ExperienceValidationError(f"training trace has no events: {trace_id}")
            trace_index[trace_id] = {
                str(event.get("event_id") or event.get("source_event_id")) for event in events
            }
            for event in events:
                manager = event.get("package_manager") or event.get("payload", {}).get("package_manager")
                if manager in {"npm", "pnpm"}:
                    observed_managers.add(manager)
            for command in trace.get("commands") or []:
                arguments = command.get("command") or []
                if arguments and arguments[0] in {"npm", "pnpm"}:
                    observed_managers.add(arguments[0])
        if set(clean.get("trace_ids") or []) != set(trace_index):
            raise ExperienceValidationError("Experience Model trace_ids do not match its three inputs")
        scope = clean.get("scope") or {}
        if scope.get("domain") != "javascript-typescript-ci-dependencies":
            raise ExperienceValidationError("Experience Model expanded beyond the MVP domain")
        ecosystems = set(scope.get("ecosystems") or [])
        if not ecosystems or not ecosystems.issubset(observed_managers):
            raise ExperienceValidationError(
                f"Experience Model claimed unobserved ecosystems: {sorted(ecosystems - observed_managers)}"
            )
        serialized = json.dumps(clean, ensure_ascii=False).lower()
        if any(term in serialized for term in FORBIDDEN_SCOPE_TERMS):
            raise ExperienceValidationError("Experience Model contains out-of-scope technology")
        seen_ids: set[str] = set()
        for collection in EVIDENCED_COLLECTIONS:
            values = clean.get(collection)
            if not isinstance(values, list) or (collection != "failed_attempts" and not values):
                raise ExperienceValidationError(f"missing evidenced collection: {collection}")
            for item in values:
                item_id = str(item.get("id", ""))
                if not item_id or item_id in seen_ids:
                    raise ExperienceValidationError(f"duplicate or empty Experience item id: {item_id}")
                seen_ids.add(item_id)
                references = item.get("evidence_refs")
                if not isinstance(references, list) or not references:
                    raise ExperienceValidationError(f"Experience item has no evidence: {item_id}")
                for reference in references:
                    match = REF_PATTERN.fullmatch(str(reference))
                    if not match or match.group(1) not in trace_index or match.group(2) not in trace_index[match.group(1)]:
                        raise ExperienceValidationError(
                            f"Experience item references nonexistent evidence: {reference}"
                        )
                if collection == "conclusions":
                    confidence = item.get("confidence")
                    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
                        raise ExperienceValidationError(f"invalid conclusion confidence: {item_id}")
                    if "limitations" not in item or "conflicts" not in item:
                        raise ExperienceValidationError(f"conclusion lacks limitations/conflicts: {item_id}")
        return clean


def validate_failure_report(value: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "schema_version", "fixture_id", "failed_stage", "command", "exit_code",
        "error_category", "output_hash", "evidence_refs",
    }
    if set(value) != allowed:
        raise ValueError(f"failure report contains forbidden or missing fields: {sorted(set(value) ^ allowed)}")
    if value["schema_version"] != "0.3":
        raise ValueError("unsupported failure report schema")
    if value["failed_stage"] not in {"reproduce", "install", "build", "test", "safety"}:
        raise ValueError("invalid failure stage")
    if not re.fullmatch(r"[a-f0-9]{64}", str(value["output_hash"])):
        raise ValueError("failure output must be referenced by SHA-256 hash")
    serialized = json.dumps(value, ensure_ascii=False).lower()
    if any(term in serialized for term in ("reference_patch", "answer", "proposed_package_json", "diff")):
        raise ValueError("failure report leaks solution material")
    return sanitize(value)
