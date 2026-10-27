// Conventional strict TypeScript baseline on exactly the pilot source snippets.
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { readFile, writeFile, mkdtemp } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { performance } from "node:perf_hooks";

const casesPath = new URL("./code_review_cases.json", import.meta.url);
const data = JSON.parse(await readFile(casesPath,"utf8"));
const root = await mkdtemp(join(tmpdir(),"decilist-review-tsc-"));
const require=createRequire(import.meta.url);
const tsc=join(dirname(require.resolve("typescript/package.json")),"bin","tsc");
const version=JSON.parse(await readFile(require.resolve("typescript/package.json"),"utf8")).version;
const rows=[];
for (const item of data.cases) {
  const path=join(root,`${item.id}.ts`);
  await writeFile(path,item.code);
  const started=performance.now();
  const config=join(root,"tsconfig.json");
  await writeFile(config,JSON.stringify({compilerOptions:{strict:true,noEmit:true,skipLibCheck:true,target:"ES2022",module:"ESNext",types:[]},files:[path]}));
  const run=spawnSync(process.execPath,[tsc,"--project",config,"--pretty","false"],{encoding:"utf8",timeout:30000});
  if(run.error || ![0,1,2].includes(run.status)) throw new Error("TypeScript baseline failed to execute");
  const output=(run.stdout||"")+(run.stderr||"");
  const diagnostics=output.split(/\r?\n/).filter(line=>/error TS\d+:/.test(line));
  if(run.status!==0 && !diagnostics.length) throw new Error(`TypeScript failed without diagnostics: ${output}`);
  rows.push({id:item.id,expected_target_flaw:item.expected,target_rule:item.target_rule,diagnostic_count:diagnostics.length,
    diagnostics,latency_ms:performance.now()-started});
}
const result={purpose:"Strict TypeScript compiler diagnostics; these are not a complete code review or an ESLint comparison.",typescript:version,rows};
if(process.argv[2]) await writeFile(resolve(process.argv[2]),JSON.stringify(result,null,2)+"\n");
console.log(JSON.stringify({cases:rows.length,with_diagnostics:rows.filter(r=>r.diagnostic_count).length}));
