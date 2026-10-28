// Local adaptations of regression observations in the pinned upstream fixes.
// Historical production files are imported unchanged. No external requests.
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { join, resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

const [id, folder] = process.argv.slice(2);
const root = resolve(folder);
const load = path => import(pathToFileURL(join(root, path)).href);
let checks = 0;
const check = (actual, expected, label) => { assert.deepEqual(actual, expected, label); checks++; };

try {
  if (id === "N01" || id === "N02") {
    const {default:pLimit} = await load("index.js");
    const limit = pLimit(1);
    check(await limit.map([1,2], value=>value*2), [2,4], "ordinary mapping preserves values");
    if (id === "N02") {
      const {map} = limit;
      check(await map([2,3], value=>value*2), [4,6], "detached mapping must still use its limiter");
    } else {
      const leaked=[];
      const onUnhandled = error => leaked.push(error.message);
      process.on("unhandledRejection",onUnhandled);
      const iteratorError=new Error("iterator failed");
      function* values(){yield 1;yield 2;throw iteratorError;}
      try {
        await assert.rejects(limit.map(values(),async()=>{await delay(5);throw new Error("mapper failed");}),error=>error===iteratorError);
        checks++;
        await limit(()=>{});
        await delay(20);
        check(leaked,[],"iteration failure must not leave orphaned mapper rejections");
      } finally {process.removeListener("unhandledRejection",onUnhandled);}
    }
    check(limit.activeCount,0,"completed calls release the active slot");
    check(limit.pendingCount,0,"completed calls drain the queue");
  } else if (id === "N03" || id === "N04") {
    const {default:pRetry} = await load("index.js");
    check(await pRetry(()=>42,{retries:0}),42,"ordinary successful call");
    if (id === "N03") {
      for (const retries of [0.5, 1.5, 10.1]) {
        let called=false;
        await assert.rejects(pRetry(()=>{called=true;return 42;},{retries}),TypeError);
        checks++;
        check(called,false,"invalid retry count rejected before invoking user function");
      }
      for (const retries of [1, 5, Infinity]) check(await pRetry(()=>42,{retries}),42,"valid retry counts remain accepted");
    } else {
      let attempts=0;
      const failure=new TypeError("non-network programming error");
      // Bound the historical unconsumed-retry loop without changing source.
      await assert.rejects(pRetry(()=>{if(++attempts===1)throw failure;return "unexpected second attempt";},
        {retries:5,minTimeout:0,shouldConsumeRetry:()=>false}),error=>error===failure);
      checks++;
      check(attempts,1,"non-network TypeError must abort despite unconsumed retry policy");
      let networkCalls=0;
      check(await pRetry(()=>{if(++networkCalls===1)throw new TypeError("Failed to fetch");return 7;},{retries:1,minTimeout:0}),7,"network TypeError remains retriable");
    }
  } else if (id === "N05" || id === "N06") {
    const {default:axios} = await load("index.js");
    const adapter=async config=>({data:"ok",status:200,statusText:"OK",headers:{},config});
    check((await axios.request({url:"/local-only",adapter})).data,"ok","ordinary injected adapter works");
    if(id==="N05") {
      const client=axios.create();
      const failure=new Error("validation failed");
      const leaked=[];const capture=error=>leaked.push(error);
      process.on("unhandledRejection",capture);
      let dispatched=0;
      client.interceptors.request.use(()=>{throw failure;},()=>Promise.reject(failure),{synchronous:true});
      try {
        let observed;
        try {await client.request({url:"/local-only",adapter:async config=>{dispatched++;return adapter(config);}});}catch(error){observed=error;}
        await delay(10);
        check(observed===failure,true,"rejected interceptor preserves original error");
        check(dispatched,0,"rejected interceptor must block dispatch");
        check(leaked.length,0,"interceptor rejection is observed");
      } finally {process.removeListener("unhandledRejection",capture);}
      for (const recover of [()=>undefined, ()=>Promise.resolve(undefined)]) {
        const recovering=axios.create();
        recovering.interceptors.request.use(()=>{throw failure;},recover,{synchronous:true});
        check((await recovering.request({url:"/local-only",adapter})).data,"ok","fulfilled rejection handler preserves recovery contract");
      }
    } else {
      for(const mode of ["structured","throw-prepare","throw-capture"]) {
        const oldPrepare=Error.prepareStackTrace, oldCapture=Error.captureStackTrace;
        const failure=new Error("adapter failed");let observed;
        try {
          if(mode==="structured")Error.prepareStackTrace=()=>({});
          if(mode==="throw-prepare")Error.prepareStackTrace=()=>{throw new Error("formatter failed");};
          if(mode==="throw-capture")Error.captureStackTrace=()=>{throw new Error("capture failed");};
          try {await axios.request({url:"/local-only",adapter:()=>Promise.reject(failure)});}catch(error){observed=error;}
        } finally {Error.prepareStackTrace=oldPrepare;Error.captureStackTrace=oldCapture;}
        check(observed===failure,true,`optional stack instrumentation must preserve original failure (${mode})`);
      }
    }
  } else if (id === "N07" || id === "N08") {
    const {default:Fastify}=await load("fastify.js");
    const app=Fastify();
    try {
      if(id==="N07") {
        const {setErrorStatusCode}=await load("lib/error-status.js");
        for(const key of ["statusCode","status"]) for(const status of [400,599,399,600]) {
          const reply={statusCode:200,code(value){this.statusCode=value;}};
          setErrorStatusCode(reply,{[key]:status});
          check(reply.statusCode,status>=400&&status<=599?status:500,`status helper validates ${key} ${status}`);
        }
        app.get("/:status",request=>{const error=new Error("route failed");error.statusCode=Number(request.params.status);throw error;});
        for(const status of [400,599,399,600]) {
          const response=await app.inject({method:"GET",url:`/${status}`});
          check(response.statusCode,status>=400&&status<=599?status:500,`error status ${status} stays in valid error range`);
        }
      } else {
        app.addContentTypeParser("application/custom",{parseAs:"string"},()=>{throw new Error("parser boom");});
        app.addContentTypeParser("application/stream",()=>{throw new Error("stream parser boom");});
        app.addContentTypeParser("application/limited",{parseAs:"string"},()=>{const error=new Error("too large");error.statusCode=413;throw error;});
        app.get("/",()=>"ok");app.post("/",()=>"unexpected route dispatch");
        check((await app.inject({method:"GET",url:"/"})).body,"ok","ordinary request works");
        const response=await app.inject({method:"POST",url:"/",headers:{"content-type":"application/custom"},payload:"{}"});
        check(response.statusCode,500,"synchronous parser throw becomes an HTTP error");
        check(response.json().message,"parser boom","parser error remains observable");
        for(const [type,status,message] of [["stream",500,"stream parser boom"],["limited",413,"too large"]]) {
          const extra=await app.inject({method:"POST",url:"/",headers:{"content-type":`application/${type}`},payload:"{}"});
          check(extra.statusCode,status,"parser throw preserves error status");
          check(extra.json().message,message,"parser throw preserves message");
        }
      }
    } finally {await app.close();}
  } else throw new Error("unknown probe id");
  console.log(JSON.stringify({status:"passed",checks}));
} catch(error) {
  console.error(JSON.stringify({status:"failed",checks,name:error.name,message:error.message,stack:String(error.stack).slice(0,4000)}));
  process.exitCode=1;
}
