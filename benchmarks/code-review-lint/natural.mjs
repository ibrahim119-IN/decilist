// The same recommended JS rules are applied to both historical variants.
import { ESLint } from "eslint";
import js from "@eslint/js";
import { readFile, writeFile } from "node:fs/promises";
import { resolve, join } from "node:path";
import { performance } from "node:perf_hooks";

const root=resolve(process.argv[2]);
const catalog=JSON.parse(await readFile(new URL('../code_review_natural_catalog.json',import.meta.url),'utf8')).cases;
const globals=Object.fromEntries(['module','require','exports','__dirname','__filename','process','Buffer','console','setTimeout','clearTimeout','setInterval','clearInterval','setImmediate','clearImmediate','queueMicrotask','URL','URLSearchParams','fetch','AbortController','AbortSignal','performance','global','globalThis','FormData','Blob','File','Request','Response','Headers','XMLHttpRequest','navigator','window','document','btoa','atob'].map(name=>[name,'readonly']));
const rows=[];
for(const item of catalog)for(const variant of ['broken','fixed']){
  const folder=join(root,item.id,variant);
  const pkg=JSON.parse(await readFile(join(folder,'package.json'),'utf8'));
  const linter=new ESLint({cwd:folder,overrideConfigFile:true,ignore:false,allowInlineConfig:false,
    overrideConfig:[{files:['**/*.js'],languageOptions:{ecmaVersion:'latest',sourceType:pkg.type==='module'?'module':'commonjs',globals},rules:js.configs.recommended.rules}]});
  const started=performance.now();
  const results=await linter.lintFiles(item.source_files.map(file=>join(folder,file.path)));
  if(results.some(result=>result.fatalErrorCount||result.messages.some(message=>!message.ruleId)))throw new Error(`Invalid lint run ${item.id}/${variant}: ${JSON.stringify(results)}`);
  rows.push({id:item.id,variant,latency_ms:performance.now()-started,
    diagnostics:results.flatMap(result=>result.messages.map(({ruleId,message,line,endLine,column,severity})=>({path:result.filePath.slice(folder.length+1).replaceAll('\\','/'),ruleId,message,line,endLine,column,severity}))) });
}
await writeFile(join(root,'lint.json'),JSON.stringify({profile:'ESLint 10.13.0 / @eslint/js 10.0.1 recommended JS; module type from original package; declared Node/browser globals; inline config disabled',rows},null,2)+'\n');
console.log(JSON.stringify(rows.map(row=>({id:row.id,variant:row.variant,diagnostics:row.diagnostics.length}))));
