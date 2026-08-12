from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product" / "backend" / "src"))

from trace2skill.agent_specs import load_agent_specs  # noqa: E402
from trace2skill.agentteams import AgentTeamsClient  # noqa: E402
from trace2skill.experience import ExperienceValidator  # noqa: E402
from trace2skill.matrix_audit import MatrixAuditClient, load_agentteams_credentials  # noqa: E402
from trace2skill.skill_generation import SkillGenerationRunner, persist_skill_candidate  # noqa: E402
from trace2skill.skill_packages import SKILL_NAME, V1  # noqa: E402
from trace2skill.trace_validation import validate_execution_trace  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--analysis",
        type=Path,
        default=ROOT / "product" / "evidence" / "v0.3" / "experience" / "analysis.json",
    )
    parser.add_argument(
        "--training-manifest",
        type=Path,
        default=ROOT / "product" / "evidence" / "v0.3" / "training" / "manifest.json",
    )
    parser.add_argument("--worker", default="trace-worker")
    parser.add_argument("--fresh-worker", action="store_true")
    parser.add_argument("--recover-task")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "product" / "evidence" / "v0.3" / "skills",
    )
    args = parser.parse_args()

    analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
    training_manifest = json.loads(args.training_manifest.read_text(encoding="utf-8"))
    traces = [
        validate_execution_trace(json.loads((ROOT / entry["path"]).read_text(encoding="utf-8")))
        for entry in training_manifest["entries"]
    ]
    experience = ExperienceValidator().validate(analysis["experience"], traces)
    username, password = load_agentteams_credentials(Path.home() / "hiclaw-manager.env")
    matrix = MatrixAuditClient("http://127.0.0.1:18080", username, password)
    agentteams = AgentTeamsClient(provisioner=matrix)
    role_spec = load_agent_specs(ROOT / "product" / "agent-specs")["skill-engineer"]
    worker = None if args.recover_task else (
        agentteams.create_worker(role_spec, "skill-v1")
        if args.fresh_worker
        else agentteams.get_worker(args.worker, "skill-engineer", role_spec.content_hash)
    )
    try:
        runner = SkillGenerationRunner(agentteams, matrix, role_spec)
        if args.recover_task:
            artifact, package = runner.recover_v1(
                args.recover_task, experience, timeout_seconds=min(args.timeout, 30)
            )
        else:
            artifact, package = runner.run_v1(
                experience, worker, timeout_seconds=args.timeout
            )
        version_root = args.output_root / SKILL_NAME / V1
        manifest = persist_skill_candidate(artifact, package, version_root)
    except Exception:
        if args.fresh_worker and worker is not None:
            try:
                agentteams.delete_worker(worker)
            except Exception:
                pass
        raise
    else:
        if args.fresh_worker and worker is not None:
            try:
                agentteams.delete_worker(worker)
            except Exception as cleanup_error:
                print(f"warning: fresh Worker cleanup failed: {cleanup_error}", file=sys.stderr)
    print(json.dumps({"output": str(version_root), "manifest": manifest}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
