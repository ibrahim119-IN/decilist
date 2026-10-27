// Conventional recommended type-aware linting on the frozen authored pilot.
import { ESLint } from "eslint";
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import { readFile, writeFile, mkdtemp } from "node:fs/promises";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { performance } from "node:perf_hooks";

const raw = await readFile(new URL("../code_review_cases.json", import.meta.url));
const { cases } = JSON.parse(raw);
const root = await mkdtemp(join(tmpdir(), "decilist-review-lint-"));
for (const item of cases) await writeFile(join(root, `${item.id}.ts`), item.code);
const config = join(root, "tsconfig.json");
await writeFile(config, JSON.stringify({ compilerOptions:{strict:true,noEmit:true,skipLibCheck:true,target:"ES2022",module:"ESNext",types:[]}, include:["*.ts"] }));
const started = performance.now();
const linter = new ESLint({cwd:root, overrideConfigFile:true, overrideConfig:[
  js.configs.recommended, ...tseslint.configs.recommendedTypeChecked,
  {files:["**/*.ts"], languageOptions:{parserOptions:{project:[config],tsconfigRootDir:root}}},
]});
const rows = [];
for (const item of cases) {
  const began = performance.now();
  const [result] = await linter.lintFiles([join(root, `${item.id}.ts`)]);
  if (result.fatalErrorCount || result.messages.some(message => !message.ruleId)) throw new Error(`Lint infrastructure failure: ${JSON.stringify(result.messages)}`);
  rows.push({id:item.id, expected_target_flaw:item.expected, target_rule:item.target_rule,
    diagnostic_count:result.messages.length, latency_ms:performance.now()-began,
    diagnostics:result.messages.map(({ruleId,message,line,column})=>({ruleId,message,line,column}))});
}
const require = createRequire(import.meta.url);
const versions = Object.fromEntries(["eslint", "@eslint/js", "typescript-eslint", "typescript"].map(name=>[name,require(`${name}/package.json`).version]));
const report = {purpose:"Recommended type-aware ESLint diagnostics on authored cases. A diagnostic is not automatically a correct target-rule classification; raw messages are retained.",
  configuration:"eslint recommended + typescript-eslint recommendedTypeChecked; no custom rules or disabled rules",
  cases_sha256:createHash("sha256").update(raw).digest("hex"), versions, total_ms:performance.now()-started,rows};
const output = process.argv[2] ? resolve(process.argv[2]) : new URL("../../reports/code-review-eslint-pilot-01.json", import.meta.url);
await writeFile(output, JSON.stringify(report,null,2)+"\n");
console.log(JSON.stringify({cases:rows.length,flawed_with_diagnostics:rows.filter(r=>r.expected_target_flaw&&r.diagnostic_count).length,
  valid_with_diagnostics:rows.filter(r=>!r.expected_target_flaw&&r.diagnostic_count).length,versions}));
