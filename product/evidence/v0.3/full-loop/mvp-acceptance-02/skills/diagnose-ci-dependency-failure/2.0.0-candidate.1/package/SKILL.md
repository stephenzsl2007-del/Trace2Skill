---
name: diagnose-ci-dependency-failure
description: "Diagnose and repair CI dependency failures for npm and pnpm from error patterns"
---

# Diagnose CI dependency failures

Detect the repository package manager before choosing commands. Read [the decision tree](references/decision-tree.md) and [failure patterns](references/failure-patterns.md) for details.

## Trigger conditions

- npm ci fails with 'package-lock.json' and 'in sync' patterns
- npm run build fails with 'MODULE_NOT_FOUND' and 'Cannot find module'
- npm install fails with 'ERESOLVE' pattern
- pnpm install fails with '--frozen-lockfile' and 'lockfile is not up to date' patterns
- pnpm run build fails with 'MODULE_NOT_FOUND' and 'Cannot find module'

## Preconditions

- package.json is present
- Either package-lock.json or pnpm-lock.yaml is present (but not both)
- No network access, force flags, or lifecycle script execution is used

## Package manager detection

- package-lock.json or npm-shrinkwrap.json -> npm
- pnpm-lock.yaml or pnpm-workspace.yaml -> pnpm

## Diagnostic workflow

- Check for package-lock.json → npm
- Check for pnpm-lock.yaml → pnpm
- Read package.json and lockfile (package-lock.json or pnpm-lock.yaml)
- Run npm ci --dry-run or pnpm install --dry-run --frozen-lockfile to detect lockfile drift
- Run npm list <missing-module> or pnpm list <missing-module> to verify missing dev dependency
- Run npm install --dry-run or pnpm install --dry-run to surface peer conflict
- For npm, use npm ci when a lockfile exists.
- For pnpm, use pnpm install --frozen-lockfile when a lockfile exists.

## Tool requirements

- cat
- npm
- pnpm

## Prohibited actions

- Use of force flags (e.g., --force, --no-frozen-lockfile)
- Use of legacy-peer-deps
- Execution of lifecycle scripts (e.g., preinstall, postinstall)
- Network access during diagnosis/repair
- Modification of any field in package.json beyond dependency declarations
- Modifying lockfiles directly
- Support for package managers other than npm or pnpm
- do not access hidden host data or private validator configuration.
- do not modify unrelated files or fields.
- do not use package managers that were not observed in the training trace.
- use of force flags (e.g., --force)

## Validation rules

- Repaired package.json must pass npm ci or pnpm install without error
- All deterministic validation stages (install, build, test) must succeed using the detected package manager
- Original failure must be absent after repair
- No unrelated fields in package.json may change
