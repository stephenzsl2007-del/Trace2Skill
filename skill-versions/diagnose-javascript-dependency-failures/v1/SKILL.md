---
name: diagnose-javascript-dependency-failures
description: Diagnose JavaScript package dependency resolution failures such as npm ERESOLVE and peer-dependency conflicts. Use when install or CI fails during npm, pnpm, or Yarn dependency resolution and the task requires safe triage, a minimal proposed change, and evidence-based verification.
---

# Diagnose JavaScript Dependency Failures

Treat this as a candidate workflow backed by one AgentTeams trace. Diagnose before mutating, preserve evidence, and never overstate validation.

## Workflow

1. Inspect `package.json` and lockfiles. Select npm for `package-lock.json`, pnpm for `pnpm-lock.yaml`, or Yarn for `yarn.lock`. Stop and ask if lockfiles conflict.
2. Reproduce or inspect the exact resolution error without changing dependencies. For the observed npm ERESOLVE case, begin with `npm ls` when command execution is available; if the benchmark supplies repository evidence only, reason from that evidence and do not invent tool execution.
3. Identify the direct dependency, the peer-dependency requirement, and the incompatible resolved version. Quote only the minimal relevant error lines.
4. Propose the smallest semver-compatible `package.json` change. Show the intended diff before editing.
5. After approval or when edits are already authorized, use only the detected package manager. Run install, then the relevant tests or CI command.
6. Report commands, exit codes, changed files, and whether the original error disappeared.
7. When the orchestrator supplies a machine-readable response contract, follow it exactly. Preserve every original `package.json` metadata field and change only the smallest necessary dependency field; never invent scripts, descriptions, peer dependencies, or unrelated packages.

## Guardrails

- Do not default to `--force` or `--legacy-peer-deps`; they may hide an invalid dependency graph.
- Do not mix npm, pnpm, and Yarn lockfiles or commands.
- Do not delete a lockfile or `node_modules` without explicit authorization and a clear recovery path.
- Do not claim success from reasoning alone. Require successful installation plus project-level verification.
- If a command suggested by this Skill is unsupported by the detected package manager, stop and adapt it rather than executing blindly.

## Evidence and status

Read `references/evidence.json` when provenance, confidence, conflicts, or failure lessons matter. This version remains `candidate`; it is not eligible for registry publication until held-out repair tasks pass.
