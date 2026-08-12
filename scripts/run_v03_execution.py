from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product" / "backend" / "src"))

from trace2skill.agent_specs import load_agent_specs  # noqa: E402
from trace2skill.agentteams import AgentTeamsClient  # noqa: E402
from trace2skill.execution import ExecutionRunner  # noqa: E402
from trace2skill.fixtures import FixtureCatalog, FixtureRunner  # noqa: E402
from trace2skill.matrix_audit import MatrixAuditClient, load_agentteams_credentials  # noqa: E402
from trace2skill.trace_validation import validate_execution_trace  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture_id")
    parser.add_argument("--worker", default="trace-worker")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    fixtures = FixtureCatalog(ROOT / "product" / "fixtures").load()
    if args.fixture_id not in fixtures:
        raise ValueError(f"unknown fixture: {args.fixture_id}")
    username, password = load_agentteams_credentials(Path.home() / "hiclaw-manager.env")
    matrix = MatrixAuditClient("http://127.0.0.1:18080", username, password)
    agentteams = AgentTeamsClient(provisioner=matrix)
    role_spec = load_agent_specs(ROOT / "product" / "agent-specs")["execution"]
    worker = agentteams.get_worker(args.worker, "execution", role_spec.content_hash)
    runner = ExecutionRunner(
        agentteams,
        matrix,
        FixtureRunner(ROOT / "product" / "data" / "workspaces"),
        role_spec,
    )
    trace = runner.run(fixtures[args.fixture_id], worker, timeout_seconds=args.timeout)
    validate_execution_trace(trace)
    output = args.output or ROOT / "product" / "data" / "traces" / f"{trace['trace_id']}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"trace": str(output), "status": trace["status"], "error": trace["error"]}, ensure_ascii=False))
    return 0 if trace["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
