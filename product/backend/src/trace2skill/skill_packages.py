from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .security import REDACTED, sanitize


SKILL_NAME = "diagnose-ci-dependency-failure"
V1 = "1.0.0-candidate.1"
V2_CANDIDATE = "2.0.0-candidate.1"
V2_RELEASE = "2.0.0"
REQUIRED_FILES = {
    "SKILL.md",
    "references/decision-tree.md",
    "references/failure-patterns.md",
    "validators/validate.py",
    "evals/cases.json",
    "trace2skill.json",
}
REQUIRED_SKILL_SECTIONS = {
    "Trigger conditions",
    "Preconditions",
    "Diagnostic workflow",
    "Tool requirements",
    "Prohibited actions",
    "Validation rules",
}


@dataclass(frozen=True, slots=True)
class SkillPackage:
    name: str
    version: str
    files: dict[str, str]
    zip_bytes: bytes
    manifest_hash: str


class SkillPackageValidator:
    def validate_candidate(
        self,
        candidate: dict[str, Any],
        *,
        expected_version: str,
        allowed_evidence_refs: set[str],
        previous: SkillPackage | None = None,
        refinement_refs: set[str] | None = None,
    ) -> SkillPackage:
        clean = sanitize(candidate)
        if clean != candidate or REDACTED in json.dumps(clean, ensure_ascii=False):
            raise ValueError("Skill candidate contains sensitive content")
        if clean.get("name") != SKILL_NAME or clean.get("version") != expected_version:
            raise ValueError("unexpected Skill name or version")
        files = clean.get("files")
        if not isinstance(files, dict) or set(files) != REQUIRED_FILES:
            raise ValueError(f"Skill package requires exactly: {sorted(REQUIRED_FILES)}")
        for path, content in files.items():
            pure = PurePosixPath(path)
            if pure.is_absolute() or ".." in pure.parts or not isinstance(content, str):
                raise ValueError(f"unsafe Skill package path: {path}")
        skill_md = files["SKILL.md"]
        if not skill_md.startswith("---\n") or f"name: {SKILL_NAME}" not in skill_md:
            raise ValueError("SKILL.md has invalid frontmatter")
        for section in REQUIRED_SKILL_SECTIONS:
            if f"## {section}" not in skill_md:
                raise ValueError(f"SKILL.md missing section: {section}")
        metadata = json.loads(files["trace2skill.json"])
        if metadata.get("name") != SKILL_NAME or metadata.get("version") != expected_version:
            raise ValueError("trace2skill.json identity mismatch")
        required_metadata = {
            "name", "version", "status", "experience_id", "source_trace_ids",
            "generation_model", "prompt_hash", "evidence_refs",
        }
        if not required_metadata.issubset(metadata):
            raise ValueError("trace2skill.json lacks required provenance metadata")
        if metadata.get("status") != "candidate":
            raise ValueError("generated Skill must remain a candidate until validation")
        if not isinstance(metadata.get("source_trace_ids"), list) or len(metadata["source_trace_ids"]) != 3:
            raise ValueError("generated Skill must reference exactly three training Traces")
        if not re.fullmatch(r"[a-f0-9]{64}", str(metadata.get("prompt_hash", ""))):
            raise ValueError("trace2skill.json prompt_hash must be SHA-256")
        references = set(metadata.get("evidence_refs") or [])
        if not references or not references.issubset(allowed_evidence_refs):
            raise ValueError("Skill candidate contains absent or unauthorized evidence references")
        cases = json.loads(files["evals/cases.json"])
        forbidden = json.dumps(cases, ensure_ascii=False).lower()
        if any(term in forbidden for term in ("reference_patch", "answer", "expected_package_json")):
            raise ValueError("eval cases leak held-out answers")
        compile(files["validators/validate.py"], "validators/validate.py", "exec")
        if expected_version == V1 and re.search(r"\bpnpm\b|pnpm-lock\.yaml", skill_md, re.IGNORECASE):
            raise ValueError("v1 must not invent unobserved pnpm support")
        if expected_version == V2_CANDIDATE:
            if previous is None or previous.version != V1:
                raise ValueError("v2 refinement requires the validated v1 package")
            lower = skill_md.lower()
            for required in ("pnpm-lock.yaml", "package-lock.json", "pnpm install", "npm ci"):
                if required not in lower:
                    raise ValueError(f"v2 lacks required package-manager branch: {required}")
            previous_guards = self._guardrails(previous.files["SKILL.md"])
            current_guards = self._guardrails(skill_md)
            if not previous_guards.issubset(current_guards):
                raise ValueError("v2 refinement weakened v1 guardrails")
            if not refinement_refs or not references.intersection(refinement_refs):
                raise ValueError("v2 is not grounded in the v1 failure report")
        archive = self._archive(files)
        manifest = {
            "name": SKILL_NAME,
            "version": expected_version,
            "files": {
                path: hashlib.sha256(content.encode()).hexdigest()
                for path, content in sorted(files.items())
            },
            "zip_sha256": hashlib.sha256(archive).hexdigest(),
        }
        manifest_hash = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return SkillPackage(SKILL_NAME, expected_version, files, archive, manifest_hash)

    @staticmethod
    def _guardrails(skill_md: str) -> set[str]:
        match = re.search(
            r"## Prohibited actions\s*(.*?)(?=\n## |\Z)", skill_md, re.DOTALL | re.IGNORECASE
        )
        if not match:
            return set()
        return {line.strip().lower() for line in match.group(1).splitlines() if line.strip().startswith("-")}

    @staticmethod
    def _archive(files: dict[str, str]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, content in sorted(files.items()):
                info = zipfile.ZipInfo(f"{SKILL_NAME}/{path}", date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, content.encode("utf-8"))
        return buffer.getvalue()


def install_package(package: SkillPackage, target: Path) -> str:
    target = Path(target)
    target.mkdir(parents=True, exist_ok=False)
    for relative, content in package.files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    digest = hashlib.sha256()
    for path in sorted(item for item in target.rglob("*") if item.is_file()):
        digest.update(path.relative_to(target).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
