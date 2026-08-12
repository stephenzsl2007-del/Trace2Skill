from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


SCHEMA_VERSION = "0.3"


class RunStatus(StrEnum):
    QUEUED = "queued"
    DISPATCHING = "dispatching"
    RUNNING = "running"
    VALIDATING = "validating"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = {
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
}


ALLOWED_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.QUEUED: {RunStatus.DISPATCHING, RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.DISPATCHING: {RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.RUNNING: {RunStatus.VALIDATING, RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.VALIDATING: {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    },
    RunStatus.SUCCEEDED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
}


class PipelinePhase(StrEnum):
    TRAINING_EXECUTION = "training_execution"
    TRACE_ANALYSIS = "trace_analysis"
    SKILL_V1_GENERATION = "skill_v1_generation"
    V1_EVALUATION = "v1_evaluation"
    REFINEMENT = "refinement"
    SKILL_V2_EVALUATION = "skill_v2_evaluation"
    SKILL_CONSUMER_VERIFICATION = "skill_consumer_verification"
    PUBLICATION_PENDING = "publication_pending"
    PUBLISHED = "published"


class RunKind(StrEnum):
    EXECUTION = "execution"
    SKILL_GENERATION = "skill-generation"
    VALIDATION = "validation"
    FULL_LOOP = "full-loop"


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class TraceEvent:
    event_id: str
    sequence: int
    run_id: str
    task_id: str
    agent_id: str
    phase: str
    event_type: str
    timestamp: str
    status: str
    tool: str | None = None
    input_ref: str | None = None
    output_ref: str | None = None
    token_usage: dict[str, int] | None = None
    evidence_refs: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assert_transition(current: str, target: str) -> None:
    try:
        current_status = RunStatus(current)
        target_status = RunStatus(target)
    except ValueError as exc:
        raise ValueError(f"unknown run status: {exc}") from exc
    if target_status not in ALLOWED_TRANSITIONS[current_status]:
        raise ValueError(f"illegal run transition: {current_status} -> {target_status}")
