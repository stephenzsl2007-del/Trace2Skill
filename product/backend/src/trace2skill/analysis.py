from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from .agent_specs import AgentSpec
from .agentteams import AgentTeamsClient, WorkerHandle
from .experience import ExperienceValidationError, ExperienceValidator
from .matrix_audit import MatrixAuditClient
from .security import sanitize


EXPERIENCE_MARKER = "TRACE2SKILL_EXPERIENCE"


def _event_kind(body: str) -> str:
    if body.startswith("TRACE2SKILL_RESULT"):
        return "agent_result"
    if "ack_task" in body and body.startswith("🔧"):
        return "task_ack_call"
    if "ack_task" in body and body.startswith("✅"):
        return "task_ack_result"
    return "message"


def compact_trace(trace: dict[str, Any]) -> dict[str, Any]:
    messages = trace["messages"]
    final_events = [
        event for event in messages if str(event.get("body", "")).startswith("TRACE2SKILL_RESULT")
    ]
    if len(final_events) != 1:
        raise ValueError(f"training Trace requires one Agent result event: {trace['trace_id']}")
    evidence_events = []
    for event in messages:
        body = str(event.get("body", ""))
        kind = _event_kind(body)
        evidence_events.append(
            {
                "event_id": event["source_event_id"],
                "sender_role": event["sender_role"],
                "event_type": kind,
                "content": (
                    trace["agent_result"]
                    if kind == "agent_result"
                    else {"action": "ack_task", "success": True}
                    if kind == "task_ack_result"
                    else "task dispatch or acknowledgement"
                ),
            }
        )
    return {
        "trace_id": trace["trace_id"],
        "fixture_id": trace["fixture_id"],
        "fixture_hash": trace["fixture_hash"],
        "package_manager": trace["commands"][0]["command"][0],
        "reproduction": trace["reproduction_receipt"],
        "diagnosis": trace["agent_result"]["diagnosis"],
        "changed_files": trace["changed_files"],
        "validator": {
            "passed": trace["validator_result"]["passed"],
            "stages": trace["validator_result"]["stages"],
            "original_error_absent": trace["validator_result"]["original_error_absent"],
        },
        "evidence_events": evidence_events,
    }


def build_analysis_spec(traces: list[dict[str, Any]], role_spec: AgentSpec, task_id: str) -> str:
    views = [compact_trace(trace) for trace in traces]
    trace_ids = [trace["trace_id"] for trace in traces]
    return f"""# Trace2Skill v0.3 Trace analysis

TASK ID: {task_id}
ROLE SPEC HASH: {role_spec.content_hash}
INPUT TRACE IDS: {json.dumps(trace_ids)}

Read all three evidence views below and produce one Experience Model. The scope is only JavaScript/TypeScript CI dependency failures. The three training traces only observed npm, so do not claim that pnpm is supported. Preserve the limitation that pnpm is unobserved.

Every item in task_signatures, package_manager_detection, success_paths, failed_attempts, preconditions, tools_permissions, prohibited_actions, validator_rules, and conclusions must have:

- id: unique string
- statement: concrete reusable claim
- evidence_refs: one or more strings in the exact form `trace:<trace_id>#<event_id>` using an event listed below

Every conclusion must additionally have confidence from 0 to 1, limitations array, and conflicts array.

Return exactly `{EXPERIENCE_MARKER} ` followed by one compact JSON object with these top-level fields and no others:

- schema_version: `0.3`
- experience_id: `experience-{task_id}`
- trace_ids: the exact three input Trace IDs
- scope: domain=`javascript-typescript-ci-dependencies`, ecosystems, failure_classes, exclusions
- task_signatures
- package_manager_detection
- success_paths
- failed_attempts
- preconditions
- tools_permissions
- prohibited_actions
- validator_rules
- conclusions

Do not use Markdown fences. Do not include secrets, reference patches, held-out answers, pnpm commands, or claims about other ecosystems.

Before returning, check every object in all nine evidenced collections. In particular, conclusions must contain evidence_refs as well as confidence, limitations, and conflicts. An otherwise useful model with even one missing evidence_refs field will be rejected.

THREE TRAINING TRACE EVIDENCE VIEWS

{json.dumps(views, ensure_ascii=False, indent=2)}
"""


