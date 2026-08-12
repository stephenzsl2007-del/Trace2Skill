from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROLES = {"execution", "trace-analyst", "skill-engineer", "evaluator"}


@dataclass(frozen=True, slots=True)
class AgentSpec:
    role: str
    runtime: str
    model: str
    timeout_seconds: int
    prompt: str
    allowed_tools: tuple[str, ...]
    permissions: dict[str, bool]
    content_hash: str


def load_agent_specs(root: Path) -> dict[str, AgentSpec]:
    result: dict[str, AgentSpec] = {}
    for path in sorted(Path(root).glob("*.yaml")):
        raw = path.read_bytes()
        value: dict[str, Any] = json.loads(raw)
        if value.get("spec_version") != "0.3":
            raise ValueError(f"unsupported AgentSpec version: {path}")
        role = value.get("role")
        if role not in ROLES or role in result:
            raise ValueError(f"invalid or duplicate AgentSpec role: {role}")
        tools = tuple(value.get("allowed_tools") or ())
        if not tools or len(tools) != len(set(tools)):
            raise ValueError(f"AgentSpec tools must be unique and non-empty: {role}")
        permissions = value.get("permissions")
        if not isinstance(permissions, dict) or not all(
            isinstance(item, bool) for item in permissions.values()
        ):
            raise ValueError(f"AgentSpec permissions must be booleans: {role}")
        if permissions.get("validator_private_read") or permissions.get("registry_write"):
            raise ValueError(f"AgentSpec exceeds product permission boundary: {role}")
        result[role] = AgentSpec(
            role=role,
            runtime=str(value["runtime"]),
            model=str(value["model"]),
            timeout_seconds=int(value["timeout_seconds"]),
            prompt=str(value["prompt"]),
            allowed_tools=tools,
            permissions=permissions,
            content_hash=hashlib.sha256(raw).hexdigest(),
        )
    if set(result) != ROLES:
        raise ValueError(f"expected AgentSpecs {sorted(ROLES)}, found {sorted(result)}")
    return result
