from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product" / "backend" / "src"))

from trace2skill.agent_specs import load_agent_specs  # noqa: E402
from trace2skill.agentteams import AgentTeamsClient  # noqa: E402
from trace2skill.execution import ExecutionRunner  # noqa: E402
from trace2skill.fixtures import FixtureCatalog, FixtureRunner  # noqa: E402
from trace2skill.matrix_audit import MatrixAuditClient, load_agentteams_credentials  # noqa: E402
from trace2skill.skill_consumption import install_verified_skill  # noqa: E402
from trace2skill.skill_packages import V2_CANDIDATE  # noqa: E402
from trace2skill.trace_validation import validate_execution_trace  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-root", type=Path, required=True)
    parser.add_argument("--skill-version", default=V2_CANDIDATE)
    parser.add_argument("--fixture", default="heldout-pnpm-missing-build-dependency")
    parser.add_argument("--source-worker", default="trace-worker")
    parser.add_argument("--consumer-worker")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    if args.output_root.exists():
        raise FileExistsError(f"immutable consumer evidence already exists: {args.output_root}")
    args.output_root.mkdir(parents=True)

    receipt_path = args.output_root / "receipt.json"

    def save_receipt(value: dict[str, object]) -> None:
        temporary = receipt_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(receipt_path)

    specs = load_agent_specs(ROOT / "product" / "agent-specs")
    role_spec = specs["execution"]
    username, password = load_agentteams_credentials(Path.home() / "hiclaw-manager.env")
    matrix = MatrixAuditClient("http://127.0.0.1:18080", username, password)
    agentteams = AgentTeamsClient(provisioner=matrix)
    controller = AgentTeamsClient()
    worker = None
    created_fresh = args.consumer_worker is None
    installed_root = ROOT / "product" / "data" / "worker-skills" / f"load-{uuid.uuid4().hex}"
    receipt: dict[str, object] = {
        "schema_version": "0.3",
        "started_at": datetime.now(UTC).isoformat(),
        "source_worker": args.source_worker,
        "created_fresh": created_fresh,
        "fixture_id": args.fixture,
        "skill_version": args.skill_version,
        "status": "failed",
        "phase": "queued",
    }
    save_receipt(receipt)
    try:
        receipt["phase"] = "installing_skill"
        save_receipt(receipt)
        install = install_verified_skill(args.skill_root, installed_root, args.skill_version)
        receipt["install"] = install
        receipt["phase"] = "creating_worker"
        save_receipt(receipt)
        worker = (
            controller.create_worker(role_spec, f"consumer-{uuid.uuid4().hex[:8]}")
            if created_fresh
            else agentteams.get_worker(args.consumer_worker, "execution-consumer", role_spec.content_hash)
        )
        if worker.name == args.source_worker:
            raise ValueError("Skill consumer must be a different Worker from the training Worker")
        if not matrix.verify_human_visible_worker_room(worker.room_id, worker.name):
            raise RuntimeError("fresh Skill consumer room is not visible to the human administrator")
        receipt["worker"] = {
            "name": worker.name,
            "role": worker.role,
            "model": worker.model,
            "runtime": worker.runtime,
            "spec_hash": worker.spec_hash,
        }
        receipt["phase"] = "running_skill_assisted_task"
        save_receipt(receipt)
        fixtures = FixtureCatalog(ROOT / "product" / "fixtures").load()
        if args.fixture not in fixtures or fixtures[args.fixture].manifest["split"] != "held-out":
            raise ValueError("consumer verification requires a known held-out fixture")
        runner = ExecutionRunner(
            agentteams,
            matrix,
            FixtureRunner(ROOT / "product" / "data" / "workspaces"),
            role_spec,
        )
        trace = runner.run(
            fixtures[args.fixture],
            worker,
            timeout_seconds=args.timeout,
            skill_md=(installed_root / "SKILL.md").read_text(encoding="utf-8"),
            skill_version=args.skill_version,
        )
        trace_path = args.output_root / "consumer.trace.json"
        trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        validate_execution_trace(trace)
        if trace["status"] != "succeeded" or not trace.get("validator_result"):
            raise RuntimeError(f"fresh Skill consumer task failed: {trace.get('error')}")
        receipt.update(
            {
                "trace_id": trace["trace_id"],
                "trace_path": trace_path.name,
                "validator_passed": bool(trace["validator_result"]["passed"]),
                "status": "succeeded",
                "phase": "validated",
            }
        )
        save_receipt(receipt)
    except Exception as exc:
        receipt["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        cleanup_error = None
        if worker is not None and created_fresh:
            try:
                receipt["phase"] = "cleaning_worker"
                save_receipt(receipt)
                controller.delete_worker(worker)
                receipt["worker_deleted"] = True
            except Exception as exc:
                cleanup_error = {"type": type(exc).__name__, "message": str(exc)}
                receipt["worker_deleted"] = False
        shutil.rmtree(installed_root, ignore_errors=True)
        receipt["installed_copy_removed"] = not installed_root.exists()
        if cleanup_error:
            receipt["cleanup_error"] = cleanup_error
        receipt["ended_at"] = datetime.now(UTC).isoformat()
        receipt["phase"] = "completed" if receipt["status"] == "succeeded" else "failed"
        save_receipt(receipt)
    print(json.dumps({"output": str(args.output_root), "status": receipt["status"]}, ensure_ascii=False))
    return 0 if receipt["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
