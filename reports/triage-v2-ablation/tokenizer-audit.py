"""Read-only prompt/tokenizer audit; imports no Torch/model and makes no requests."""
import ast
import hashlib
import json
from pathlib import Path
import sys
import types

from tokenizers import Tokenizer

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / "outputs/decilist"
SNAPSHOT = ROOT / "work/model-cache/hub/models--LiquidAI--d1-3B/snapshots/051bcc464b01b9f92942b364d9586b0ef5912432"
PROMPT = SNAPSHOT / "prompt.py"
data = PROMPT.read_bytes()
git_blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
assert git_blob == "6c44c47b600f1b41015a273cb9e03c3e18ef918a"

# Execute only the reviewed pure prompt/rendering module, without import bytecode
# writes in the vendor snapshot or importing its Torch-bearing model modules.
module = types.ModuleType("triage_audit_vendor_prompt")
sys.modules[module.__name__] = module
exec(compile(data, str(PROMPT), "exec"), module.__dict__)
native = Tokenizer.from_file(str(SNAPSHOT / "tokenizer.json"))

class LocalTokenizer:
    def encode(self, text, add_special_tokens=False):
        return native.encode(text, add_special_tokens=add_special_tokens).ids

tokenizer = LocalTokenizer()
tree = ast.parse((REPO / "packages/python/src/decilist/triage.py").read_text())
constants = {}
for statement in tree.body:
    if isinstance(statement, ast.Assign):
        for target in statement.targets:
            if isinstance(target, ast.Name) and target.id in {"ROUTES", "TRIAGE_INSTRUCTIONS"}:
                constants[target.id] = ast.literal_eval(statement.value)
question = module.Choice(constants["TRIAGE_INSTRUCTIONS"], constants["ROUTES"])
codes = module.aliases(tokenizer, list(question.criteria))
groups = module.readout_ids(tokenizer, question)
question_text = module.question_block(tokenizer, question)
report = json.loads((REPO / "reports/triage-machine-2026-10-26.json").read_text())
rows = []
for row in report["runs"]["liquid"]["results"]:
    if row["repetition"] != 1:
        continue
    evidence = row["evidence"]
    state = json.dumps(evidence, ensure_ascii=False, allow_nan=False)
    prompt = module.render(tokenizer, state, question, bos="<|startoftext|>")
    decision = row.get("output", {}).get("decision") or {}
    signals = evidence.get("signals", [])
    rows.append({"case": row["case_id"], "role": row["role"], "status": row["status"],
                 "state_chars": len(state), "prompt_tokens": len(tokenizer.encode(prompt)),
                 "state_tokens": len(tokenizer.encode(state)),
                 "log_chars": len(evidence["log"]), "signal_count": len(signals),
                 "signal_chars": sum(len(signal.get("text", "")) for signal in signals),
                 "signals_repeating_log_lines": sum(signal.get("text", "") in evidence["log"].splitlines() for signal in signals),
                 "source_references": len(evidence.get("references", [])),
                 "evidence_truncated": evidence["truncated"],
                 "retained_probability_count": len(decision.get("probabilities", {})),
                 "attempt_outcomes": [attempt["outcome"] for attempt in decision.get("attempts", [])]})
result = {"scope": "Offline tokenizer/pure-renderer audit only; no model inference, network, GPU, server calls, or vendor edits",
          "vendor_revision": SNAPSHOT.name, "verified_prompt_git_blob_sha1": git_blob,
          "criteria": [{"route": route, "code": code, "code_token_id": token_id, "readout_token_ids": groups[index]}
                       for index, (route, (code, token_id)) in enumerate(zip(question.criteria, codes))],
          "question_block": question_text, "question_tokens": len(tokenizer.encode(question_text)),
          "cases": rows}
print(json.dumps(result, ensure_ascii=False, indent=2))
