"""Frozen paired local-model review experiment; never a human productivity study.

Preparation and oracle data stay outside participant packets. Use --preflight,
then --freeze, then --run, in that order. Output is append-only per trial.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import time
from datetime import datetime, timezone

import httpx

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
LIMIT = 180.0
MODEL = "qwen3-coder-30b-a3b-q4km"
SYSTEM = """You are reviewing existing JavaScript production source. Find a concrete
runtime correctness defect, if any, and propose the smallest repair. Do not change
behavior merely to silence lint. Preserve public contracts and ordinary successful
behavior. An advisory hint is fallible; no hint is not proof of correctness.
Return ONLY JSON: {"issue": boolean, "summary": "specific trigger and consequence",
"edits": [{"path": "supplied source path", "old": "exact original substring",
"new": "replacement substring"}]}. Source line numbers are display annotations,
not part of substrings. At most six edits and 120 changed lines in total. No tests,
configuration, imports, external requests, subprocesses, filesystem operations or
new dependencies. Use only supplied source files. If no concrete defect is found,
return issue false, a short summary, and edits []."""
TASKS = {
    "sindresorhus/p-limit": "Limit concurrency of asynchronous function calls, with iterable mapping and queue counters.",
    "sindresorhus/p-retry": "Retry asynchronous operations according to configured retry counts, error hooks and timing policies.",
    "axios/axios": "Process HTTP client configuration, request/response interceptors and errors while preserving public behavior.",
    "fastify/fastify": "Process web framework requests and errors while preserving response status and parser behavior.",
}
DENY = re.compile(r"\b(?:import|require|eval|Function|process|globalThis|global|fetch|XMLHttpRequest|WebSocket|__proto__|constructor|child_process)\b|(?:node:|https?://)")


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files(folder):
    return {p.relative_to(folder).as_posix(): sha(p) for p in sorted(folder.rglob("*")) if p.is_file()}


def probe(case_id, folder, timeout=15):
    began = time.perf_counter()
    command = ["node", "--require", str(HERE/"code_review_no_external.cjs"), str(HERE/"code_review_natural_probe.mjs"), case_id, str(folder)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=folder)
        return {"passed": result.returncode == 0, "returncode": result.returncode, "stdout": result.stdout[-6000:], "stderr": result.stderr[-6000:], "seconds": time.perf_counter()-began}
    except subprocess.TimeoutExpired:
        return {"passed": False, "timeout": True, "seconds": time.perf_counter()-began}


def packet(source, diagnostics, task, hints=None, feedback=None):
    # Explicit allowlist: no catalog object, revision labels, oracle, or test text.
    result = {"task": task, "files": [{"path": path, "numbered_source": "\n".join(f"{i}: {line}" for i, line in enumerate(code.splitlines(), 1))} for path, code in source.items()], "lint_diagnostics": diagnostics}
    if hints is not None:
        result["advisory_review"] = hints
    if feedback is not None:
        result["validation_feedback"] = feedback
    return result


def validate_edits(folder, allowed, proposal):
    if not isinstance(proposal, dict) or type(proposal.get("issue")) is not bool or not isinstance(proposal.get("summary"), str):
        raise ValueError("reply requires boolean issue and string summary")
    edits = proposal.get("edits")
    if not isinstance(edits, list) or len(edits) > 6 or (not proposal["issue"] and edits):
        raise ValueError("invalid edits")
    pending = {}
    changed = 0
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"path", "old", "new"} or not all(isinstance(x, str) for x in edit.values()):
            raise ValueError("invalid edit schema")
        relative, old, new = edit["path"], edit["old"], edit["new"]
        target = folder/relative
        if relative not in allowed or target.is_symlink() or not target.resolve().is_relative_to(folder.resolve()):
            raise ValueError("path is not a permitted source file")
        content = pending.get(relative, target.read_text(encoding="utf-8"))
        if not old or old == new or content.count(old) != 1:
            raise ValueError("old substring must match exactly once and change source")
        delta = list(difflib.ndiff(old.splitlines(), new.splitlines()))
        changed += sum(line[:2] in ("+ ", "- ") for line in delta)
        # An unchanged token on a modified line is not a newly introduced capability.
        for line in delta:
            if line.startswith("+ "):
                for match in DENY.finditer(line[2:]):
                    if match.group() not in old:
                        raise ValueError("new capability/import is outside this experiment")
        if changed > 120 or len(new) > 24000 or "\0" in new:
            raise ValueError("edit exceeds bounds")
        pending[relative] = content.replace(old, new, 1)
    return pending


def ask(client, payload, timeout, request_path=None):
    request = {"model": MODEL, "temperature": 0, "seed": 17843, "max_tokens": 3072, "cache_prompt": False,
               "response_format": {"type": "json_object"}, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]}
    if request_path is not None:
        dump(request_path, request)
    began = time.perf_counter()
    response = client.post("http://127.0.0.1:8792/v1/chat/completions", json=request, timeout=max(.1, timeout))
    response.raise_for_status()
    raw = response.json()
    return request, raw, time.perf_counter()-began


def preflight(root, catalog):
    results = []
    for case in catalog:
        for variant in ("broken", "fixed"):
            result = probe(case["id"], root/case["id"]/variant)
            results.append({"id": case["id"], "variant": variant, **result})
            print(json.dumps({"probe":case["id"], "variant":variant, "passed":result["passed"]}), flush=True)
    dump(root/"fixture-proof.json", results)
    if not all(row["passed"] == (row["variant"] == "fixed") for row in results):
        raise RuntimeError("fixture precondition failed; retained all evidence")
    with httpx.Client(trust_env=False) as client:
        profile = {path: client.get("http://127.0.0.1:8792/"+path).json() for path in ("health", "v1/models", "props")}
        request, raw, elapsed = ask(client, packet({"sample.js":"export function add(a, b) { return a + b; }"}, [], "Export a numeric addition function."), 60)
        json.loads(raw["choices"][0]["message"]["content"])
        warm = client.post("http://127.0.0.1:8766/v1/code-review", json={"code":"export const enabled = true;", "task":"Export a feature flag.", "language":"JavaScript"}, timeout=60)
        warm.raise_for_status()
        if warm.json()["status"] == "unavailable":
            raise RuntimeError("specialist unavailable")
    dump(root/"preflight.json", {"profile":profile, "request":request, "response":raw, "seconds":elapsed, "specialist_warmup":warm.json(), "runtime_hashes":read(root/"runtime-hashes.json")})


def frozen_files(root):
    paths = [HERE/"run_code_review_natural.py", HERE/"code_review_natural_probe.mjs", HERE/"code_review_no_external.cjs", HERE/"code_review_natural_catalog.json", HERE/"prepare_code_review_natural.py", HERE/"code-review-lint/natural.mjs", HERE/"code-review-lint/package-lock.json", REPO/"docs/code-review-natural-protocol.md"]
    paths += sorted((REPO/"packages/python/src/decilist").rglob("*.py"))
    result = {p.relative_to(REPO).as_posix():sha(p) for p in paths}
    result.update({"artifact/"+name:sha(root/name) for name in ("sources.json", "lint.json", "fixture-proof.json", "preflight.json")})
    return result


def freeze(root, catalog):
    if (root/"freeze.json").exists() or (root/"trials").exists():
        raise RuntimeError("freeze is write-once")
    prepared = read(root/"sources.json")["cases"]
    for case in prepared:
        for variant in ("broken", "fixed"):
            if files(root/case["id"]/variant) != case["variants"][variant]["files"]:
                raise RuntimeError("original source changed")
        if sha(root/case["id"]/"package-lock.json") != case["dependency_lock_sha256"]:
            raise RuntimeError("dependency lock changed")
    digest = read(root/"runtime-hashes.json")
    if digest != {"runtime":"049243ee4986362abecbe70b1a2ac1457d01bc4dc6178f3a3a46d9d199f57022", "model":"f6050b4da22869aa3fb9d94bb3314e9fc5a027d4741c1f97761c387d6b2b2335"}:
        raise RuntimeError("unexpected model/runtime")
    schedule = []
    for index, case in enumerate(catalog):
        order = ["ordinary", "assisted"] if index % 2 == 0 else ["assisted", "ordinary"]
        for variant in ("broken", "fixed"):
            for arm in (order if variant == "broken" else list(reversed(order))):
                schedule.append({"id":case["id"], "variant":variant, "arm":arm})
    dump(root/"freeze.json", {"utc":datetime.now(timezone.utc).isoformat(), "hashes":frozen_files(root), "schedule":schedule, "task_limit_seconds":LIMIT, "attempt_limit":3, "control_attempt_limit":1, "source_commit":"a11174b2448f9228594b37e59100367f73c675a5"})
    print("Frozen 32 assigned trials.", flush=True)


def trial(root, case, assignment, lint, client):
    name = "-".join(assignment[k] for k in ("id", "variant", "arm"))
    evidence = root/"trials"/name
    if evidence.exists():
        if (evidence/"result.json").exists():
            return read(evidence/"result.json")
        raise RuntimeError("interrupted trial retained; do not silently rerun")
    evidence.mkdir(parents=True)
    original = root/case["id"]/assignment["variant"]
    project = root/case["id"]/"trials"/name/"project"
    shutil.copytree(original, project)
    before = files(project)
    allowed = [item["path"] for item in case["source_files"]]
    source = {path:(project/path).read_text(encoding="utf-8") for path in allowed}
    task = TASKS[case["repository"]]
    row = {**assignment, "utc":datetime.now(timezone.utc).isoformat(), "attempts":[], "verified_repair":False, "hints":None, "issue":None}
    feedback = None
    began = time.perf_counter()
    try:
        if assignment["arm"] == "assisted":
            hints = []
            for path, code in source.items():
                hint_start = time.perf_counter()
                try:
                    response = client.post("http://127.0.0.1:8766/v1/code-review", json={"code":code,"task":task,"language":"JavaScript"}, timeout=min(35, LIMIT-(time.perf_counter()-began)))
                    response.raise_for_status()
                    hint = response.json()
                except (httpx.HTTPError, ValueError) as error:
                    hint = {"status":"unavailable", "error":type(error).__name__}
                hints.append({"path":path, "result":hint, "wall_seconds":time.perf_counter()-hint_start})
            row["hints"] = hints
        for attempt in range(1, (3 if assignment["variant"] == "broken" else 1)+1):
            remaining = LIMIT-(time.perf_counter()-began)
            if remaining <= 1:
                break
            payload = packet(source, lint, task, row["hints"], feedback)
            dump(evidence/f"{attempt}-packet.json", payload)
            request, raw, model_seconds = ask(client, payload, remaining, evidence/f"{attempt}-request.json")
            dump(evidence/f"{attempt}-response.json", raw)
            step = {"attempt":attempt, "model_seconds":model_seconds, "finish_reason":raw["choices"][0].get("finish_reason")}
            row["attempts"].append(step)
            try:
                proposal = json.loads(raw["choices"][0]["message"]["content"])
                pending = validate_edits(project, allowed, proposal)
                step["proposal"] = proposal
                row["issue"] = proposal["issue"]
                if not proposal["issue"] or not pending:
                    break
                for path, content in pending.items():
                    (project/path).write_text(content, encoding="utf-8", newline="")
                after = files(project)
                if any(before.get(path) != after.get(path) for path in set(before)|set(after) if path not in allowed):
                    raise RuntimeError("protected source changed")
                remaining = LIMIT-(time.perf_counter()-began)
                if remaining <= 0:
                    break
                verification = probe(case["id"], project, min(15, remaining))
                step["verification"] = verification
                if verification["passed"]:
                    row["verified_repair"] = assignment["variant"] == "broken"
                    row["control_regression_passed"] = assignment["variant"] == "fixed"
                    break
                # Real bounded failing test output, never the expected patch.
                feedback = {"message":"Protected behavioral validation failed.", "output":verification.get("stderr", "timeout")[-4000:]}
            except (ValueError, KeyError, TypeError) as error:
                step["invalid_proposal"] = str(error)
                feedback = {"message":"Proposal could not be applied.", "output":str(error)}
            source = {path:(project/path).read_text(encoding="utf-8") for path in allowed}
    except (httpx.HTTPError, ValueError) as error:
        row["error"] = type(error).__name__+": "+str(error)[:500]
    row["elapsed_seconds"] = time.perf_counter()-began
    if row["elapsed_seconds"] > LIMIT:
        row["verified_repair"] = False
    row["capped_task_seconds"] = row["elapsed_seconds"] if row["verified_repair"] else LIMIT
    row["source_hashes_before"] = before
    row["source_hashes_after"] = files(project)
    row["diff"] = "".join("".join(difflib.unified_diff((original/path).read_text(encoding="utf-8").splitlines(True), (project/path).read_text(encoding="utf-8").splitlines(True), fromfile=path, tofile=path)) for path in allowed)
    dump(evidence/"result.json", row)
    print(json.dumps({k:row[k] for k in ("id","variant","arm","verified_repair","issue","elapsed_seconds")}), flush=True)
    return row


def run(root, catalog):
    frozen = read(root/"freeze.json")
    if frozen["hashes"] != frozen_files(root):
        raise RuntimeError("frozen code or evidence changed; this is not the original trial")
    lint = {(x["id"],x["variant"]):x["diagnostics"] for x in read(root/"lint.json")["rows"]}
    by_id = {x["id"]:x for x in catalog}
    rows = []
    with httpx.Client(trust_env=False) as client:
        for assignment in frozen["schedule"]:
            rows.append(trial(root, by_id[assignment["id"]], assignment, lint[assignment["id"],assignment["variant"]], client))
    summary = {}
    for arm in ("ordinary", "assisted"):
        broken = [x for x in rows if x["arm"]==arm and x["variant"]=="broken"]
        summary[arm] = {"verified_repairs":sum(x["verified_repair"] for x in broken), "cases":len(broken), "capped_median_seconds":statistics.median(x["capped_task_seconds"] for x in broken), "actual_total_seconds":sum(x["elapsed_seconds"] for x in broken)}
    summary["human_time_measured"] = False
    dump(root/"results.json", {"freeze_sha256":sha(root/"freeze.json"), "summary":summary, "rows":rows})
    print(json.dumps(summary), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    for name in ("preflight", "freeze", "run"):
        action.add_argument("--"+name,action="store_true")
    args = parser.parse_args()
    catalog = read(HERE/"code_review_natural_catalog.json")["cases"]
    if args.preflight: preflight(args.root,catalog)
    if args.freeze: freeze(args.root,catalog)
    if args.run: run(args.root,catalog)


if __name__ == "__main__":
    main()
