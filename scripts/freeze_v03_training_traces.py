from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product" / "backend" / "src"))

from trace2skill.fixtures import FixtureCatalog  # noqa: E402
from trace2skill.trace_validation import validate_execution_trace  # noqa: E402


EXPECTED = {
    "train-npm-peer-conflict",
    "train-npm-lockfile-drift",
    "train-npm-missing-dev-dependency",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("traces", nargs=3, type=Path)
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "product" / "evidence" / "v0.3" / "training"
    )
    args = parser.parse_args()
    fixtures = FixtureCatalog(ROOT / "product" / "fixtures").load()
    selected: dict[str, tuple[Path, dict]] = {}
    for path in args.traces:
        trace = validate_execution_trace(json.loads(path.read_text(encoding="utf-8")))
        fixture_id = trace["fixture_id"]
        if fixture_id not in EXPECTED or fixture_id in selected:
            raise ValueError(f"unexpected or duplicate training fixture: {fixture_id}")
        if trace["fixture_hash"] != fixtures[fixture_id].manifest["initial_snapshot_hash"]:
            raise ValueError(f"fixture hash mismatch in Trace: {fixture_id}")
        selected[fixture_id] = (path, trace)
    if set(selected) != EXPECTED:
        raise ValueError(f"training Trace set mismatch: {sorted(set(selected) ^ EXPECTED)}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for fixture_id in sorted(selected):
        source, trace = selected[fixture_id]
        target = args.output_dir / f"{fixture_id}.trace.json"
        target.write_bytes(source.read_bytes())
        entries.append(
            {
                "fixture_id": fixture_id,
                "trace_id": trace["trace_id"],
                "task_id": trace["task_id"],
                "fixture_hash": trace["fixture_hash"],
                "trace_sha256": digest(target),
                "path": target.relative_to(ROOT).as_posix(),
                "validator_passed": True,
            }
        )
    manifest = {
        "schema_version": "0.3",
        "created_at": datetime.now(UTC).isoformat(),
        "purpose": "Trace2Skill MVP three-Trace training input",
        "entries": entries,
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"manifest": str(manifest_path), "trace_count": len(entries)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
