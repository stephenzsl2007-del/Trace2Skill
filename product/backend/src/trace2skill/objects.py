from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .security import sanitize


class ObjectStore:
    """Immutable SHA-256 object store with atomic writes."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, digest: str) -> Path:
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("invalid sha256 digest")
        return self.root / "sha256" / digest[:2] / digest

    def put_bytes(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        target = self._path(digest)
        if target.exists():
            return f"sha256:{digest}"
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=target.parent, delete=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        try:
            temporary.replace(target)
        except FileExistsError:
            temporary.unlink(missing_ok=True)
        return f"sha256:{digest}"

    def put_json(self, value: Any) -> str:
        payload = json.dumps(
            sanitize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return self.put_bytes(payload)

    def get_bytes(self, reference: str) -> bytes:
        algorithm, separator, digest = reference.partition(":")
        if separator != ":" or algorithm != "sha256":
            raise ValueError("unsupported object reference")
        return self._path(digest).read_bytes()

    def get_json(self, reference: str) -> Any:
        return json.loads(self.get_bytes(reference))

    def verify(self, reference: str) -> bool:
        data = self.get_bytes(reference)
        return f"sha256:{hashlib.sha256(data).hexdigest()}" == reference
