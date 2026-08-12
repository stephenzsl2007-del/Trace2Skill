# ADR 0001: Pin AgentTeams v1.1.2

## Status

Accepted for the competition MVP.

## Decision

Use AgentTeams `v1.1.2` at commit `a99457830fafb99c991bdb666aa8a1eef2f83b12` for local development and recorded experiments.

Do not install from a floating `latest` tag during benchmark collection. The installation wrapper passes the fixed image version to the official upstream installer.

## Rationale

- It is the current stable release inspected during Day 1.
- It provides Manager/Worker orchestration, Matrix-visible collaboration, declarative resources, shared storage, Higress model routing, and Nacos-backed skill discovery.
- Pinning preserves reproducibility across the eight-day build and prevents beta API changes from invalidating recorded traces.

## Compatibility boundary

Trace2Skill will depend on exported trace events and standard `SKILL.md` packages, not private AgentTeams internals. A future upgrade should require adapter validation rather than changes to the experience model or benchmark format.
