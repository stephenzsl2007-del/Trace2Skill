from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRAINING = (
    "train-npm-peer-conflict",
    "train-npm-lockfile-drift",
    "train-npm-missing-dev-dependency",
)
V1 = "1.0.0-candidate.1"
V2 = "2.0.0-candidate.1"
SKILL = "diagnose-ci-dependency-failure"


def now() -> str:
    return datetime.now(UTC).isoformat()


def save_state(path: Path, state: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def recover_interrupted_runs(runs_root: Path) -> None:
    if not runs_root.exists():
        return
    for state_path in runs_root.glob("*/run.json"):
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("status") in {"queued", "running"}:
            process_id = state.get("pid")
            if isinstance(process_id, int):
                try:
                    os.kill(process_id, 0)
                except OSError:
                    pass
                else:
                    continue
            state["status"] = "failed"
            state["ended_at"] = now()
            state["error"] = {
                "type": "InterruptedRun",
                "message": "previous orchestrator process ended before a terminal state",
            }
            save_state(state_path, state)


def execute_phase(state_path: Path, state: dict, phase: str, command: list[str], timeout: int) -> None:
    state["phase"] = phase
    state["status"] = "running"
    state["phases"][phase] = {"status": "running", "started_at": now()}
    save_state(state_path, state)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        shell=False,
        check=False,
    )
    receipt = {
        "status": "succeeded" if completed.returncode == 0 else "failed",
        "exit_code": completed.returncode,
        "ended_at": now(),
        "output_tail": completed.stdout[-4000:],
    }
    state["phases"][phase] = {**state["phases"][phase], **receipt}
    save_state(state_path, state)
    if completed.returncode != 0:
        raise RuntimeError(f"phase {phase} failed with exit {completed.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=f"mvp-{uuid.uuid4().hex[:8]}")
    parser.add_argument("--execution-worker", default="trace-worker")
    parser.add_argument("--skill-worker", default="skill-worker")
    parser.add_argument("--consumer-worker", default="consumer-worker")
    parser.add_argument("--task-timeout", type=int, default=180)
    parser.add_argument("--show")
    args = parser.parse_args()
    runs_root = ROOT / "product" / "runs"
    if args.show:
        state_path = runs_root / args.show / "run.json"
        if not state_path.exists():
            raise FileNotFoundError(f"unknown MVP run: {args.show}")
        print(state_path.read_text(encoding="utf-8"), end="")
        return 0
    recover_interrupted_runs(runs_root)
    run_root = runs_root / args.run_id
    run_root.mkdir(parents=True, exist_ok=False)
    state_path = run_root / "run.json"
    state = {
        "schema_version": "0.3",
        "run_id": args.run_id,
        "kind": "full-loop",
        "pid": os.getpid(),
        "status": "queued",
        "phase": None,
        "started_at": now(),
        "ended_at": None,
        "phases": {},
        "error": None,
    }
    save_state(state_path, state)
    python = sys.executable
    try:
        raw = run_root / "raw-training"
        raw.mkdir()
        for fixture in TRAINING:
            execute_phase(
                state_path,
                state,
                f"training_execution:{fixture}",
                [python, "scripts/run_v03_execution.py", fixture, "--worker", args.execution_worker, "--timeout", str(args.task_timeout), "--output", str(raw / f"{fixture}.json")],
                args.task_timeout + 90,
            )
        training = run_root / "training"
        execute_phase(
            state_path,
            state,
            "training_freeze",
            [python, "scripts/freeze_v03_training_traces.py", *[str(raw / f"{fixture}.json") for fixture in TRAINING], "--output-dir", str(training)],
            60,
        )
        analysis = run_root / "experience" / "analysis.json"
        execute_phase(
            state_path,
            state,
            "trace_analysis",
            [python, "scripts/run_v03_analysis.py", "--manifest", str(training / "manifest.json"), "--worker", args.execution_worker, "--timeout", str(args.task_timeout), "--output", str(analysis)],
            args.task_timeout + 90,
        )
        skills = run_root / "skills"
        execute_phase(
            state_path,
            state,
            "skill_v1_generation",
            [python, "scripts/run_v03_skill_v1.py", "--analysis", str(analysis), "--training-manifest", str(training / "manifest.json"), "--worker", args.skill_worker, "--timeout", str(args.task_timeout), "--output-root", str(skills)],
            args.task_timeout + 90,
        )
        v1_probes = run_root / "v1-probes"
        execute_phase(
            state_path,
            state,
            "v1_evaluation",
            [python, "scripts/run_v03_v1_probes.py", "--worker", args.execution_worker, "--timeout", str(args.task_timeout), "--skill-version", V1, "--skill-root", str(skills / SKILL / V1 / "package"), "--output-root", str(v1_probes)],
            3 * (args.task_timeout + 90),
        )
        execute_phase(
            state_path,
            state,
            "refinement",
            [python, "scripts/run_v03_skill_v2.py", "--worker", args.skill_worker, "--timeout", str(args.task_timeout), "--analysis", str(analysis), "--v1-root", str(skills / SKILL / V1 / "package"), "--failure-root", str(v1_probes), "--output-root", str(skills)],
            args.task_timeout + 90,
        )
        execute_phase(
            state_path,
            state,
            "skill_v2_evaluation",
            [python, "scripts/run_v03_v1_probes.py", "--worker", args.execution_worker, "--timeout", str(args.task_timeout), "--skill-version", V2, "--skill-root", str(skills / SKILL / V2 / "package"), "--output-root", str(run_root / "v2-probes")],
            3 * (args.task_timeout + 90),
        )
        consumer_command = [
            python,
            "scripts/verify_v03_skill_consumer.py",
            "--source-worker",
            args.execution_worker,
            "--timeout",
            str(max(args.task_timeout, 300)),
            "--skill-version",
            V2,
            "--skill-root",
            str(skills / SKILL / V2 / "package"),
            "--output-root",
            str(run_root / "skill-consumer"),
        ]
        consumer_command.extend(["--consumer-worker", args.consumer_worker])
        execute_phase(
            state_path,
            state,
            "skill_consumer_verification",
            consumer_command,
            max(args.task_timeout, 300) + 300,
        )
        state["status"] = "succeeded"
        state["phase"] = "completed"
    except Exception as exc:
        state["status"] = "failed"
        state["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        state["ended_at"] = now()
        save_state(state_path, state)
    print(json.dumps({"run_id": args.run_id, "status": state["status"], "state": str(state_path)}, ensure_ascii=False))
    return 0 if state["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
