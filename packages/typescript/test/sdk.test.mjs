import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Decilist, validateDecision, HttpTransport, questionSchema, applyCosts, expectedCosts } from "../dist/index.js";
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

const triageOptions = {ignore:"Noise", fix:"Run the fix", page:"Call a person"};
const triageCosts = {wrong:{ignore:{page:100, fix:5}, fix:{page:40}, page:{ignore:3, fix:3}}, abstain:2};
function fixed(kind, probabilities, seen = []) {
  const confidence = Math.max(...Object.values(probabilities));
  const value = kind === "predict" ? probabilities.true >= .5 : Object.keys(probabilities).find(key => probabilities[key] === confidence);
  return new Decilist({async decide(question, policy) { seen.push(policy); return {schema_version:"1",request_id:"test",kind,status:"accepted",value,probabilities,confidence,provider:"test",model:"test",latency_ms:1,cost_usd:null,attempts:[]}; }});
}
function near(actual, expected) { for (const key of Object.keys(expected)) assert.ok(Math.abs(actual[key] - expected[key]) < 1e-9, `${key}: ${actual[key]}`); assert.deepEqual(Object.keys(actual), Object.keys(expected)); }
test("cost-aware choice matches the documented example and the Python SDK", async () => {
  const seen = [];
  const abstained = await fixed("choose", {ignore:.75, fix:.05, page:.20}, seen).chooseCosted("alert", "Triage", triageOptions, {costs:triageCosts, policy:{min_confidence:.99}});
  assert.equal(abstained.status, "abstained"); assert.equal(abstained.value, null); assert.equal(abstained.expectedCost, null);
  near(abstained.expectedCosts, {ignore:20.25, fix:8.75, page:2.4});
  assert.equal(abstained.decision.value, "ignore"); assert.equal(abstained.abstainCost, 2);
  assert.equal(seen[0].min_confidence, 0);
  const paged = await fixed("choose", {ignore:.5, fix:.05, page:.45}).chooseCosted("alert", "Triage", triageOptions, {costs:triageCosts});
  assert.equal(paged.status, "accepted"); assert.equal(paged.value, "page"); assert.equal(paged.decision.value, "ignore");
  assert.ok(Math.abs(paged.expectedCost - 1.65) < 1e-9);
  const sure = await fixed("choose", {ignore:.99, fix:.007, page:.003}).chooseCosted("alert", "Triage", triageOptions, {costs:triageCosts});
  assert.equal(sure.value, "ignore");
});
test("uniform costs equal the confidence threshold, and null never abstains", () => {
  near(expectedCosts({a:.71, b:.29}, {abstain:.3}), {a:.29, b:.71});
  const decision = p => ({schema_version:"1",request_id:"t",kind:"choose",status:"accepted",value:"a",probabilities:p,confidence:p.a,provider:"t",model:"t",latency_ms:1,cost_usd:null,attempts:[]});
  assert.equal(applyCosts(decision({a:.71, b:.29}), {abstain:.3}).value, "a");
  assert.equal(applyCosts(decision({a:.69, b:.31}), {abstain:.3}).status, "abstained");
  assert.equal(applyCosts(decision({a:.4, b:.35, c:.25}), {abstain:null}).value, "a");
});
test("cost-aware yes or no uses true and false labels", async () => {
  const result = await fixed("predict", {false:.9, true:.1}).predictCosted("payment", "Is this fraud?", {costs:{wrong:{false:{true:50}}, abstain:10}});
  assert.equal(result.value, true); assert.equal(result.decision.value, false);
  near(result.expectedCosts, {false:5, true:.9});
});
test("costs are validated before the model is asked", async () => {
  let asked = 0;
  const sdk = new Decilist({async decide() { asked++; throw new Error("not reached"); }});
  for (const costs of [{wrong:{ignore:{pgae:100}}, abstain:1}, {wrong:{ignore:{page:-1}}, abstain:1}, {wrong:{ignore:{page:NaN}}, abstain:1}, {abstain:-1}, {abstain:1, default:Infinity}, {wrong:{ignore:1}, abstain:1}, {}, {abstain:1, typo:2}, null])
    await assert.rejects(sdk.chooseCosted("alert", "Triage", triageOptions, {costs}));
  await assert.rejects(sdk.predictCosted("payment", "Fraud?", {costs:{wrong:{yes:{no:1}}, abstain:1}}), /yes/);
  await assert.rejects(sdk.chooseVerified("alert", "Triage", triageOptions, {verify:() => true, costs:{wrong:{ignore:{pgae:1}}, abstain:1}}), /pgae/);
  assert.equal(asked, 0);
  const score = {schema_version:"1",request_id:"t",kind:"score",status:"accepted",value:.5,probabilities:{"0":.5,"1":.5},confidence:.5,provider:"t",model:"t",latency_ms:1,cost_usd:null,attempts:[]};
  assert.throws(() => applyCosts(score, {abstain:1}), /not score/);
});
test("verified choices follow cost order and skip answers costlier than a person", async () => {
  const calls = [];
  const seen = [];
  const costs = {wrong:{ignore:{page:100}}, abstain:5};
  const sdk = fixed("choose", {ignore:.7, fix:.1, page:.2}, seen);
  const result = await sdk.chooseVerified("alert", "Triage", triageOptions, {costs, verify: label => {calls.push(label); return label === "fix";}, fallback: () => "person"});
  assert.deepEqual(calls, ["page", "fix"]);
  assert.equal(result.status, "verified"); assert.equal(result.value, "fix");
  assert.equal(result.costed.value, "page"); assert.equal(result.decision.value, "ignore"); assert.equal(seen[0].min_confidence, 0);
  calls.length = 0;
  const refused = await sdk.chooseVerified("alert", "Triage", triageOptions, {costs, verify: label => {calls.push(label); return false;}, fallback: () => "person"});
  assert.deepEqual(calls, ["page", "fix"]); assert.equal(refused.status, "fallback"); assert.equal(refused.result, "person");
  const plain = await sdk.chooseVerified("alert", "Triage", triageOptions, {verify: () => true, policy:{min_confidence:0}});
  assert.equal(plain.value, "ignore"); assert.equal("costed" in plain, false);
});
test("a model abstention stays an abstention under costs", async () => {
  const sdk = new Decilist({async decide() { return {schema_version:"1",request_id:"t",kind:"choose",status:"abstained",value:null,probabilities:{},confidence:null,provider:null,model:null,latency_ms:1,cost_usd:null,attempts:[{provider:"t",outcome:"error",latency_ms:1}]}; }});
  const result = await sdk.chooseCosted("alert", "Triage", triageOptions, {costs:triageCosts});
  assert.equal(result.status, "abstained"); assert.deepEqual(result.expectedCosts, {});
});
