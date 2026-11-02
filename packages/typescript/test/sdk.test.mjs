import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Decilist, validateDecision, HttpTransport, questionSchema } from "../dist/index.js";
const cases = JSON.parse(await readFile(new URL("../../../contracts/cases.json", import.meta.url)));
function envelope(c) { return {schema_version:"1",request_id:"test",kind:c.question.kind,status:"accepted",value:c.value,probabilities:c.probabilities,confidence:c.confidence,provider:"test",model:"test",latency_ms:1,cost_usd:null,attempts:[{provider:"test",outcome:"accepted",latency_ms:1}]}; }
for(const [index,c] of cases.entries()) test(`shared contract ${index}`,async()=>{
  const sdk = new Decilist({async decide(question,policy){assert.equal(policy.local_only,true);assert.deepEqual(question,c.question);return envelope(c);}});
  const result = await sdk.decide(c.question);
  assert.equal(result.value,c.value);
  assert.deepEqual(result.probabilities,c.probabilities);
});
test("special own labels retain trimming and safe ordinary data properties",()=>{
  const options=JSON.parse('{"__proto__":"  Special label  ","constructor":"  Constructor label  "}');
  const parsed=questionSchema.parse({kind:"choose",state:"  Evidence  ",instructions:" Choose ",options});
  assert.equal(parsed.state,"Evidence");
  assert.equal(parsed.instructions,"Choose");
  assert.deepEqual(Object.keys(parsed.options),["__proto__","constructor"]);
  assert.equal(parsed.options.__proto__,"Special label");
  assert.equal(parsed.options.constructor,"Constructor label");
  assert.equal(Object.getPrototypeOf(parsed.options),Object.prototype);
  const nullOptions=Object.assign(Object.create(null),options);
  assert.deepEqual(questionSchema.parse({...parsed,options:nullOptions}).options,parsed.options);
  assert.equal(Object.getPrototypeOf({}),Object.prototype);
});
test("special own keys cannot hide invalid values or unexpected probability labels",()=>{
  const c=cases[0];
  const probabilities=Object.fromEntries([...Object.entries(c.probabilities),["__proto__",0]]);
  assert.throws(()=>validateDecision(c.question,{...envelope(c),probabilities}));
  const special=cases.find(item=>item.value==="__proto__");
  for(const invalid of [NaN,-1,2,true,{},"0.8"]) {
    const malformed=Object.fromEntries(Object.entries(special.probabilities).map(([key,value])=>[key,key==="__proto__"?invalid:value]));
    assert.throws(()=>validateDecision(special.question,{...envelope(special),probabilities:malformed}));
  }
  for(const invalid of ["  ",{},true]) {
    const options=Object.fromEntries([["__proto__",invalid],["other","Other"]]);
    assert.throws(()=>questionSchema.parse({...special.question,options}));
  }
  const options={alpha:"A",beta:"B",[Symbol("hidden")]:"Invalid label"};
  assert.throws(()=>questionSchema.parse({...c.question,options}));
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

test("structured states become wire strings for each convenience call", async () => {
  const seen = [];
  const sdk = new Decilist({async decide(question) {
    seen.push(question);
    return {schema_version:"1",request_id:"test",kind:question.kind,status:"abstained",value:null,probabilities:{},confidence:null,provider:null,model:null,latency_ms:1,cost_usd:null,attempts:[]};
  }});
  await sdk.choose({name:"\u0639\u0631\u0628\u064a", count:2}, "Choose", {a:"A",b:"B"});
  await sdk.predict([1, {ready:true}], "Ready?");
  await sdk.score("unchanged", "Rate", ["Low", "High"]);
  assert.deepEqual(seen.map(q => q.state), ['{"name":"\u0639\u0631\u0628\u064a","count":2}', '[1,{"ready":true}]', 'unchanged']);
  assert.throws(() => sdk.predict({toJSON() { return undefined; }}, "Ready?"), TypeError);
  assert.throws(() => sdk.predict({x: 1n}, "Ready?"), TypeError);
});

function rankedSdk(probabilities = {a:.2,b:.5,c:.3}, value = "b", status = "accepted") {
  let calls = 0;
  const sdk = new Decilist({async decide(question, policy) {
    calls++;
    return {schema_version:"1",request_id:"test",kind:"choose",status,value:status === "accepted" ? value : null,
      probabilities:status === "accepted" ? probabilities : {}, confidence:status === "accepted" ? Math.max(...Object.values(probabilities)) : null,
      provider:status === "accepted" ? "stub" : null,model:status === "accepted" ? "stub" : null,latency_ms:1,cost_usd:null,attempts:[]};
  }});
  return {sdk, calls: () => calls};
}
const rankedOptions = {a:"A",b:"B",c:"C"};
test("verified choice checks in order, retains results and respects the limit", async () => {
  const {sdk} = rankedSdk();
  const calls = [];
  const payload = {ok:true};
  const first = await sdk.chooseVerified("state", "Choose", rankedOptions, {verify: label => {calls.push(label); return payload;}});
  assert.equal(first.status,"verified"); assert.equal(first.result,payload); assert.deepEqual(calls,["b"]);
  calls.length = 0;
  const second = await sdk.chooseVerified("state", "Choose", rankedOptions, {verify: async label => {calls.push(label); return label === "c";}});
  assert.equal(second.value,"c"); assert.deepEqual(calls,["b","c"]); assert.deepEqual(second.tried.map(x => x.ok),[false,true]);
  calls.length = 0;
  const unresolved = await sdk.chooseVerified("state", "Choose", rankedOptions, {verify: label => {calls.push(label); return false;}, maxTries:2});
  assert.equal(unresolved.status,"unresolved"); assert.equal(unresolved.result,null); assert.deepEqual(calls,["b","c"]);
  assert.ok(unresolved.tried.every(x => x.latencyMs >= 0 && x.error === null));
});
test("ties begin with decision value even if probability keys arrive reordered", async () => {
  const {sdk} = rankedSdk({c:.5,b:.5,a:0}, "b");
  const calls = [];
  const result = await sdk.chooseVerified("state", "Choose", rankedOptions, {verify: label => {calls.push(label); return false;}});
  assert.deepEqual(calls,["b","c","a"]); assert.equal(calls[0], result.decision.value);
  assert.deepEqual(Object.keys(result.decision.probabilities),["c","b","a"]);
});
test("abstention, failures, error redaction and fallback", async () => {
  const abstained = rankedSdk(undefined, undefined, "abstained").sdk;
  let checked = 0;
  const result = await abstained.chooseVerified("state", "Choose", rankedOptions, {verify: () => {checked++;}, fallback: () => 0});
  assert.equal(result.status,"fallback"); assert.equal(result.result,0); assert.equal(checked,0); assert.deepEqual(result.tried,[]);
  const {sdk} = rankedSdk();
  const errors = await sdk.chooseVerified("state", "Choose", rankedOptions, {verify: label => {
    if (label === "b") throw new TypeError("private text");
    if (label === "c") throw "private text";
    return true;
  }});
  assert.equal(errors.value,"a"); assert.deepEqual(errors.tried.map(x => x.error),["TypeError","Error",null]);
  assert.ok(!JSON.stringify(errors).includes("private text"));
  const fallback = await sdk.chooseVerified("state", "Choose", rankedOptions, {verify: () => false, fallback: async () => false});
  assert.equal(fallback.status,"fallback"); assert.equal(fallback.result,false);
  await assert.rejects(sdk.chooseVerified("state", "Choose", rankedOptions, {verify: () => false, fallback: () => {throw new Error("fallback");}}), /fallback/);
});
test("invalid verification settings fail before transport", async () => {
  const {sdk, calls} = rankedSdk();
  for (const maxTries of [0,-1,1.5,NaN,true,null]) await assert.rejects(sdk.chooseVerified("state", "Choose", rankedOptions, {verify: () => true, maxTries}));
  await assert.rejects(sdk.chooseVerified("state", "Choose", rankedOptions, {verify:null}), TypeError);
  await assert.rejects(sdk.chooseVerified("state", "Choose", rankedOptions, {verify:() => true, fallback:null}), TypeError);
  assert.equal(calls(),0);
});
