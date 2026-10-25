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