class TraceAnalysisRunner:
    def __init__(
        self,
        agentteams: AgentTeamsClient,
        matrix: MatrixAuditClient,
        role_spec: AgentSpec,
    ) -> None:
        self.agentteams = agentteams
        self.matrix = matrix
        self.role_spec = role_spec

    def run(
        self, traces: list[dict[str, Any]], worker: WorkerHandle, timeout_seconds: int = 240
    ) -> dict[str, Any]:
        if len(traces) != 3:
            raise ValueError("Trace Analyst requires exactly three training traces")
        task_id = f"v03-trace-analysis-{uuid.uuid4().hex[:8]}"
        started_at = datetime.now(UTC).isoformat()
        spec = build_analysis_spec(traces, self.role_spec, task_id)
        assignment = self.agentteams.create_finite_task(
            worker, task_id, "analyze three training traces", spec, inline_spec=True
        )
        assignments = [assignment]
        validation_repair: dict[str, Any] | None = None
        try:
            candidate, events = self.matrix.wait_for_worker_result(
                task_id, EXPERIENCE_MARKER, timeout_seconds=timeout_seconds
            )
            try:
                experience = ExperienceValidator().validate(candidate, traces)
                self.agentteams.finalize_task(task_id, "completed")
            except ExperienceValidationError as first_error:
                # A formatting/evidence omission is not silently repaired by the host. Give the
                # Analyst one bounded chance to return a fully evidenced replacement.
                original_task_id = task_id
                self.agentteams.finalize_task(original_task_id, "failed")
                repair_task_id = f"v03-trace-analysis-repair-{uuid.uuid4().hex[:8]}"
                task_id = repair_task_id
                repair_spec = build_analysis_spec(traces, self.role_spec, repair_task_id) + f"""

PRIOR OUTPUT REJECTED

Validation error: {first_error}

Return a complete replacement Experience Model, not a patch. Keep valid claims, add only real evidence references from the supplied events, and do not weaken or remove the evidence requirement.
"""
                repair_assignment = self.agentteams.create_finite_task(
                    worker,
                    repair_task_id,
                    "repair rejected Experience Model evidence",
                    repair_spec,
                    inline_spec=True,
                )
                assignments.append(repair_assignment)
                repaired_candidate, repair_events = self.matrix.wait_for_worker_result(
                    repair_task_id, EXPERIENCE_MARKER, timeout_seconds=timeout_seconds
                )
                experience = ExperienceValidator().validate(repaired_candidate, traces)
                self.agentteams.finalize_task(repair_task_id, "completed")
                events = events + repair_events
                validation_repair = {
                    "original_task_id": original_task_id,
                    "repair_task_id": repair_task_id,
                    "reason": str(first_error),
                    "attempts": 1,
                }
            return sanitize(
                {
                    "schema_version": "0.3",
                    "analysis_id": f"analysis_{uuid.uuid4().hex}",
                    "task_id": task_id,
                    "role": "trace-analyst",
                    "role_spec_hash": self.role_spec.content_hash,
                    "source_trace_ids": [trace["trace_id"] for trace in traces],
                    "source_trace_hashes": {
                        trace["trace_id"]: hashlib.sha256(
                            json.dumps(trace, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
                        ).hexdigest()
                        for trace in traces
                    },
                    "assignments": assignments,
                    "validation_repair": validation_repair,
                    "messages": events,
                    "experience": experience,
                    "started_at": started_at,
                    "ended_at": datetime.now(UTC).isoformat(),
                    "status": "succeeded",
                }
            )
        except Exception:
            try:
                self.agentteams.finalize_task(task_id, "failed")
            except Exception:
                pass
            raise
