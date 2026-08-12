from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product" / "backend" / "src"))

from trace2skill.matrix_audit import MatrixAuditClient, load_agentteams_credentials  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("--matrix-base", default="http://127.0.0.1:18080")
    parser.add_argument("--env-file", type=Path, default=Path.home() / "hiclaw-manager.env")
    args = parser.parse_args()
    username, password = load_agentteams_credentials(args.env_file)
    events = MatrixAuditClient(args.matrix_base, username, password).task_events(args.task_id)
    print(json.dumps({"task_id": args.task_id, "events": events}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
