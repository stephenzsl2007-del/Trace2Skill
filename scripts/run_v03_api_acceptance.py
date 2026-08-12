from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product" / "backend" / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from trace2skill.api import create_app  # noqa: E402
from trace2skill.dispatcher import LocalMvpDispatcher  # noqa: E402
from trace2skill.objects import ObjectStore  # noqa: E402
from trace2skill.repository import Repository  # noqa: E402
from trace2skill.service import ProductSettings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--task-timeout", type=int, default=240)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    data_root = ROOT / "product" / "data" / "api-acceptance"
    settings = ProductSettings(data_root)
    repository = Repository(settings.database_path)
    objects = ObjectStore(settings.object_path)
    dispatcher = LocalMvpDispatcher(repository, objects, ROOT)
    app = create_app(settings, dispatcher=dispatcher)
    output = args.output or ROOT / "product" / "evidence" / "v0.3" / "api-acceptance" / f"{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    result: dict[str, object] = {
        "schema_version": "0.3",
        "started_at": datetime.now(UTC).isoformat(),
        "status": "failed",
    }
    try:
        with TestClient(app) as client:
            key = f"api-acceptance-{stamp}"
            request = {
                "kind": "full-loop",
                "config": {
                    "task_timeout": args.task_timeout,
                    "execution_worker": "trace-worker",
                    "skill_worker": "skill-worker",
                    "consumer_worker": "consumer-worker",
                },
            }
            first = client.post("/runs", json=request, headers={"Idempotency-Key": key})
            first.raise_for_status()
            created = first.json()
            second = client.post("/runs", json=request, headers={"Idempotency-Key": key})
            second.raise_for_status()
            duplicate = second.json()
            if duplicate["id"] != created["id"] or duplicate["created"]:
                raise RuntimeError("POST /runs idempotency gate failed")
            run_id = str(created["id"])
            deadline = time.monotonic() + args.timeout
            current = created
            while time.monotonic() < deadline:
                response = client.get(f"/runs/{run_id}")
                response.raise_for_status()
                current = response.json()
                if current["status"] in {"succeeded", "failed", "cancelled"}:
                    break
                time.sleep(1)
            else:
                client.post(f"/runs/{run_id}/cancel")
                raise TimeoutError(f"API full-loop did not finish in {args.timeout}s")
            events = app.state.repository.list_events(run_id)
            with app.state.repository.connection() as connection:
                trace_count = connection.execute(
                    "SELECT COUNT(*) FROM traces WHERE run_id=?", (run_id,)
                ).fetchone()[0]
            skills = [
                item for item in client.get("/skills").json()
                if item.get("metadata", {}).get("run_id") == run_id
            ]
            if current["status"] != "succeeded":
                raise RuntimeError(f"API full-loop failed: {current.get('failure_reason')}")
            if trace_count < 10 or not any(item["status"] == "qualified" for item in skills):
                raise RuntimeError("API did not ingest the complete Trace/Skill evidence set")
            result.update(
                {
                    "status": "succeeded",
                    "run_id": run_id,
                    "idempotency_verified": True,
                    "final_run": current,
                    "event_count": len(events),
                    "event_sequence_contiguous": [item["sequence"] for item in events]
                    == list(range(1, len(events) + 1)),
                    "trace_count": trace_count,
                    "skills": [
                        {
                            "name": item["name"],
                            "version": item["version"],
                            "status": item["status"],
                            "manifest_hash": item["manifest_hash"],
                        }
                        for item in skills
                    ],
                    "run_state_path": str(ROOT / "product" / "runs" / run_id / "run.json"),
                }
            )
    except Exception as exc:
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        result["ended_at"] = datetime.now(UTC).isoformat()
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "status": result["status"]}, ensure_ascii=False))
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
