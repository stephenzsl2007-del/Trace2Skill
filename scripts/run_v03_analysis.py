from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product" / "backend" / "src"))

from trace2skill.agent_specs import load_agent_specs  # noqa: E402
from trace2skill.agentteams import AgentTeamsClient  # noqa: E402
from trace2skill.analysis import TraceAnalysisRunner  # noqa: E402
from trace2skill.matrix_audit import MatrixAuditClient, load_agentteams_credentials  # noqa: E402
from trace2skill.trace_validation import validate_execution_trace  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "product" / "evidence" / "v0.3" / "training" / "manifest.json",
    )
    parser.add_argument("--worker", default="trace-worker")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "product" / "evidence" / "v0.3" / "experience" / "analysis.json",
    )
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    traces = [
        validate_execution_trace(json.loads((ROOT / entry["path"]).read_text(encoding="utf-8")))
        for entry in manifest["entries"]
    ]
    username, password = load_agentteams_credentials(Path.home() / "hiclaw-manager.env")
    matrix = MatrixAuditClient("http://127.0.0.1:18080", username, password)
    agentteams = AgentTeamsClient(provisioner=matrix)
    role_spec = load_agent_specs(ROOT / "product" / "agent-specs")["trace-analyst"]
    worker = agentteams.get_worker(args.worker, "trace-analyst", role_spec.content_hash)
    analysis = TraceAnalysisRunner(agentteams, matrix, role_spec).run(
        traces, worker, timeout_seconds=args.timeout
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "status": analysis["status"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
