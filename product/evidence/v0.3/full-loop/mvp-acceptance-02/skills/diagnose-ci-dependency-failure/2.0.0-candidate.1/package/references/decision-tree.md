# Decision tree

- When: npm ci fails with 'package-lock.json' and 'in sync' patterns
  Then: Align package.json dependencies with package-lock.json versions
  Evidence: trace:trace_13d6730f107949ed826b28629d3b9fe8#$TsEGHLlIzhczCuG1TzjBOStQruKkE0CZlLUi1wE5DVI
- When: npm run build fails with 'MODULE_NOT_FOUND' and 'Cannot find module'
  Then: Add missing module to devDependencies in package.json
  Evidence: trace:trace_ef00f7013bfa42cba0d4c94eb772d590#$c5sQpJnt4Z6Gc6wZiomsKBvm4Ay5SuirpBz-3SIrFK4
- When: npm install fails with 'ERESOLVE' pattern
  Then: Adjust conflicting dependency version in package.json to satisfy peer requirements
  Evidence: trace:trace_1033f617c6e84149867370661fafe155#$8mtTM1wfuQXb9xzIjGF2IgGZrOX59E7iAXZytZgCcqc
- When: pnpm install fails with '--frozen-lockfile' and 'lockfile is not up to date'
  Then: Align package.json dependencies with pnpm-lock.yaml versions
  Evidence: matrix:$S-bK6jhTT_JONlCf9CWze5wO85EYK9TAfHhVdt8HlKU
- When: pnpm run build fails with 'MODULE_NOT_FOUND' and 'Cannot find module'
  Then: Add missing module to devDependencies in package.json
  Evidence: matrix:$xq8HBShSZVakHsmvZD9D-VVx0AllnSEQAxWo0cKZs2g
