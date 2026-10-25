import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Decilist, validateDecision, HttpTransport } from "../dist/index.js";
const cases = JSON.parse(await readFile(new URL("../../../contracts/cases.json", import.meta.url)));
function envelope(c) { return {schema_version:"1",request_id:"test",kind:c.question.kind,status:"accepted",value:c.value,probabilities:c.probabilities,confidence:c.confidence,provider:"test",model:"test",latency_ms:1,cost_usd:null,attempts:[{provider:"test",outcome:"accepted",latency_ms:1}]}; }
for(const [index,c] of cases.entries()) test(`shared contract ${index}`,async()=>{
  const sdk = new Decilist({async decide(question,policy){assert.equal(policy.local_only,true);return envelope(c);}});
  const result = await sdk.decide(c.question);
  assert.equal(result.value,c.value);
});
test("invalid input is rejected before transport", async()=>{
  const sdk = new Decilist({decide(){assert.fail("must not call transport");}});
  await assert.rejects(sdk.choose("", "Choose", {one:"one"}));
  await assert.rejects(sdk.choose("state", "Choose", {" a ":"A", b:"B"}));
  await assert.rejects(sdk.predict("evidence", "Ready?", {min_confidence:NaN}));
});
test("rejects forged or malformed provider outputs",()=>{
  const c=cases[0];
  for(const change of [{value:"outside"},{kind:"score"},{probabilities:{alpha:.1,beta:.2}},{confidence:.99},{probabilities:{alpha:NaN,beta:1}},{status:"abstained",value:"alpha"},{provider:null}]) assert.throws(()=>validateDecision(c.question,{...envelope(c),...change}));
});
test("HTTP URLs reject embedded credentials",()=>{
  assert.throws(()=>new HttpTransport("https://user:password@example.com"));
  assert.throws(()=>new HttpTransport("file:///tmp/test"));
});
test("abstention retains null value",async()=>{
  const c=cases[0];const result=validateDecision(c.question,{...envelope(c),status:"abstained",value:null,confidence:null,probabilities:{},provider:null,model:null});
  assert.equal(result.value,null);
});
