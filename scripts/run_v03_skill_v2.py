from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_RUN = ROOT / "product/evidence/v0.3/full-loop/mvp-acceptance-02"
sys.path.insert(0, str(ROOT / "product" / "backend" / "src"))

from trace2skill.agent_specs import load_agent_specs  # noqa: E402
from trace2skill.agentteams import AgentTeamsClient  # noqa: E402
from trace2skill.matrix_audit import MatrixAuditClient, load_agentteams_credentials  # noqa: E402
from trace2skill.skill_generation import SkillGenerationRunner, persist_skill_candidate  # noqa: E402
from trace2skill.skill_packages import (  # noqa: E402
    REQUIRED_FILES, SKILL_NAME, V1, V2_CANDIDATE, SkillPackageValidator,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", default="skill-worker")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument(
        "--analysis",
        type=Path,
        default=CANONICAL_RUN / "experience/analysis.json",
    )
    parser.add_argument(
        "--v1-root",
        type=Path,
        default=CANONICAL_RUN / "skills" / SKILL_NAME / V1 / "package",
    )
    parser.add_argument(
        "--failure-root",
        type=Path,
        default=CANONICAL_RUN / "v1-probes",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "product/runs/manual-refinement/skills",
    )
    args = parser.parse_args()
    analysis = json.loads(
        args.analysis.read_text(encoding="utf-8")
    )
    experience = analysis["experience"]
    v1_root = args.v1_root
    v1_candidate = {
        "name": SKILL_NAME,
        "version": V1,
        "files": {
            relative: (v1_root / relative).read_text(encoding="utf-8")
            for relative in REQUIRED_FILES
        },
    }
    previous = SkillPackageValidator().validate_candidate(
        v1_candidate,
        expected_version=V1,
        allowed_evidence_refs={
            str(ref)
            for key in (
                "task_signatures", "package_manager_detection", "success_paths", "failed_attempts",
                "preconditions", "tools_permissions", "prohibited_actions", "validator_rules", "conclusions",
            )
            for item in experience.get(key) or []
            for ref in item.get("evidence_refs") or []
        },
    )
    report_root = args.failure_root
    reports = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(report_root.glob("*.failure.json"))
    ]
    username, password = load_agentteams_credentials(Path.home() / "hiclaw-manager.env")
    matrix = MatrixAuditClient("http://127.0.0.1:18080", username, password)
    agentteams = AgentTeamsClient(provisioner=matrix)
    role_spec = load_agent_specs(ROOT / "product/agent-specs")["skill-engineer"]
    worker = agentteams.get_worker(args.worker, "skill-engineer", role_spec.content_hash)
    artifact, package = SkillGenerationRunner(agentteams, matrix, role_spec).run_v2(
        experience, previous, reports, worker, timeout_seconds=args.timeout
    )
    version_root = args.output_root / SKILL_NAME / V2_CANDIDATE
    manifest = persist_skill_candidate(artifact, package, version_root)
    print(json.dumps({"output": str(version_root), "manifest": manifest}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
