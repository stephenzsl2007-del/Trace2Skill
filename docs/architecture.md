# Architecture

Trace2Skill separates probabilistic agent work from deterministic validation.

## Learning Loop

```text
Manager
  ├─ Execution Agent ──> sanitized training traces
  ├─ Trace Analyst ────> evidence-backed Experience Model
  ├─ Skill Engineer ───> Skill v1
  ├─ Evaluator ────────> held-out failure evidence
  └─ Skill Engineer ───> Skill v2
                              │
Host Validator <──────────────┘
       │
       └─> independently loaded Skill package
```

AgentTeams owns role orchestration, task hand-off, and collaboration state. Trace2Skill owns the learning pipeline, artifact contracts, evidence references, and version transitions. The host Validator owns the final pass/fail decision.

## Core Boundaries

- **Agent boundary:** Workers receive bounded prompts, tools, and answer-free fixture copies.
- **Evidence boundary:** persisted messages and tool results are sanitized and referenced by hashes.
- **Validation boundary:** install, build, test, error disappearance, and changed-file policy are evaluated outside the LLM.
- **Skill boundary:** generated packages have a fixed six-file contract and immutable version metadata.
- **Storage boundary:** services use repository interfaces so local persistence can be replaced without changing Agent contracts.

## Failure Handling

Each phase has a terminal status and finite timeout. Protocol failures may be retried within a small fixed budget; Validator failures are preserved as learning evidence and are not hidden by automatic reruns. A failed phase prevents publication or downstream success artifacts.

## Extension Points

- AgentScope can consume the existing event and trace model for visualization.
- Nacos can implement the Skill registry boundary and governed release lifecycle.
- SQLite/object storage can be replaced behind repository interfaces.
- Model access remains behind the Higress-compatible gateway boundary.

These integrations are optional around the core Trace → Experience → Skill → Validate loop.
