"""Send actual Python engine envelopes through the built TypeScript validator."""
import asyncio
import json
from pathlib import Path
import subprocess

from decilist import Decilist, FunctionProvider, Policy
from decilist.contracts import question_adapter

ROOT = Path(__file__).resolve().parents[1]


async def main():
    envelopes = []
    cases = json.loads((ROOT / "contracts/cases.json").read_text(encoding="utf-8"))
    for case in cases:
        provider = FunctionProvider(lambda question, probabilities=case["probabilities"]: probabilities)
        decision = await Decilist([provider], Policy(min_confidence=0)).decide(question_adapter.validate_python(case["question"]))
        assert decision.status == "accepted"
        envelopes.append({"question": case["question"], "decision": decision.model_dump()})
    javascript = """
import {pathToFileURL} from 'node:url';
const {validateDecision}=await import(pathToFileURL(process.argv[1]));
let input=''; for await (const chunk of process.stdin) input+=chunk;
const cases=JSON.parse(input);
for (const item of cases) validateDecision(item.question,item.decision);
console.log(JSON.stringify({pythonEnvelopesAcceptedByTypeScript:cases.length}));
"""
    result = subprocess.run(["node", "--input-type=module", "-e", javascript, str(ROOT / "packages/typescript/dist/index.js")],
                            input=json.dumps(envelopes), capture_output=True, encoding="utf-8", timeout=30, check=True)
    summary = json.loads(result.stdout)
    assert summary["pythonEnvelopesAcceptedByTypeScript"] == len(cases)
    print(json.dumps(summary))


if __name__ == "__main__":
    asyncio.run(main())
