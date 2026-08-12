from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product" / "backend" / "src"))

from trace2skill.fixtures import snapshot_hash  # noqa: E402


def main() -> int:
    fixture_root = ROOT / "product" / "fixtures"
    updated = 0
    for manifest_path in sorted(fixture_root.glob("*/*/host.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["initial_snapshot_hash"] = snapshot_hash(manifest_path.parent / "repo")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        updated += 1
    if updated != 6:
        raise RuntimeError(f"expected six fixtures, found {updated}")
    print(f"Frozen {updated} Trace2Skill v0.3 fixture snapshots.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
