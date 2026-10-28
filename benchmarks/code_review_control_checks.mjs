// Post-trial claim adjudication only. Not a source of timed-trial feedback.
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import {join,resolve} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';

const [id,folder]=process.argv.slice(2);
const load=path=>import(pathToFileURL(join(resolve(folder),path)).href);
const facts=[];
if(id==='N01'||id==='N02'){
  const {default:pLimit}=await load('index.js');
  const signal=AbortSignal.abort();
  assert.equal(signal.aborted,true);
  assert.equal(signal.reason.name,'AbortError');
  facts.push('AbortSignal.abort() is valid and produces an aborted signal');
  const errors=[];const capture=error=>errors.push(error.message);
  process.on('unhandledRejection',capture);
  try {
    const limit=pLimit(1), failure=new Error('caller-visible failure');
    await assert.rejects(limit(()=>{throw failure;}),error=>error===failure);
    assert.equal(await limit(()=>42),42);
    await delay(10);
    assert.deepEqual(errors,[]);
    facts.push('caller observes original rejection; internal catch prevents orphaned rejection; queue proceeds');
    const queue=pLimit({concurrency:1,rejectOnClear:true});
    let release;
    const active=queue(()=>new Promise(resolve=>{release=resolve;}));
    await delay(0);
    const queued=queue(()=>99);
    const observed=queued.then(()=>({resolved:true}),error=>error);
    queue.clearQueue();
    const error=await observed;
    assert.equal(error.name,'AbortError');
    release(1);await active;
    assert.equal(queue.pendingCount,0);
    facts.push('clearQueue rejects queued calls with AbortError');
  } finally {process.removeListener('unhandledRejection',capture);}
} else if(id==='N03'||id==='N04') {
  const {default:pRetry}=await load('index.js');
  let attempts=0;
  const failure=new Error('transient');
  const contexts=[];
  await assert.rejects(pRetry(()=>{attempts++;throw failure;},{retries:1,minTimeout:0,onFailedAttempt:context=>contexts.push({attempt:context.attemptNumber,consumed:context.retriesConsumed,left:context.retriesLeft})}),error=>error===failure);
  assert.equal(attempts,2);
  assert.deepEqual(contexts,[{attempt:1,consumed:0,left:1},{attempt:2,consumed:1,left:0}]);
  facts.push('one consumed retry produces exactly two attempts with consistent counters');
  attempts=0;
  assert.equal(await pRetry(()=>{if(++attempts<3)throw failure;return 42;},{retries:1,minTimeout:0,shouldConsumeRetry:()=>false}),42);
  assert.equal(attempts,3);
  facts.push('non-consuming policy permits repeated failures followed by success');
  attempts=0;
  await assert.rejects(pRetry(()=>{attempts++;throw failure;},{retries:0,minTimeout:5000,shouldConsumeRetry:()=>false}),error=>error===failure);
  assert.equal(attempts,1);
  facts.push('zero remaining retries aborts even with non-consuming policy');
} else if(id==='N07') {
  const {buildErrorHandler}=await load('lib/error-handler.js');
  const root=buildErrorHandler();
  let chain=root;
  for(let i=0;i<3;i++)chain=buildErrorHandler(chain,()=>{});
  const seen=new Set();
  for(let current=chain;current!==null;current=Object.getPrototypeOf(current)){
    assert.equal(seen.has(current),false);seen.add(current);
  }
  assert.throws(()=>Object.setPrototypeOf(root,chain),TypeError);
  facts.push('built error handler chain is finite; JS rejects a cyclic prototype assignment');
  const {default:Fastify}=await load('fastify.js');const app=Fastify();
  try {
    app.setErrorHandler(()=>{throw new Error('handler failed');});
    app.get('/',()=>{throw new Error('route failed');});
    const response=await app.inject({method:'GET',url:'/'});
    assert.equal(response.statusCode,500);assert.equal(response.json().message,'handler failed');
    facts.push('throwing custom error handler terminates in a normal 500 response');
  } finally {await app.close();}
} else if(id==='N08') {
  const {default:Fastify}=await load('fastify.js');const app=Fastify();
  try {
    app.addContentTypeParser(/^application\/x-/, {parseAs:'string'},(_request,body,done)=>done(null,{parsed:body}));
    app.post('/',request=>request.body);
    for(const type of ['application/x-one','application/x-two','application/x-one']){
      const response=await app.inject({method:'POST',url:'/',headers:{'content-type':type},payload:'hello'});
      assert.equal(response.statusCode,200);assert.deepEqual(response.json(),{parsed:'hello'});
    }
    facts.push('regex parser registration, string-key lookup and cached lookup all work');
  } finally {await app.close();}
} else throw new Error('No adjudication checks for this case');
console.log(JSON.stringify({status:'passed',facts}));
