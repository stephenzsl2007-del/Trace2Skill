from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product" / "backend" / "src"))

from trace2skill.agent_specs import load_agent_specs  # noqa: E402
from trace2skill.agentteams import AgentTeamsClient  # noqa: E402
from trace2skill.matrix_audit import MatrixAuditClient, load_agentteams_credentials  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lifecycle-smoke", action="store_true")
    parser.add_argument("--task-smoke", action="store_true")
    parser.add_argument("--fixed-worker", default="")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "product" / "data" / "gates" / "agentteams.json"
    )
    args = parser.parse_args()
    if args.task_smoke:
        username, password = load_agentteams_credentials(Path.home() / "hiclaw-manager.env")
        provisioner = MatrixAuditClient("http://127.0.0.1:18080", username, password)
        client = AgentTeamsClient(provisioner=provisioner)
    else:
        client = AgentTeamsClient()
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "health": client.health(),
        "lifecycle": {"attempted": False, "passed": False},
    }
    worker = None
    try:
        if args.fixed_worker:
            spec = load_agent_specs(ROOT / "product" / "agent-specs")["execution"]
            worker = client.get_worker(args.fixed_worker, "execution", spec.content_hash)
            result["lifecycle"] = {
                "attempted": False,
                "passed": True,
                "fixed_worker": True,
                "worker_name": worker.name,
                "spec_hash": worker.spec_hash,
            }
        elif args.lifecycle_smoke or args.task_smoke:
            result["lifecycle"]["attempted"] = True
            spec = load_agent_specs(ROOT / "product" / "agent-specs")["execution"]
            worker = client.create_worker(spec, "run_lifecycle_gate")
            result["lifecycle"].update(
                {
                    "created": True,
                    "worker_name": worker.name,
                    "role": worker.role,
                    "runtime": worker.runtime,
                    "model": worker.model,
                    "spec_hash": worker.spec_hash,
                }
            )
        if args.task_smoke:
            if worker is None:
                raise RuntimeError("task smoke requires a ready Worker")
            task_id = "v03-smoke-" + datetime.now(UTC).strftime("%H%M%S")
            specification = """# Trace2Skill v0.3 finite task smoke

This is a read-only protocol check. Do not access files, network, credentials, or tools other than taskflow.
After acknowledging this finite task, submit exactly this JSON object as the task result:

{"schema_version":"0.3","ok":true,"role":"execution","claim":"protocol-only"}

Do not add fields and do not claim that a CI task or Validator ran.
"""
            client.create_finite_task(worker, task_id, "v0.3 protocol smoke", specification)
            task_result = client.wait_for_result(task_id, timeout_seconds=180)
            expected = {
                "schema_version": "0.3",
                "ok": True,
                "role": "execution",
                "claim": "protocol-only",
            }
            if task_result != expected:
                raise RuntimeError(f"unexpected finite task result: {task_result}")
            client.finalize_task(task_id, "completed")
            result["task"] = {
                "attempted": True,
                "passed": True,
                "task_id": task_id,
                "result": task_result,
            }
    finally:
        if worker and not args.fixed_worker:
            client.delete_worker(worker)
            result["lifecycle"]["deleted"] = True
            result["lifecycle"]["passed"] = True
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["health"]["passed"] and (not (args.lifecycle_smoke or args.task_smoke) or result["lifecycle"]["passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
