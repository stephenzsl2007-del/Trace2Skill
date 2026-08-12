const compiler = require('@fixture/compiler');
if (compiler.compile() !== 'compiled') throw new Error('compiler contract failed');
console.log('build passed');
