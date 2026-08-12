const util = require('ci-util');
if (util.version !== '1.0.0') throw new Error(`unexpected ci-util ${util.version}`);
console.log('build passed');
