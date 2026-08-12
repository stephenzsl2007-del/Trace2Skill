const version = require('./node_modules/eslint/package.json').version;
if (!version.startsWith('8.')) throw new Error(`expected compatible ESLint 8, received ${version}`);
console.log('build passed');
