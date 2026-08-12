# Failure patterns

## npm ci.*package-lock.json.*in sync

- Diagnosis: lockfile drift between package.json and package-lock.json
- Repair: Update package.json dependency versions to match package-lock.json
- Evidence: trace:trace_13d6730f107949ed826b28629d3b9fe8#$TsEGHLlIzhczCuG1TzjBOStQruKkE0CZlLUi1wE5DVI

## MODULE_NOT_FOUND.*Cannot find module

- Diagnosis: missing dev dependency declaration in package.json
- Repair: Add required module to devDependencies in package.json
- Evidence: trace:trace_ef00f7013bfa42cba0d4c94eb772d590#$c5sQpJnt4Z6Gc6wZiomsKBvm4Ay5SuirpBz-3SIrFK4, matrix:$xq8HBShSZVakHsmvZD9D-VVx0AllnSEQAxWo0cKZs2g

## ERESOLVE

- Diagnosis: peer dependency conflict between declared dependencies and their peer requirements
- Repair: Change conflicting dependency version in package.json to satisfy peer requirements
- Evidence: trace:trace_1033f617c6e84149867370661fafe155#$8mtTM1wfuQXb9xzIjGF2IgGZrOX59E7iAXZytZgCcqc

## pnpm install.*--frozen-lockfile.*lockfile is not up to date

- Diagnosis: lockfile drift between package.json and pnpm-lock.yaml
- Repair: Update package.json dependency versions to match pnpm-lock.yaml
- Evidence: matrix:$S-bK6jhTT_JONlCf9CWze5wO85EYK9TAfHhVdt8HlKU
