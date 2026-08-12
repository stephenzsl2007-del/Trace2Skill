from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .models import PipelinePhase, RunKind, RunStatus, TERMINAL_STATUSES
from .objects import ObjectStore
from .repository import Repository


class Dispatcher(Protocol):
    async def execute(self, run_id: str, kind: str, request: dict[str, Any]) -> None: ...

    async def cancel(self, run_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ProductSettings:
    data_dir: Path
    cors_origins: tuple[str, ...] = ("http://127.0.0.1:3000", "http://localhost:3000")

    @property
    def database_path(self) -> Path:
        return self.data_dir / "trace2skill.sqlite3"

    @property
    def object_path(self) -> Path:
        return self.data_dir / "objects"


class RunService:
    def __init__(
        self, repository: Repository, objects: ObjectStore, dispatcher: Dispatcher | None = None
    ) -> None:
        self.repository = repository
        self.objects = objects
        self.dispatcher = dispatcher
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def create_run(
        self, kind: str, request: dict[str, Any], idempotency_key: str | None
    ) -> tuple[dict[str, Any], bool]:
        RunKind(kind)
        config_hash = hashlib.sha256(
            json.dumps(request.get("config", {}), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return self.repository.create_run(kind, request, idempotency_key, config_hash)

    def start(self, run: dict[str, Any], created: bool) -> None:
        if not created or not self.dispatcher:
            return
        self._tasks[run["id"]] = asyncio.create_task(self._execute(run))

    async def _execute(self, run: dict[str, Any]) -> None:
        run_id = run["id"]
        try:
            self.repository.transition_run(run_id, RunStatus.DISPATCHING)
            self.repository.transition_run(
                run_id, RunStatus.RUNNING, PipelinePhase.TRAINING_EXECUTION
            )
            await self.dispatcher.execute(run_id, run["kind"], run["request"])
            current = self.repository.get_run(run_id)
            if current and current["status"] not in {status.value for status in TERMINAL_STATUSES}:
                self.repository.transition_run(run_id, RunStatus.VALIDATING)
                self.repository.transition_run(run_id, RunStatus.SUCCEEDED)
        except asyncio.CancelledError:
            current = self.repository.get_run(run_id)
            if current and current["status"] not in {status.value for status in TERMINAL_STATUSES}:
                self.repository.cancel_run(run_id)
            raise
        except Exception as exc:
            current = self.repository.get_run(run_id)
            if current and current["status"] not in {status.value for status in TERMINAL_STATUSES}:
                self.repository.transition_run(run_id, RunStatus.FAILED, reason=str(exc))
        finally:
            self._tasks.pop(run_id, None)

    async def cancel(self, run_id: str) -> dict[str, Any]:
        if not self.repository.get_run(run_id):
            raise KeyError(run_id)
        if self.dispatcher:
            await self.dispatcher.cancel(run_id)
        task = self._tasks.get(run_id)
        if task and not task.done():
            task.cancel()
        return self.repository.cancel_run(run_id)

    def prepare_publish(self, name: str, version: str) -> dict[str, str]:
        skill = self.repository.get_skill(name, version)
        if not skill:
            raise KeyError(f"{name}/{version}")
        if skill["status"] != "qualified":
            raise ValueError("skill has not passed the publication gate")
        approval_id, challenge, expires_at = self.repository.create_approval(
            name, version, skill["manifest_hash"]
        )
        return {
            "approval_id": approval_id,
            "challenge": challenge,
            "manifest_hash": skill["manifest_hash"],
            "expires_at": expires_at,
        }

    def confirm_publish(
        self,
        name: str,
        version: str,
        approval_id: str,
        challenge: str,
        manifest_hash: str,
    ) -> dict[str, Any]:
        skill = self.repository.get_skill(name, version)
        if not skill or skill["manifest_hash"] != manifest_hash:
            raise ValueError("manifest hash changed or skill does not exist")
        if not self.repository.consume_approval(approval_id, challenge, manifest_hash):
            raise ValueError("approval is invalid, expired, or already consumed")
        return {"approved": True, "status": "publication_pending", "manifest_hash": manifest_hash}
