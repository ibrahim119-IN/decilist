import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { Decilist, reviewRequestSchema, validateReviewResult } from "../dist/index.js";

const code = "const record: any = read();";
const options = {task:"Read and validate a record."};
const request = reviewRequestSchema.parse({code,...options});
const result = {schema_version:"code-review.1",status:"flagged",advisory:true,model:"specialist",revision:"pinned",
  rules:request.policy.rules,threshold:.9,total_lines:1,reviewed_lines:1,
  windows:[{start_line:1,end_line:1,probabilities:{error_handling_problem:.01,type_safety_hole:.99,unsafe_input:.01}}],
  findings:[{rule:"type_safety_hole",probability:.99,start_line:1,end_line:1}],latency_ms:10,error:null};

test("review returns typed advice through an injected transport",async()=>{
  const sdk = new Decilist({decide:async()=>{},review:async body=>{assert.deepEqual(body,request);return result;}});
  assert.equal((await sdk.review(code,options)).status,"flagged");
});
test("review requires a supporting transport and valid inputs",async()=>{
  await assert.rejects(new Decilist({decide:async()=>{}}).review(code,options),/does not support/);
  for (const change of [{language:"Python"},{code:" "},{code:"x\0y"},{task:""},{policy:{rules:["unsafe_input","unsafe_input"]}}]) {
    assert.throws(()=>reviewRequestSchema.parse({code,...options,...change}));
  }
});
test("invalid responses cannot become clean reviews",()=>{
  for (const change of [{status:"no_flags"},{findings:[]},{reviewed_lines:0},{windows:[]},{error:"timeout"},{threshold:.5},{total_lines:2}]) {
    assert.throws(()=>validateReviewResult(request,{...result,...change}));
  }
});
test("HTTP review works without a Playground and preserves unavailability",async()=>{
  let unavailable=false;
  const server=createServer(async(req,res)=>{
    res.setHeader("Content-Type","application/json");
    if(req.url==="/health") return res.end(JSON.stringify({service:"decilist",schema_version:"1",runtime_version:"test",ready:true,status:"ok"}));
    assert.equal(req.url,"/v1/code-review");
    let body=""; for await (const chunk of req) body+=chunk;
    assert.deepEqual(JSON.parse(body),request);
    if(unavailable){res.statusCode=503;return res.end('{}');}
    res.end(JSON.stringify(result));
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  try {
    const sdk=await Decilist.connect({baseUrl:`http://127.0.0.1:${server.address().port}`});
    assert.equal((await sdk.review(code,options)).findings[0].rule,"type_safety_hole");
    unavailable=true;
    await assert.rejects(sdk.review(code,options),/503/);
  } finally {server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
});
