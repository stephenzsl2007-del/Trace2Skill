from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "product" / "evidence" / "v0.3" / "full-loop",
    )
    args = parser.parse_args()
    source = ROOT / "product" / "runs" / args.run_id
    state = json.loads((source / "run.json").read_text(encoding="utf-8"))
    if state.get("status") != "succeeded" or state.get("phase") != "completed":
        raise ValueError("only a completed successful MVP run can be frozen")
    v1 = json.loads((source / "v1-probes" / "manifest.json").read_text(encoding="utf-8"))
    v2 = json.loads((source / "v2-probes" / "manifest.json").read_text(encoding="utf-8"))
    if not v1.get("gate_passed") or not v2.get("gate_passed"):
        raise ValueError("MVP run probe gates are incomplete")
    target = args.output_root / args.run_id
    if target.exists():
        raise FileExistsError(f"frozen MVP run already exists: {target}")
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    files = {
        path.relative_to(target).as_posix(): sha256(path)
        for path in sorted(item for item in target.rglob("*") if item.is_file())
    }
    manifest = {
        "schema_version": "0.3",
        "run_id": args.run_id,
        "status": "succeeded",
        "v1_gate_passed": True,
        "v2_gate_passed": True,
        "files": files,
    }
    (target / "freeze-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(target), "file_count": len(files)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
