import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { Decilist, HttpTransport, RuntimeCompatibilityError, RuntimeConnectionError } from "../dist/index.js";
import { launcherArgs } from "../dist/cli-args.js";

const health = {service:"decilist",schema_version:"1",runtime_version:"0.1.0a1",ready:true,status:"ok",provider:"demo"};
test("launcher preserves application --python arguments after the command separator",()=>{
  const child=["runtime","run","--provider","demo","--","node","app.mjs","--python","application-setting"];
  assert.deepEqual(launcherArgs(child),{python:"python",args:child});
  assert.deepEqual(launcherArgs(["--python","C:/Python/python.exe",...child]),{python:"C:/Python/python.exe",args:child});
  assert.throws(()=>launcherArgs(["--python","--","node","app.mjs"]));
});
async function serve(handler, run) {
  const server = createServer(handler);
  await new Promise(resolve => server.listen(0,"127.0.0.1",resolve));
  try { await run(`http://127.0.0.1:${server.address().port}`); }
  finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
}
test("connect waits for readiness once then direct calls share the endpoint", async () => {
  let checks=0;
  await serve((req,res)=>{
    res.setHeader("Content-Type","application/json");
    if(req.url==="/health") return res.end(JSON.stringify({...health,ready:++checks>1}));
    assert.equal(req.url,"/v1/decisions");
    res.end(JSON.stringify({schema_version:"1",request_id:"test",kind:"predict",status:"accepted",value:true,probabilities:{false:.1,true:.9},confidence:.9,provider:"demo",model:"baseline",latency_ms:1,cost_usd:null,attempts:[]}));
  }, async baseUrl=>{
    const sdk=await Decilist.connect({baseUrl,waitMs:1000});
    assert.equal((await sdk.predict("Ready", "Ready?")).value,true);
    assert.equal((await sdk.predict("Ready", "Ready?")).value,true);
    assert.equal(checks,2);
  });
});
test("incompatible schema fails before sending decision evidence", async()=>{
  const paths=[];
  await serve((req,res)=>{paths.push(req.url);res.end(JSON.stringify({...health,schema_version:"2"}));},async baseUrl=>{
    await assert.rejects(new Decilist(new HttpTransport(baseUrl)).predict("private evidence","Ready?"),RuntimeCompatibilityError);
    assert.deepEqual(paths,["/health"]);
  });
});
test("foreign service and non-JSON health are rejected", async()=>{
  for(const body of ["not JSON",JSON.stringify({service:"unrelated"})]) await serve((req,res)=>res.end(body),async baseUrl=>{
    await assert.rejects(Decilist.connect({baseUrl}),RuntimeCompatibilityError);
  });
});
test("unavailable runtime has an actionable startup message",async()=>{
  let baseUrl;
  await serve((req,res)=>res.end(),async url=>{baseUrl=url;});
  await assert.rejects(Decilist.connect({baseUrl,waitMs:0}),error=>error instanceof RuntimeConnectionError && /runtime start/.test(error.message));
});
