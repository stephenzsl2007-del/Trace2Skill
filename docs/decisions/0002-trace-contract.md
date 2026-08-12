# ADR 0002: Use an observable, event-sourced Trace v0.1 contract

## Status

Accepted on Day 2.

## Decision

Trace2Skill stores one append-only normalized event stream per task. Events retain Matrix provenance and actor identity, classify lifecycle and tool activity, and connect calls to results through `call_id`.

The trace stores observable messages and tool evidence only. It does not request or persist hidden chain-of-thought. Failed actions remain in the stream because they are necessary to learn guardrails and recovery steps.

The canonical interchange format is JSON validated against `schemas/trace.schema.json`. Storage engines may normalize it into relational, document, vector, or UnifiedModel representations, but those implementations must preserve the schema semantics and provenance join keys.

## Consequences

- Trace extraction is auditable back to source events.
- Skill generation can separate successful steps, failed attempts, and verification evidence.
- MCP, AgentLoop, LoongSuite, GitHub, and sandbox adapters can emit the same event envelope.
- Raw traces require redaction before persistence and a second pseudonymization step before public release.
- Schema changes require a new `schema_version`; consumers must not infer compatibility from file shape alone.
