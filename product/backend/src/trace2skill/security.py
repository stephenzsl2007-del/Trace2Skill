from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


REDACTED = "[REDACTED]"
SENSITIVE_KEYS = re.compile(
    r"(?:api[-_]?key|authorization|password|passwd|secret|access[-_]?token|refresh[-_]?token|credential)",
    re.IGNORECASE,
)
SENSITIVE_TEXT = [
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"), REDACTED),
    (
        re.compile(r"\b(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
        lambda match: f"{match.group(1)}{REDACTED}",
    ),
    (
        re.compile(r"((?:api[-_]?key|password|secret|token)\s*[:=]\s*)[^\s,;]+", re.IGNORECASE),
        lambda match: f"{match.group(1)}{REDACTED}",
    ),
]


def redact_text(value: str) -> str:
    redacted = value
    for pattern, replacement in SENSITIVE_TEXT:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def sanitize(value: Any, key: str | None = None) -> Any:
    if key and SENSITIVE_KEYS.search(key):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {str(k): sanitize(v, str(k)) for k, v in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [sanitize(item) for item in value]
    return value


def sanitized_json(value: Any) -> str:
    return json.dumps(sanitize(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
