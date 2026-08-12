from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product" / "backend" / "src"))

from trace2skill.agentteams import AgentTeamsClient  # noqa: E402
from trace2skill.matrix_audit import MatrixAuditClient, load_agentteams_credentials  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("--worker", default="trace-worker")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    username, password = load_agentteams_credentials(Path.home() / "hiclaw-manager.env")
    matrix = MatrixAuditClient("http://127.0.0.1:18080", username, password)
    client = AgentTeamsClient(provisioner=matrix)
    worker = client.get_worker(args.worker, "execution")
    nudge_event_id = client.nudge_submit(worker, args.task_id)
    result = client.wait_for_result(args.task_id, args.timeout)
    client.finalize_task(args.task_id, "completed")
    print(
        json.dumps(
            {
                "task_id": args.task_id,
                "nudge_event_id": nudge_event_id,
                "result": result,
                "status": "completed",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
