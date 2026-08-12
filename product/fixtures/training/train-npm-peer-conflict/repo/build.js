const version = require('./node_modules/react/package.json').version;
if (!version.startsWith('18.')) throw new Error(`expected compatible React 18, received ${version}`);
console.log('build passed');
