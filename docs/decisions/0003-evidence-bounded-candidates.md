# ADR 0003: Keep generated Skills evidence-bounded

## Status

Accepted on Day 3.

## Decision

Trace2Skill produces a typed `candidate` document before it produces a publishable Skill. The candidate records source trace IDs, evidence event IDs, observed versus template-derived steps, conflicting recommendations, bounded failure categories, confidence, limitations, and security assertions.

A single successful trace can never produce `validated` or `published` status. The Day 3 analyzer is deterministic and rejects failed, partial, cancelled, semantically invalid, lifecycle-incomplete, secret-scan-failing, or unsupported traces.

Raw event payloads are not copied into the Candidate Skill. Provenance uses event IDs; failure output is reduced to a fixed category. This prevents Matrix identifiers, filesystem paths, logs, and latent credentials from being promoted into reusable instructions.

Agent-authored diagnostic commands are untrusted evidence. Before promotion, they must pass a default-deny package-manager-specific policy: approved read-only subcommands or supported dry-run forms only, no cross-manager command, shell control syntax, mutation, or dependency-bypass flags. Failure rejects candidate generation.

## Consequences

The generated Skill is honest but deliberately conservative. Trace-only observations are distinguishable from workflow templates. Day 4 must add genuine repository repair traces and held-out evaluation before confidence can increase or registry publication can be considered.
