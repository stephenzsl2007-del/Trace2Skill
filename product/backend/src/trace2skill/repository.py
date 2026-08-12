from __future__ import annotations

import json
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from .models import RunStatus, TERMINAL_STATUSES, TraceEvent, assert_transition, utc_now
from .security import sanitize, sanitized_json


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  phase TEXT,
  status TEXT NOT NULL,
  request_json TEXT NOT NULL,
  idempotency_key TEXT UNIQUE,
  config_hash TEXT,
  failure_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workers (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  role TEXT NOT NULL,
  external_id TEXT,
  state TEXT NOT NULL,
  created_at TEXT NOT NULL,
  archived_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,
  sequence INTEGER NOT NULL,
  run_id TEXT NOT NULL REFERENCES runs(id),
  task_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  phase TEXT NOT NULL,
  event_type TEXT NOT NULL,
  timestamp TEXT NOT NULL,
  tool TEXT,
  input_ref TEXT,
  output_ref TEXT,
  status TEXT NOT NULL,
  token_usage_json TEXT,
  evidence_refs_json TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  UNIQUE(run_id, sequence)
);
CREATE INDEX IF NOT EXISTS events_run_sequence ON events(run_id, sequence);
CREATE TABLE IF NOT EXISTS traces (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  task_id TEXT NOT NULL,
  object_ref TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS skills (
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  status TEXT NOT NULL,
  manifest_hash TEXT NOT NULL,
  object_ref TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(name, version)
);
CREATE TABLE IF NOT EXISTS evaluations (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  skill_name TEXT,
  skill_version TEXT,
  fixture_id TEXT NOT NULL,
  condition TEXT NOT NULL,
  repeat_index INTEGER NOT NULL,
  passed INTEGER NOT NULL,
  report_ref TEXT NOT NULL,
  metrics_json TEXT NOT NULL,
  UNIQUE(run_id, fixture_id, condition, repeat_index)
);
CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT PRIMARY KEY,
  skill_name TEXT NOT NULL,
  skill_version TEXT NOT NULL,
  challenge_hash TEXT NOT NULL,
  manifest_hash TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  consumed_at TEXT,
  created_at TEXT NOT NULL
);
"""


class Repository:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
        finally:
            connection.close()

    def create_run(
        self,
        kind: str,
        request: dict[str, Any],
        idempotency_key: str | None = None,
        config_hash: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        clean_request = sanitize(request)
        now = utc_now()
        run_id = f"run_{uuid.uuid4().hex}"
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if idempotency_key:
                    existing = connection.execute(
                        "SELECT * FROM runs WHERE idempotency_key = ?", (idempotency_key,)
                    ).fetchone()
                    if existing:
                        connection.commit()
                        return self._run_dict(existing), False
                connection.execute(
                    """INSERT INTO runs
                    (id, kind, status, request_json, idempotency_key, config_hash, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        run_id,
                        kind,
                        RunStatus.QUEUED,
                        sanitized_json(clean_request),
                        idempotency_key,
                        config_hash,
                        now,
                        now,
                    ),
                )
                row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
                connection.commit()
                return self._run_dict(row), True
            except Exception:
                connection.rollback()
                raise

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return self._run_dict(row) if row else None

    def transition_run(
        self, run_id: str, target: str, phase: str | None = None, reason: str | None = None
    ) -> dict[str, Any]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
                if not row:
                    raise KeyError(run_id)
                assert_transition(row["status"], target)
                connection.execute(
                    "UPDATE runs SET status=?, phase=COALESCE(?,phase), failure_reason=?, updated_at=? WHERE id=?",
                    (target, phase, sanitize(reason), utc_now(), run_id),
                )
                updated = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
                connection.commit()
                return self._run_dict(updated)
            except Exception:
                connection.rollback()
                raise

    def update_run_phase(self, run_id: str, phase: str) -> dict[str, Any]:
        """Update progress without inventing a second lifecycle transition."""
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if not row:
                raise KeyError(run_id)
            if RunStatus(row["status"]) in TERMINAL_STATUSES:
                return self._run_dict(row)
            connection.execute(
                "UPDATE runs SET phase=?, updated_at=? WHERE id=?", (sanitize(phase), utc_now(), run_id)
            )
            updated = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return self._run_dict(updated)

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if not run:
            raise KeyError(run_id)
        if RunStatus(run["status"]) in TERMINAL_STATUSES:
            return run
        return self.transition_run(run_id, RunStatus.CANCELLED, reason="cancelled_by_human")

    def append_event(self, payload: dict[str, Any]) -> TraceEvent:
        clean = sanitize(payload)
        run_id = str(clean["run_id"])
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if not connection.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone():
                    raise KeyError(run_id)
                sequence = connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE run_id=?", (run_id,)
                ).fetchone()[0]
                event = TraceEvent(
                    event_id=f"evt_{uuid.uuid4().hex}",
                    sequence=sequence,
                    run_id=run_id,
                    task_id=str(clean["task_id"]),
                    agent_id=str(clean["agent_id"]),
                    phase=str(clean["phase"]),
                    event_type=str(clean["event_type"]),
                    timestamp=str(clean.get("timestamp") or utc_now()),
                    tool=clean.get("tool"),
                    input_ref=clean.get("input_ref"),
                    output_ref=clean.get("output_ref"),
                    status=str(clean["status"]),
                    token_usage=clean.get("token_usage"),
                    evidence_refs=list(clean.get("evidence_refs") or []),
                )
                connection.execute(
                    """INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        event.event_id,
                        event.sequence,
                        event.run_id,
                        event.task_id,
                        event.agent_id,
                        event.phase,
                        event.event_type,
                        event.timestamp,
                        event.tool,
                        event.input_ref,
                        event.output_ref,
                        event.status,
                        sanitized_json(event.token_usage) if event.token_usage is not None else None,
                        sanitized_json(event.evidence_refs),
                        event.schema_version,
                    ),
                )
                connection.commit()
                return event
            except Exception:
                connection.rollback()
                raise

    def list_events(self, run_id: str, after_event_id: str | None = None) -> list[dict[str, Any]]:
        after_sequence = 0
        with self.connection() as connection:
            if after_event_id:
                row = connection.execute(
                    "SELECT sequence FROM events WHERE run_id=? AND event_id=?",
                    (run_id, after_event_id),
                ).fetchone()
                if not row:
                    raise KeyError(after_event_id)
                after_sequence = row["sequence"]
            rows = connection.execute(
                "SELECT * FROM events WHERE run_id=? AND sequence>? ORDER BY sequence",
                (run_id, after_sequence),
            ).fetchall()
        return [self._event_dict(row) for row in rows]

    def recover_incomplete_runs(self) -> list[str]:
        recovered: list[str] = []
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT id FROM runs WHERE status NOT IN (?,?,?)",
                tuple(status.value for status in TERMINAL_STATUSES),
            ).fetchall()
        for row in rows:
            self.transition_run(
                row["id"], RunStatus.FAILED, reason="service_restarted_before_safe_resume"
            )
            self.append_event(
                {
                    "run_id": row["id"],
                    "task_id": "system",
                    "agent_id": "host",
                    "phase": "recovery",
                    "event_type": "run.recovered_as_failed",
                    "status": "failed",
                    "evidence_refs": [],
                }
            )
            recovered.append(row["id"])
        return recovered

    def put_trace(self, trace_id: str, run_id: str, task_id: str, object_ref: str) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO traces(id,run_id,task_id,object_ref,created_at) VALUES (?,?,?,?,?)",
                (trace_id, run_id, task_id, object_ref, utc_now()),
            )

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM traces WHERE id=?", (trace_id,)).fetchone()
        return dict(row) if row else None

    def put_skill(
        self,
        name: str,
        version: str,
        status: str,
        manifest_hash: str,
        object_ref: str,
        metadata: dict[str, Any],
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO skills VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(name,version) DO UPDATE SET
                status=excluded.status,
                manifest_hash=excluded.manifest_hash,
                object_ref=excluded.object_ref,
                metadata_json=excluded.metadata_json""",
                (
                    name,
                    version,
                    status,
                    manifest_hash,
                    object_ref,
                    sanitized_json(metadata),
                    utc_now(),
                ),
            )

    def list_skills(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM skills ORDER BY name, created_at DESC"
            ).fetchall()
        return [self._skill_dict(row) for row in rows]

    def get_skill(self, name: str, version: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM skills WHERE name=? AND version=?", (name, version)
            ).fetchone()
        return self._skill_dict(row) if row else None

    def add_evaluation(self, value: dict[str, Any]) -> None:
        clean = sanitize(value)
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO evaluations VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    clean.get("id", f"eval_{uuid.uuid4().hex}"),
                    clean["run_id"],
                    clean.get("skill_name"),
                    clean.get("skill_version"),
                    clean["fixture_id"],
                    clean["condition"],
                    int(clean["repeat_index"]),
                    int(bool(clean["passed"])),
                    clean["report_ref"],
                    sanitized_json(clean.get("metrics", {})),
                ),
            )

    def skill_evaluations(self, name: str, version: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """SELECT * FROM evaluations
                WHERE skill_name=? AND skill_version=?
                ORDER BY fixture_id, condition, repeat_index""",
                (name, version),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["passed"] = bool(item["passed"])
            item["metrics"] = json.loads(item.pop("metrics_json"))
            result.append(item)
        return result

    def create_approval(
        self, skill_name: str, skill_version: str, manifest_hash: str, ttl_minutes: int = 10
    ) -> tuple[str, str, str]:
        approval_id = f"approval_{uuid.uuid4().hex}"
        challenge = secrets.token_urlsafe(32)
        challenge_hash = __import__("hashlib").sha256(challenge.encode()).hexdigest()
        expires_at = (datetime.now(UTC) + timedelta(minutes=ttl_minutes)).isoformat()
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?)",
                (
                    approval_id,
                    skill_name,
                    skill_version,
                    challenge_hash,
                    manifest_hash,
                    expires_at,
                    None,
                    utc_now(),
                ),
            )
        return approval_id, challenge, expires_at

    def consume_approval(self, approval_id: str, challenge: str, manifest_hash: str) -> bool:
        challenge_hash = __import__("hashlib").sha256(challenge.encode()).hexdigest()
        now = datetime.now(UTC)
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM approvals WHERE approval_id=?", (approval_id,)
                ).fetchone()
                valid = bool(
                    row
                    and row["consumed_at"] is None
                    and datetime.fromisoformat(row["expires_at"]) > now
                    and secrets.compare_digest(row["challenge_hash"], challenge_hash)
                    and secrets.compare_digest(row["manifest_hash"], manifest_hash)
                )
                if valid:
                    changed = connection.execute(
                        "UPDATE approvals SET consumed_at=? WHERE approval_id=? AND consumed_at IS NULL",
                        (utc_now(), approval_id),
                    ).rowcount
                    valid = changed == 1
                connection.commit()
                return valid
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _skill_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    @staticmethod
    def _run_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        return result

    @staticmethod
    def _event_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["token_usage"] = (
            json.loads(result.pop("token_usage_json")) if result["token_usage_json"] else None
        )
        result["evidence_refs"] = json.loads(result.pop("evidence_refs_json"))
        return result
