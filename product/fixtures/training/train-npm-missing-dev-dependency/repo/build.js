const builder = require('ci-builder');
if (builder.build() !== 'built') throw new Error('builder contract failed');
console.log('build passed');
