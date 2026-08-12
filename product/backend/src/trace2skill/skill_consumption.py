from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from .skill_packages import REQUIRED_FILES, SKILL_NAME


def installed_skill_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in Path(root).rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def install_verified_skill(source: Path, target: Path, expected_version: str) -> dict[str, Any]:
    source = Path(source)
    target = Path(target)
    if target.exists():
        raise FileExistsError(f"Skill install target already exists: {target}")
    files = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_file()
    }
    if files != REQUIRED_FILES:
        raise ValueError(f"Skill package files differ from contract: {sorted(files ^ REQUIRED_FILES)}")
    metadata = json.loads((source / "trace2skill.json").read_text(encoding="utf-8"))
    if metadata.get("name") != SKILL_NAME or metadata.get("version") != expected_version:
        raise ValueError("Skill package identity or version mismatch")
    source_digest = installed_skill_digest(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    target_digest = installed_skill_digest(target)
    if target_digest != source_digest:
        shutil.rmtree(target, ignore_errors=True)
        raise RuntimeError("installed Skill content hash mismatch")
    return {
        "name": SKILL_NAME,
        "version": expected_version,
        "source_digest": source_digest,
        "installed_digest": target_digest,
        "files": sorted(files),
        "load_transport": "verified-package-to-matrix-context",
    }
