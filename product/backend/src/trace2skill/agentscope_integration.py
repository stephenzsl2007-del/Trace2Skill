from __future__ import annotations

import importlib
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .security import sanitize


AGENTSCOPE_VERSION = "2.0.5"
AGENTSCOPE_STUDIO_VERSION = "1.0.9"


class AgentScopeUnavailable(RuntimeError):
    pass


def _attribute(value: Any) -> str | int | float | bool:
    clean = sanitize(value)
    if clean is None:
        return "missing"
    if isinstance(clean, (str, int, float, bool)):
        return clean
    import json

    return json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(slots=True)
class AgentScopeBridge:
    studio_url: str
    enabled: bool = True
    _tracer: Any = None

    def initialize(self) -> None:
        if not self.enabled:
            return
        parsed = urlparse(self.studio_url)
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("AgentScope Studio must be local for the MVP")
        try:
            agentscope = importlib.import_module("agentscope")
            version = getattr(agentscope, "__version__", None)
            if version != AGENTSCOPE_VERSION:
                raise AgentScopeUnavailable(
                    f"AgentScope SDK version mismatch: expected {AGENTSCOPE_VERSION}, received {version}"
                )
            agentscope.init(studio_url=self.studio_url)
            trace = importlib.import_module("opentelemetry.trace")
            self._tracer = trace.get_tracer("trace2skill", "0.3.0")
        except AgentScopeUnavailable:
            raise
        except Exception as exc:
            raise AgentScopeUnavailable(f"AgentScope initialization failed: {exc}") from exc

    def span(self, name: str, **attributes: Any) -> AbstractContextManager[Any]:
        if not self.enabled:
            return nullcontext()
        if self._tracer is None:
            raise AgentScopeUnavailable("AgentScope bridge has not been initialized")
        clean_attributes = {f"trace2skill.{key}": _attribute(value) for key, value in attributes.items()}
        return self._tracer.start_as_current_span(name, attributes=clean_attributes)

    def run_span(self, **attributes: Any) -> AbstractContextManager[Any]:
        return self.span("trace2skill.run", **attributes)

    def phase_span(self, phase: str, **attributes: Any) -> AbstractContextManager[Any]:
        return self.span(f"trace2skill.phase.{phase}", phase=phase, **attributes)

    def agent_span(self, role: str, **attributes: Any) -> AbstractContextManager[Any]:
        return self.span(f"trace2skill.agent.{role}", agent_role=role, **attributes)

    def tool_span(self, tool: str, **attributes: Any) -> AbstractContextManager[Any]:
        return self.span(f"trace2skill.tool.{tool}", tool=tool, **attributes)

    def validator_span(self, stage: str, **attributes: Any) -> AbstractContextManager[Any]:
        return self.span(f"trace2skill.validator.{stage}", validator_stage=stage, **attributes)
