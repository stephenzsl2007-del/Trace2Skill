from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from .objects import ObjectStore
from .repository import Repository


class LocalMvpDispatcher:
    """Run the proven CLI pipeline behind FastAPI and mirror its evidence into product storage."""

    def __init__(self, repository: Repository, objects: ObjectStore, root: Path | None = None) -> None:
        self.repository = repository
        self.objects = objects
        self.root = Path(root or Path(__file__).resolve().parents[4])
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    async def execute(self, run_id: str, kind: str, request: dict[str, Any]) -> None:
        if kind != "full-loop":
            raise ValueError("the MVP dispatcher currently supports only kind=full-loop")
        config = request.get("config") or {}
        timeout = int(config.get("task_timeout", 180))
        if timeout < 30 or timeout > 900:
            raise ValueError("task_timeout must be between 30 and 900 seconds")
        command = [
            sys.executable,
            str(self.root / "scripts" / "run_v03_mvp.py"),
            "--run-id",
            run_id,
            "--execution-worker",
            str(config.get("execution_worker", "trace-worker")),
            "--skill-worker",
            str(config.get("skill_worker", "skill-worker")),
            "--consumer-worker",
            str(config.get("consumer_worker", "consumer-worker")),
            "--task-timeout",
            str(timeout),
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=self.root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._processes[run_id] = process
        state_path = self.root / "product" / "runs" / run_id / "run.json"
        observed: dict[str, str] = {}
        try:
            while process.returncode is None:
                self._mirror_state(run_id, state_path, observed)
                await asyncio.sleep(0.25)
            output_bytes, _ = await process.communicate()
            self._mirror_state(run_id, state_path, observed)
            output = output_bytes.decode("utf-8", errors="replace")
            if process.returncode != 0:
                raise RuntimeError(f"MVP pipeline failed with exit {process.returncode}: {output[-1200:]}")
            self._ingest_artifacts(run_id, state_path.parent)
        finally:
            self._processes.pop(run_id, None)

    async def cancel(self, run_id: str) -> None:
        process = self._processes.get(run_id)
        if not process or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            process.kill()
            await process.wait()

    def _mirror_state(self, run_id: str, state_path: Path, observed: dict[str, str]) -> None:
        if not state_path.exists():
            return
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        phase = state.get("phase")
        if phase:
            self.repository.update_run_phase(run_id, str(phase))
        for name, detail in state.get("phases", {}).items():
            phase_status = str(detail.get("status", "unknown"))
            if observed.get(name) == phase_status:
                continue
            observed[name] = phase_status
            receipt_ref = self.objects.put_json({"phase": name, **detail})
            self.repository.append_event(
                {
                    "run_id": run_id,
                    "task_id": name,
                    "agent_id": "host-orchestrator",
                    "phase": name,
                    "event_type": f"phase.{phase_status}",
                    "status": phase_status,
                    "output_ref": receipt_ref,
                    "evidence_refs": [receipt_ref],
                }
            )

    def _ingest_artifacts(self, run_id: str, run_root: Path) -> None:
        seen_traces: set[str] = set()
        for path in sorted(run_root.rglob("*.trace.json")):
            trace = json.loads(path.read_text(encoding="utf-8"))
            trace_id = str(trace["trace_id"])
            if trace_id in seen_traces:
                continue
            seen_traces.add(trace_id)
            reference = self.objects.put_json(trace)
            self.repository.put_trace(trace_id, run_id, str(trace["task_id"]), reference)
        for metadata_path in sorted(run_root.glob("skills/*/*/package/trace2skill.json")):
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            version_root = metadata_path.parent.parent
            manifest = json.loads((version_root / "manifest.json").read_text(encoding="utf-8"))
            archive = next(version_root.glob("*.zip"))
            object_ref = self.objects.put_bytes(archive.read_bytes())
            status = "qualified" if str(metadata["version"]).startswith("2.") else "failed"
            self.repository.put_skill(
                str(metadata["name"]),
                str(metadata["version"]),
                status,
                str(manifest["manifest_hash"]),
                object_ref,
                {**metadata, "run_id": run_id, "package_path": str(metadata_path.parent)},
            )
