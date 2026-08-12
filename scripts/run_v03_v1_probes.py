from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product" / "backend" / "src"))

from trace2skill.agent_specs import load_agent_specs  # noqa: E402
from trace2skill.agentteams import AgentTeamsClient  # noqa: E402
from trace2skill.evaluation import failure_report_from_trace  # noqa: E402
from trace2skill.execution import ExecutionRunner  # noqa: E402
from trace2skill.fixtures import FixtureCatalog, FixtureRunner  # noqa: E402
from trace2skill.matrix_audit import MatrixAuditClient, load_agentteams_credentials  # noqa: E402
from trace2skill.skill_packages import V1, V2_CANDIDATE  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", default="trace-worker")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--skill-version", choices=(V1, V2_CANDIDATE), default=V1)
    parser.add_argument(
        "--skill-root",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
    )
    args = parser.parse_args()
    if args.skill_root is None:
        args.skill_root = ROOT / "product" / "evidence" / "v0.3" / "skills" / "diagnose-ci-dependency-failure" / args.skill_version / "package"
    if args.output_root is None:
        label = "v1-probes" if args.skill_version == V1 else "v2-probes"
        args.output_root = ROOT / "product" / "evidence" / "v0.3" / label
    if args.output_root.exists():
        raise FileExistsError(f"immutable probe output already exists: {args.output_root}")
    fixtures = FixtureCatalog(ROOT / "product" / "fixtures").load()
    heldout = [
        fixtures[key]
        for key in sorted(fixtures)
        if fixtures[key].manifest["split"] == "held-out"
    ]
    skill_md = (args.skill_root / "SKILL.md").read_text(encoding="utf-8")
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
    args.output_root.mkdir(parents=True)
    records = []
    for fixture in heldout:
        trace = runner.run(
            fixture,
            worker,
            timeout_seconds=args.timeout,
            skill_md=skill_md,
            skill_version=args.skill_version,
        )
        trace_path = args.output_root / f"{fixture.fixture_id}.trace.json"
        trace_path.write_text(
            json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        report = None
        if trace["status"] != "succeeded":
            report = failure_report_from_trace(trace)
            (args.output_root / f"{fixture.fixture_id}.failure.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        records.append(
            {
                "fixture_id": fixture.fixture_id,
                "passed": trace["status"] == "succeeded",
                "trace": trace_path.name,
                "failure_report": report,
            }
        )
    actual = {item["fixture_id"]: item["passed"] for item in records}
    expected = (
        {
            "heldout-npm-peer-conflict": True,
            "heldout-pnpm-frozen-lockfile": False,
            "heldout-pnpm-missing-build-dependency": False,
        }
        if args.skill_version == V1
        else {fixture.fixture_id: True for fixture in heldout}
    )
    gate = actual == expected
    manifest = {
        "schema_version": "0.3",
        "skill_version": args.skill_version,
        "records": records,
        "gate_passed": gate,
    }
    (args.output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(args.output_root), "gate_passed": gate, "results": actual}, ensure_ascii=False))
    return 0 if gate else 1


if __name__ == "__main__":
    raise SystemExit(main())
