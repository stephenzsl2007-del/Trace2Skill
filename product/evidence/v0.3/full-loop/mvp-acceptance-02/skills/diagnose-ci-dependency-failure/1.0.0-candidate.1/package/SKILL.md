---
name: diagnose-ci-dependency-failure
description: "Diagnose and repair npm CI dependency failures from error patterns"
---

# Diagnose CI dependency failures

Apply evidence-grounded npm dependency repairs and let the host Validator decide success. Read [the decision tree](references/decision-tree.md) and [failure patterns](references/failure-patterns.md) when matching an observed error.

## Trigger conditions

- npm ci fails with 'package-lock.json' and 'in sync' patterns
- npm run build fails with 'MODULE_NOT_FOUND' and 'Cannot find module'
- npm install fails with 'ERESOLVE' pattern

## Preconditions

- package.json and package-lock.json are present
- npm is the detected package manager
- No network access, force flags, or legacy-peer-deps are used

## Diagnostic workflow

- Read package.json and package-lock.json
- Run npm ci --dry-run to detect lockfile drift
- Run npm list <missing-module> to verify missing dev dependency
- Run npm install --dry-run to surface peer conflict

## Tool requirements

- cat
- npm

## Prohibited actions

- Use of force flags (e.g., --force)
- Use of legacy-peer-deps
- Execution of lifecycle scripts (e.g., preinstall, postinstall)
- Network access during diagnosis/repair
- Modification of any field in package.json beyond dependency declarations
- Do not use package managers that were not observed in the training Trace.
- Do not modify unrelated files or fields.
- Do not access hidden host data or private Validator configuration.

## Validation rules

- Repaired package.json must pass npm ci without error
- All deterministic validation stages (install, build, test) must succeed
- Original failure must be absent after repair
- No unrelated fields in package.json may change
