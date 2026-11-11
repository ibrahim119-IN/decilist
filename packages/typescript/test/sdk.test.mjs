import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Decilist, validateDecision, HttpTransport, questionSchema, applyCosts, expectedCosts, applyCalibration, calibrateDecision } from "../dist/index.js";
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
const pickOptions = {a:"A", b:"B", c:"C"};
const twice = {temperature:2, kind:"choose", labels:["a","b","c"], model:"test"};
function model(probabilities, seen = [], name = "test") {
  const confidence = Math.max(...Object.values(probabilities));
  const value = Object.keys(probabilities).find(key => probabilities[key] === confidence);
  return new Decilist({async decide(question, policy) { seen.push(policy); return {schema_version:"1",request_id:"t",kind:"choose",status:"accepted",value,probabilities,confidence,provider:"p",model:name,latency_ms:1,cost_usd:null,attempts:[{provider:"p",outcome:"accepted",latency_ms:1}]}; }});
}
test("calibration matches the Python SDK", () => {
  // Same figures as packages/python/tests/test_calibration.py.
  near(applyCalibration({a:.9, b:.1, c:0}, twice), {a:.75, b:.25, c:0});
  near(applyCalibration({a:.9, b:.05, c:.05}, twice), {a:0.6796227589829592, b:0.16018862050852037, c:0.16018862050852037});
  near(applyCalibration({true:.8, false:.2}, {temperature:.5, kind:"predict", labels:["true","false"]}), {true:.64/.68, false:.04/.68});
  assert.deepEqual(applyCalibration({a:.9, b:.05, c:.05}, {...twice, temperature:1}), {a:.9, b:.05, c:.05});
  for (const bad of [{...twice, temperature:0}, {...twice, temperature:NaN}, {...twice, kind:"score"}, {...twice, labels:["a","b"]}, {...twice, labels:["a","b","x"]}, {...twice, min_confidence:2}, null])
    assert.throws(() => applyCalibration({a:.9, b:.05, c:.05}, bad));
});
test("calibrated choice applies the threshold on the client after correcting", async () => {
  const seen = [];
  const sdk = model({a:.9, b:.05, c:.05}, seen);
  const accepted = await sdk.chooseCalibrated("state", "Choose", pickOptions, {calibration:twice, policy:{min_confidence:.6}});
  assert.equal(accepted.status, "accepted"); assert.equal(accepted.value, "a");
  assert.ok(Math.abs(accepted.confidence - 0.6796227589829592) < 1e-9);
  const refused = await sdk.chooseCalibrated("state", "Choose", pickOptions, {calibration:twice, policy:{min_confidence:.8}});
  assert.equal(refused.status, "abstained"); assert.deepEqual(refused.probabilities, {}); assert.equal(refused.attempts[0].outcome, "low_confidence");
  // No policy here: the default 0.7 applies, or the calibration's own threshold when it has one.
  assert.equal((await sdk.chooseCalibrated("state", "Choose", pickOptions, {calibration:twice})).status, "abstained");
  assert.equal((await sdk.chooseCalibrated("state", "Choose", pickOptions, {calibration:{...twice, min_confidence:.5}})).status, "accepted");
  assert.equal((await sdk.chooseCalibrated("state", "Choose", pickOptions, {calibration:{...twice, min_confidence:.5}, policy:{min_confidence:.9}})).status, "abstained");
  assert.ok(seen.every(policy => policy.min_confidence === 0));
  const other = await model({a:.9, b:.05, c:.05}, [], "another").chooseCalibrated("state", "Choose", pickOptions, {calibration:twice, policy:{min_confidence:.8}});
  assert.equal(other.status, "accepted"); assert.equal(other.confidence, .9);
  await assert.rejects(sdk.chooseCalibrated("state", "Choose", {a:"A", b:"B"}, {calibration:twice}), /different answers/);
  await assert.rejects(sdk.predictCalibrated("state", "Ready?", {calibration:twice}), /choose question, not predict/);
  const yes = new Decilist({async decide() { return {schema_version:"1",request_id:"t",kind:"predict",status:"accepted",value:true,probabilities:{false:.2,true:.8},confidence:.8,provider:"p",model:"test",latency_ms:1,cost_usd:null,attempts:[]}; }});
  const sure = await yes.predictCalibrated("state", "Ready?", {calibration:{temperature:.5, kind:"predict", labels:["false","true"]}, policy:{min_confidence:.9}});
  assert.equal(sure.value, true); assert.ok(sure.confidence > .94);
});
test("calibration changes what the cost rule picks", async () => {
  const costs = {wrong:{a:{b:100}, b:{a:50, c:50}, c:{a:50, b:50}}, abstain:2};
  const sdk = model({a:.98, b:.01, c:.01});
  const raw = await sdk.chooseCosted("state", "Choose", pickOptions, {costs});
  assert.equal(raw.value, "a"); assert.ok(Math.abs(raw.expectedCost - 1.01) < 1e-9);
  const corrected = await sdk.chooseCosted("state", "Choose", pickOptions, {costs, calibration:{...twice, temperature:3}});
  assert.equal(corrected.status, "abstained"); assert.ok(corrected.decision.probabilities.a < .7); assert.ok(corrected.expectedCosts.a > 2);
  const calls = [];
  const verified = await sdk.chooseVerified("state", "Choose", pickOptions, {costs, calibration:{...twice, temperature:3}, verify: label => {calls.push(label); return true;}, fallback: () => "person"});
  assert.deepEqual(calls, []); assert.equal(verified.status, "fallback");
  const plain = await sdk.chooseVerified("state", "Choose", pickOptions, {calibration:twice, policy:{min_confidence:.99}, verify: () => true, fallback: () => "person"});
  assert.equal(plain.status, "fallback"); assert.equal(plain.decision.status, "abstained");
});
test("calibrateDecision leaves abstentions alone and rejects scores", () => {
  const abstained = {schema_version:"1",request_id:"t",kind:"choose",status:"abstained",value:null,probabilities:{},confidence:null,provider:null,model:null,latency_ms:1,cost_usd:null,attempts:[]};
  assert.equal(calibrateDecision(abstained, twice), abstained);
  const score = {schema_version:"1",request_id:"t",kind:"score",status:"accepted",value:.5,probabilities:{"0":.5,"1":.5},confidence:.5,provider:"p",model:"test",latency_ms:1,cost_usd:null,attempts:[]};
  assert.throws(() => calibrateDecision(score, twice), /not score/);
});
