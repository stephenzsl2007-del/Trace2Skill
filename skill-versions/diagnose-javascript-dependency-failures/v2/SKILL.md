---
name: diagnose-javascript-dependency-failures
description: Diagnose JavaScript dependency resolution failures including npm ERESOLVE, peer conflicts, and pnpm workspace package errors. Use when npm, pnpm, or Yarn install/CI resolution fails and the task requires safe triage, a minimal manifest change, and evidence-based verification.
---

# Diagnose JavaScript Dependency Failures

Treat this as a candidate v2 workflow backed by npm execution evidence and a pnpm training failure. Diagnose before mutating, preserve evidence, and never overstate validation.

## Workflow

1. Inspect `package.json`, workspace configuration, and lockfiles. Select npm for `package-lock.json`, pnpm for `pnpm-lock.yaml` or `pnpm-workspace.yaml`, and Yarn for `yarn.lock`. Stop if package-manager evidence conflicts.
2. Keep the parsed original manifest as an immutable base. Construct a complete proposed manifest mechanically: deep-copy the original object, copy the relevant dependency map, change only the necessary dependency entry in that copied map, then assign the map back. Do not author a replacement manifest from memory.
3. Reproduce or inspect the exact error without changing dependencies. If command execution is unavailable, reason only from supplied repository evidence and do not invent execution.
4. Branch on the failure:
   - For npm peer conflicts, identify the direct dependency, peer range, and incompatible resolved version; propose the smallest compatible version change.
   - For `ERR_PNPM_WORKSPACE_PKG_NOT_FOUND`, compare dependency keys with workspace package `name` fields. Correct only the mismatched dependency key and preserve its `workspace:` protocol and range.
5. Show the minimal intended manifest diff. Before returning a complete manifest object, enforce both invariants: `set(proposed.keys()) == set(original.keys())`, and every value outside the changed dependency map is deeply equal to the original. If either check fails, discard the proposal and rebuild it from the original object. Never add a field merely because it is common in package manifests.
6. After authorization, use only the detected package manager to install and run relevant tests or CI. Report commands, exit codes, changed files, and whether the original error disappeared.
7. Follow any machine-readable response contract exactly; it takes precedence over conversational reporting format.

## Guardrails

- Do not default to `--force` or `--legacy-peer-deps`; they may hide an invalid dependency graph.
- Do not mix npm, pnpm, and Yarn lockfiles or commands.
- Do not delete a lockfile or `node_modules` without explicit authorization and a clear recovery path.
- Do not claim success from reasoning alone. Require successful installation plus project-level verification.
- If a command suggested by this Skill is unsupported by the detected package manager, stop and adapt it rather than executing blindly.
- For pnpm workspace errors, do not replace `workspace:` with a registry version unless repository evidence proves the dependency is external.
- When the supplied root manifest contains only `name`, `private`, `version`, and `dependencies`, the returned root manifest must contain exactly those four keys. This is an integrity example, not permission to remove keys from a different manifest.

## Evidence and status

Read `references/evidence.json` when original trace provenance matters. This version remains `candidate`; pnpm held-out evidence must pass before any validation or publication claim.
