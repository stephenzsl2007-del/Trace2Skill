const lib = require('frozen-lib');
if (lib.version !== '1.0.0') throw new Error(`unexpected frozen-lib ${lib.version}`);
console.log('build passed');
