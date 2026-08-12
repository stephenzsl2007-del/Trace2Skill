from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    repo = Path(sys.argv[1]).resolve()
    package = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    if (repo / "pnpm-lock.yaml").exists() or (repo / "pnpm-workspace.yaml").exists():
        install = ["pnpm", "install", "--offline", "--frozen-lockfile", "--ignore-scripts"]
        run = "pnpm"
    elif (repo / "package-lock.json").exists() or (repo / "npm-shrinkwrap.json").exists():
        install = ["npm", "ci", "--offline", "--ignore-scripts", "--no-audit", "--no-fund"]
        run = "npm"
    else:
        return 2
    if not all(name in package.get("scripts", {}) for name in ("build", "test")):
        return 2
    for command in (install, [run, "run", "build"], [run, "test"]):
        if subprocess.run(command, cwd=repo, shell=False, check=False).returncode != 0:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
