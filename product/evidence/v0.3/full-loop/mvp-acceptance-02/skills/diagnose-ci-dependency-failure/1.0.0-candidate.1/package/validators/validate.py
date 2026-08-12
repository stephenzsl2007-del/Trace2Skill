from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def run(command: list[str], cwd: Path) -> int:
    return subprocess.run(command, cwd=cwd, shell=False, check=False).returncode


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    repository = Path(sys.argv[1]).resolve()
    package = json.loads((repository / "package.json").read_text(encoding="utf-8"))
    install = ["npm", "ci" if (repository / "package-lock.json").exists() else "install", "--offline", "--ignore-scripts", "--no-audit", "--no-fund"]
    commands = [install, ["npm", "run", "build"], ["npm", "test"]]
    if not all(name in package.get("scripts", {}) for name in ("build", "test")):
        return 2
    for command in commands:
        if run(command, repository) != 0:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
