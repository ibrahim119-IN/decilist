const $ = id => document.getElementById(id);
const presets = {ready:{complete:true,valid:true,transient:false,retries:0},retry:{complete:false,valid:true,transient:true,retries:1},review:{complete:false,valid:false,transient:false,retries:2}};
const instructions = {choose:$("instructions").value,predict:"Is the workflow ready to proceed?",score:"How ready is the workflow?"};
const options = {proceed:"Required inputs are complete and valid",retry:"A transient failure with retries remaining",review:"Incomplete, invalid, unknown, or exhausted retries"};
const levels = ["Needs review","Can retry","Ready"];
function clearResult(){ $("result").hidden=true; $("empty").hidden=false; $("error").hidden=true; }
$("decision-form").addEventListener("input",clearResult);
$("kind").addEventListener("change",clearResult);
document.querySelectorAll("[data-preset]").forEach(button=>button.addEventListener("click",()=>{$("state").value=JSON.stringify(presets[button.dataset.preset],null,2);clearResult();}));
$("kind").addEventListener("change",()=>{$("instructions").value=instructions[$("kind").value];document.querySelector(".choices").textContent=$("kind").value==="choose"?"proceed  /  retry  /  review":$("kind").value==="predict"?"false  /  true":levels.join("  /  ");});
$("threshold").addEventListener("input",()=>{$("threshold-value").textContent=Math.round(Number($("threshold").value)*100)+"%";});
async function loadHealth(){try{const r=await fetch("/health");if(!r.ok)throw Error();const data=await r.json();$("runtime").textContent="RUNTIME  /  "+data.providers.map(p=>p.model).join(" → ");document.querySelector(".note").hidden=!data.providers.some(p=>p.model==="rules-v1");}catch{$("runtime").textContent="Runtime unavailable. Start the local API and refresh.";$("run").disabled=true;}}
loadHealth();
$("decision-form").addEventListener("submit",async event=>{
  event.preventDefault();$("run").disabled=true;$("error").hidden=true;
  const kind=$("kind").value;
  const question={kind,state:$("state").value,instructions:$("instructions").value,...(kind==="choose"?{options}:kind==="score"?{levels}:{})};
  try{
    const response=await fetch("/v1/decisions",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({question,policy:{local_only:true,min_confidence:Number($("threshold").value),timeout_ms:30000}}),signal:AbortSignal.timeout(65000)});
    if(!response.ok)throw Error(`Request rejected (${response.status}). Check the evidence and instructions.`);
    const result=await response.json();$("empty").hidden=true;$("result").hidden=false;
    $("status").textContent=result.status.toUpperCase();$("value").textContent=result.value===null?"Abstained":typeof result.value==="number"?result.value.toFixed(2):String(result.value);
    $("latency").textContent=result.latency_ms.toFixed(2)+" ms";$("provider").textContent=result.provider?`${result.provider} · ${result.model}`:"No provider met the policy. Application action withheld.";
    $("probabilities").replaceChildren();
    for(const [key,p]of Object.entries(result.probabilities)){const row=document.createElement("div");row.className="prob";const label=document.createElement("div");label.className="prob-label";const name=document.createElement("span");name.textContent=kind==="score"?levels[Number(key)]:key;const value=document.createElement("span");value.textContent=(p*100).toFixed(1)+"%";label.append(name,value);const bar=document.createElement("div");bar.className="bar";const fill=document.createElement("span");fill.style.width=`${p*100}%`;bar.append(fill);row.append(label,bar);$("probabilities").append(row);}
    $("trace").replaceChildren();for(const attempt of result.attempts){const li=document.createElement("li");const name=document.createElement("div");name.textContent=attempt.provider;const outcome=document.createElement("span");outcome.textContent=attempt.outcome.replaceAll("_"," ");li.append(name,outcome);$("trace").append(li);}$("json").textContent=JSON.stringify(result,null,2);
  }catch(error){$("result").hidden=true;$("empty").hidden=true;$("error").textContent=error.message;$("error").hidden=false;}finally{$("run").disabled=false;}
});
