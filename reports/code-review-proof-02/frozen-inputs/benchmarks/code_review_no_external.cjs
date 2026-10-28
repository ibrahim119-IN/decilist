// Defense in depth for local probe processes, not an OS security sandbox.
const deny=()=>{throw new Error('External network/process execution disabled in local regression probe');};
const net=require('node:net');
net.Socket.prototype.connect=deny;
require('node:tls').connect=deny;
for(const key of ['exec','execFile','spawn','fork','execSync','execFileSync','spawnSync'])require('node:child_process')[key]=deny;
globalThis.fetch=deny;
require('node:module').syncBuiltinESMExports();
