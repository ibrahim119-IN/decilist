"""V3 model/design matrix; source targets are inspection measures, not causal truth.

Candidate manifest schema (created by the primary after development selection):
``schema_version=1, suite='triage-v3-matrix', corpus_freeze_sha256,
sources_sha256, specifications={ranking,diagnostic}, runtime_config_sha256,
runtimes={liquid:{url,model,revision},qwen:{url,model,expected_build}},
protocol_metadata={liquid:{ready_identity,model_artifact_sha256},
qwen:{ready_identity,model_artifact_sha256,runtime_binary_sha256}}``.
Extra finite protocol metadata may retain measured protocol-smoke evidence.
``candidate_snapshot`` constructs this shape without contacting either runtime.
Ready identities must be the exact stable dictionaries returned by the respective
client/provider.ready calls. Artifact hashes are external setup evidence, not
cryptographic attestation by a loopback HTTP server.

Development requires development-proof.json. Evaluation/controls require the full
proof AND --candidate-freeze, verified before reading their rows or catalog labels.
The default four factorial cells plus Qwen-only generation all retain first-call
quality separately from later timing/stability repetitions. No trial case is run.
Private decoded request/response traces are retained under work/triage-v3-lab;
public JSON redacts absolute local paths while retaining their hashes and all errors.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
from statistics import median
from time import perf_counter
from uuid import uuid4

from decilist import Client, Decilist, LiquidLocalProvider
from decilist.llama_cpp import LlamaCppProvider
from decilist.triage import validate_local_client
from decilist.triage_evidence import prepare_evidence
from decilist import triage_diagnostic, triage_v3
from . import triage_v3_lab as lab

ROOT = Path(__file__).resolve().parents[1]
CELLS = ("first_grounded_action", "liquid_route", "liquid_action", "qwen_route", "qwen_action", "qwen_diagnostic")
SOURCE_FILES = (
    "docs/triage-v3-experiment.md", "scripts/setup_stronger_local.py",
    "benchmarks/run_triage_v3.py", "benchmarks/triage_v3_lab.py", "benchmarks/triage_v3_trial.py",
    "benchmarks/report_triage_v3_trials.py",
    "benchmarks/triage_agent_trial.py", "benchmarks/triage_study.py",
    *[f"packages/python/src/decilist/{name}.py" for name in (
        "triage_v3", "triage_diagnostic", "triage_actions", "triage_context", "triage_context_v3", "triage_inspect",
        "triage_evidence", "triage_assistant", "triage", "llama_cpp", "engine", "contracts",
        "client", "providers", "server", "runtime", "__init__")],
)
_PRIVATE_PATH = re.compile(r"[A-Za-z]:[\\/]|(?<![\w:])/(?:home|Users|tmp|opt|var|mnt)/")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(value) -> str:
    return triage_v3.fingerprint(value)


def _json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _hash_text(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _check_protocol_metadata(metadata: dict) -> None:
    for model in ("liquid", "qwen"):
        item = metadata.get(model, {})
        if not isinstance(item.get("ready_identity"), dict) or not item["ready_identity"]:
            raise ValueError("protocol_metadata requires actual ready_identity for " + model)
        fields = ("model_artifact_sha256", "runtime_binary_sha256") if model == "qwen" else ("model_artifact_sha256",)
        if any(not _hash_text(item.get(field)) for field in fields):
            raise ValueError("protocol_metadata requires measured artifact SHA-256 fields for " + model)
    json.dumps(metadata, allow_nan=False)


def candidate_snapshot(config_path: Path, *, protocol_metadata: dict,
                       liquid_url="http://127.0.0.1:8765", qwen_url="http://127.0.0.1:8792") -> dict:
    """Pure manifest builder. Caller saves it once after the development phase."""
    config = triage_v3.load_qwen_config(config_path)
    _check_protocol_metadata(protocol_metadata)
    return {"schema_version": 1, "suite": "triage-v3-matrix",
        "cells": list(CELLS),
        "corpus_freeze_sha256": sha(lab.FREEZE.read_bytes()),
        "sources_sha256": {name: sha((ROOT / name).read_bytes()) for name in SOURCE_FILES},
        "specifications": {"ranking": triage_v3.specification(), "diagnostic": triage_diagnostic.specification()},
        "runtime_config_sha256": sha(config_path.read_bytes()),
        "runtimes": {"liquid": {"url": liquid_url, "model": LiquidLocalProvider.model, "revision": LiquidLocalProvider.revision},
                     "qwen": {"url": qwen_url, "model": config["model"], "expected_build": config["expected_build"]}},
        "protocol_metadata": _public(protocol_metadata)}


def _source_hashes() -> dict:
    return {name: sha((ROOT / name).read_bytes()) for name in SOURCE_FILES}


def verify_candidate(path: Path | None, config_path: Path, *, liquid_url: str, qwen_url: str) -> dict:
    if path is None:
        raise ValueError("evaluation and controls require --candidate-freeze before any case is read")
    saved = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(saved, dict) or saved.get("schema_version") != 1 or saved.get("suite") != "triage-v3-matrix":
        raise ValueError("unsupported candidate manifest")
    current = candidate_snapshot(config_path, protocol_metadata=saved.get("protocol_metadata", {}),
                                 liquid_url=liquid_url, qwen_url=qwen_url)
    for key, value in current.items():
        if saved.get(key) != value:
            raise ValueError("frozen candidate drift: " + key)
    return saved


def _public(value):
    """Retain useful identities and content, with explicit path hashes on redaction."""
    if isinstance(value, list):
        return [_public(item) for item in value]
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            model_path = isinstance(item, str) and key in {"model_path", "expected_model_path"} and (
                item.startswith("/") or PureWindowsPath(item).is_absolute())
            if isinstance(item, str) and (_PRIVATE_PATH.search(item) or model_path):
                output[key] = prepare_evidence(item)["log"]
                output[key + "_original_sha256"] = sha(item.encode("utf-8"))
                if key in {"model_path", "expected_model_path"}:
                    output[key + "_basename"] = PureWindowsPath(item).name if "\\" in item else Path(item).name
            else:
                output[key] = _public(item)
        return output
    if isinstance(value, str) and _PRIVATE_PATH.search(value):
        return prepare_evidence(value)["log"]
    return value


def _ref_match(reference: dict, target: dict) -> bool:
    symbols = set(reference.get("symbols", []))
    if isinstance(reference.get("symbol"), str):
        symbols.add(reference["symbol"])
    return reference.get("path") == target["path"] and bool(symbols & set(target["symbols"]))


def refs_match(references: list[dict], targets: list[dict]) -> bool:
    return any(_ref_match(ref, target) for ref in references for target in targets)


def availability(plan: dict, targets: list[dict]) -> dict:
    sources = plan["inspected"]["facts"].get("source_references", [])
    refs = [{"path": row["path"], "symbols": [context.get("symbol") for context in row.get("implementation_context", [])]}
            for row in sources]
    matching = [action["id"] for action in plan["actions"] if refs_match(action.get("source_refs", []), targets)]
    return {"source_target_path_available": any(ref["path"] == target["path"] for ref in refs for target in targets),
            "source_target_symbol_available": refs_match(refs, targets),
            "action_target_available": bool(matching), "matching_action_ids": matching}


def score_output(report: dict, plan: dict, case: dict, *, cell: str) -> dict:
    targets = case.get("inspection_targets", [])
    measured = {"guarded": bool(report.get("guard_reason")), "error": report.get("error"),
                "model_called": report.get("decision") is not None or report.get("generated_text") is not None,
                **availability(plan, targets)}
    if cell == "qwen_diagnostic":
        measured.update(kind="diagnostic", grounded_hypothesis=report.get("status") == "grounded_hypothesis",
            diagnostic_reference_target_match=refs_match(report.get("diagnostic_references", []), targets),
            hypothesis_causal_correctness=None)
        return measured
    raw = report.get("raw_top_selection")
    accepted = report.get("status") == "suggested" and report.get("selection") not in (None, "manual_review")
    measured.update(kind=plan["design"], raw_top_selection=raw, raw_top_valid=raw in plan["options"] if raw is not None else False,
                    accepted=accepted, accepted_selection=report.get("selection") if accepted else None)
    if plan["design"] == "route":
        measured.update(raw_top_match=raw in case["acceptable_routes"] if raw is not None else False,
                        accepted_match=accepted and report.get("selection") in case["acceptable_routes"])
    else:
        choices = {action["id"]: action for action in plan["actions"]}
        raw_refs = choices.get(raw, {}).get("source_refs", [])
        selected_refs = choices.get(report.get("selection"), {}).get("source_refs", [])
        measured.update(raw_top_match=refs_match(raw_refs, targets),
                        accepted_match=accepted and refs_match(selected_refs, targets),
                        action_match_causal_correctness=None)
    return measured


def summarize(rows: list[dict]) -> dict:
    first = [row for row in rows if row["repetition"] == 1]
    quality = [row for row in first if row["role"] == "regression"]
    result = {"unique_regressions": len(quality), "observations": len(rows),
        "measurement_errors": sum(row.get("measurement_error") is not None or row["score"].get("error") is not None for row in rows),
        "guarded_regressions": sum(row["score"]["guarded"] for row in quality),
        "first_observation_median_ms": median([row["elapsed_ms"] for row in quality]) if quality else None,
        "warm_repeat_median_ms": median([row["elapsed_ms"] for row in rows if row["role"] == "regression" and row["repetition"] > 1])
            if any(row["role"] == "regression" and row["repetition"] > 1 for row in rows) else None,
        "controls": [{"case_id": row["case_id"], "status": row["report"].get("status"),
                      "guarded": row["score"]["guarded"], "model_called": row["score"]["model_called"],
                      "model_correctness_credit": None} for row in first if row["role"] != "regression"],
        "causal_correctness": None, "human_time_saved": None}
    if not quality:
        return result
    kind = quality[0]["score"]["kind"]
    result["kind"] = kind
    if kind == "diagnostic":
        result.update(grounded_hypotheses=sum(row["score"]["grounded_hypothesis"] for row in quality),
                      diagnostic_reference_target_matches=sum(row["score"]["diagnostic_reference_target_match"] for row in quality),
                      hypothesis_causal_correctness=None)
    else:
        accepted = [row for row in quality if row["score"]["accepted"]]
        result.update(raw_top_valid=sum(row["score"]["raw_top_valid"] for row in quality),
            raw_top_matches=sum(row["score"]["raw_top_match"] for row in quality),
            raw_top_match_rate=sum(row["score"]["raw_top_match"] for row in quality)/len(quality),
            accepted=len(accepted), coverage=len(accepted)/len(quality), abstention_or_error_rate=1-len(accepted)/len(quality),
            accepted_matches=sum(row["score"]["accepted_match"] for row in accepted),
            accepted_match_rate=sum(row["score"]["accepted_match"] for row in accepted)/len(accepted) if accepted else None,
            wrong_accepted=[row["case_id"] for row in accepted if not row["score"]["accepted_match"]])
        if kind == "action":
            available = sum(row["score"]["action_target_available"] for row in quality)
            result.update(cases_with_available_target=available,
                          raw_match_rate_given_available_target=sum(row["score"]["raw_top_match"] for row in quality)/available if available else None,
                          source_target_matches_are_causal_correctness=False)
    result["changed_outcome_cases"] = [case_id for case_id in sorted({row["case_id"] for row in rows})
        if len({(row["report"].get("status"), row["report"].get("raw_top_selection"), row["report"].get("selection"),
                 fingerprint(row["report"].get("generated_diagnostic")))
                for row in rows if row["case_id"] == case_id}) > 1]
    return result


class TracedQwen(LlamaCppProvider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.traces = []

    async def _json(self, method, path, payload=None):
        entry = {"method": method, "path": path, "request": payload}
        self.traces.append(entry)
        tick = perf_counter()
        try:
            entry["response"] = await super()._json(method, path, payload)
            return entry["response"]
        except Exception as error:
            entry["error"] = {"type": type(error).__name__, "message": str(error)}
            raise
        finally:
            entry["elapsed_ms"] = (perf_counter() - tick)*1000

    async def infer(self, question):
        try:
            return await super().infer(question)
        except Exception as error:
            self.traces.append({"stage": "infer_protocol_or_distribution", "error": {
                "type": type(error).__name__, "message": str(error)}})
            raise


def _check_ready(model: str, actual: dict, candidate: dict | None, config: dict) -> None:
    if model == "liquid":
        providers = actual.get("providers", [])
        if (actual.get("provider") != "liquid" or actual.get("local_only") is not True or len(providers) != 1
                or providers[0].get("name") != "liquid-local" or providers[0].get("model") != LiquidLocalProvider.model
                or providers[0].get("revision") != LiquidLocalProvider.revision or providers[0].get("locality") != "local"):
            raise ValueError("Liquid runtime identity does not match the pinned local provider")
    elif actual.get("model") != config["model"] or actual.get("build_info") != config["expected_build"]:
        raise ValueError("Qwen runtime identity does not match the configured model/build")
    if candidate is not None and _public(actual) != candidate["protocol_metadata"][model]["ready_identity"]:
        raise ValueError("runtime ready identity differs from the frozen protocol evidence: " + model)


def _validate_proof(proof: dict, cases: list[dict], *, split: str) -> dict:
    rows = proof.get("cases", [])
    ids = [row["case_id"] for row in rows]
    expected = {case["id"] for case in cases if case["split"] == "dev"} if split == "dev" else {case["id"] for case in cases}
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise ValueError("proof membership differs from the frozen split; no case filtering is permitted")
    by_id = {row["case_id"]: row for row in rows}
    for row in rows:
        if not row.get("verified"):
            raise ValueError("unverified fixture in proof")
        phases = (row["baseline"], row["mutation"], row["reference_repair"]["run"])
        if row["baseline"]["exit_code"] != 0 or row["mutation"]["exit_code"] in (0, None) or not row["reference_repair"]["passed"]:
            raise ValueError("proof does not establish original-pass/bug-fail/reference-pass")
        for phase in phases:
            if (not phase["source_stable"] or not phase["protected_files_ok"] or phase["timed_out"]
                    or sha(phase["stdout"].encode("utf-8")) != phase["run_sha256"]):
                raise ValueError("proof phase integrity/hash mismatch")
    return by_id


def _payload(plan: dict, *, diagnostic: bool) -> dict:
    if diagnostic:
        refs = triage_diagnostic.reference_catalog(plan)
        return {"messages": triage_diagnostic.input_messages(plan, refs),
                "json_schema": triage_diagnostic.generation_schema(refs), "n_predict": triage_diagnostic.MAX_TOKENS,
                "samplers": ["temperature"], "temperature": 0, "seed": 42, "cache_prompt": False,
                "n_probs": 0, "top_k": 0, "top_p": 1, "min_p": 0}
    return {"state": plan["model_state"], "instructions": plan["instructions"], "options": plan["options"],
            "policy": {"local_only": True, "min_confidence": 0, "timeout_ms": triage_v3.TIMEOUT_MS}}


def first_grounded_action(plan: dict) -> dict:
    """Zero-inference comparator; no probability or learned-confidence estimate."""
    report = triage_v3.report_from_plan(plan)
    report.update(source="first_grounded_action", model_policy=None, confidence=None, diagnostic_scoring_only=False)
    if not plan["guard_reason"]:
        action = next((action for action in plan["actions"] if action["id"] != "manual_review"), None)
        if action is not None:
            report.update(status="suggested", selection=action["id"], raw_top_selection=action["id"], selected_action=action,
                          next_check={"title": action["title"], "steps": [action["objective"]]})
    return report


async def run(args: argparse.Namespace) -> dict:
    if not 1 <= args.repetitions <= 10 or not args.cells or len(args.cells) != len(set(args.cells)) or not set(args.cells) <= set(CELLS):
        raise ValueError("use unique declared cells and 1–10 timing repetitions")
    output = Path(args.output)
    if output.exists():
        raise ValueError("output already exists; preserve previous observations")
    config_path, proof_path = Path(args.model_config), Path(args.proof)
    candidate = None
    if args.split in {"evaluation", "controls"} or args.candidate_freeze:
        candidate = verify_candidate(Path(args.candidate_freeze) if args.candidate_freeze else None, config_path,
                                     liquid_url=args.liquid_url, qwen_url=args.qwen_url)
    if args.split == "dev" and proof_path.name != "development-proof.json":
        raise ValueError("development runs require the development-proof.json artifact")
    if args.split not in {"dev", "evaluation", "controls"}:
        raise ValueError("reserved trials and unknown splits cannot be evaluated")
    source_before = _source_hashes()
    config = triage_v3.load_qwen_config(config_path)
    case_freeze = lab.verify_freeze()
    catalog = lab.load_catalog()
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    by_id = _validate_proof(proof, catalog["cases"], split=args.split)
    selected = [case for case in catalog["cases"] if case["split"] == args.split and case["role"] != "trial_regression"]
    if not selected:
        raise ValueError("no cases in requested frozen split")
    traces, rows, preparations = [], [], []
    liquid = Client(args.liquid_url)
    try:
        validate_local_client(liquid)
        qwen = TracedQwen(base_url=args.qwen_url, **config)
    except Exception:
        await liquid.aclose()
        raise
    async def capture_request(request):
        traces.append({"transport": "liquid", "method": request.method, "path": request.url.path,
                       "request_text": request.content.decode("utf-8", "replace")})
    async def capture_response(response):
        await response.aread()
        traces.append({"transport": "liquid", "status_code": response.status_code,
                       "path": response.request.url.path, "response_text": response.text})
    liquid._http.event_hooks = {"request": [capture_request], "response": [capture_response]}
    try:
        for case in selected:
            project = proof_path.parent / case["id"]
            tick = perf_counter()
            try:
                observed_source = {path: sha((project / path).read_bytes()) for path in case["editable_paths"]}
                if observed_source != by_id[case["id"]]["editable_mutation_sha256"]:
                    raise ValueError("current production bytes differ from the observed mutant proof")
                evidence = lab.prepare_machine_evidence(project, by_id[case["id"]]["mutation"])
                if evidence.get("source_context_omitted"):
                    raise ValueError("saved failure/source context is stale; no reenrichment or model call is allowed")
                preparation_ms = (perf_counter()-tick)*1000
                plan_tick = perf_counter()
                plans = {design: triage_v3.prepare_plan(evidence, project=project, design=design) for design in ("route", "action")}
                plan_preparation_ms = (perf_counter()-plan_tick)*1000
                for key in ("evidence", "model_state", "actions"):
                    if fingerprint(plans["route"][key]) != fingerprint(plans["action"][key]):
                        raise ValueError("factorial cells do not share the same prepared evidence/state/actions")
                if observed_source != {path: sha((project / path).read_bytes()) for path in case["editable_paths"]}:
                    raise ValueError("production changed while the shared plans were prepared")
                preparations.append({"case_id": case["id"], "evidence_preparation_ms": preparation_ms,
                    "shared_plan_preparation_ms": plan_preparation_ms,
                    "editable_source_sha256": observed_source,
                    "evidence_sha256": fingerprint(evidence), "model_state_sha256": fingerprint(plans["action"]["model_state"]),
                    "actions_sha256": fingerprint(plans["action"]["actions"])})
                preparation_error = None
            except Exception as error:
                evidence, plans = {}, {}
                preparation_error = {"stage": "evidence_preparation", "type": type(error).__name__, "message": str(error)}
            for repetition in range(1, args.repetitions+1):
                for cell in args.cells:
                    model, design = ("baseline", "action") if cell == "first_grounded_action" else cell.split("_", 1)
                    plan = plans.get("action" if design == "diagnostic" else design)
                    error = preparation_error
                    try:
                        payload = _payload(plan, diagnostic=design == "diagnostic") if plan else None
                        if model == "baseline" and plan:
                            payload = {"actions": plan["actions"], "guard_reason": plan["guard_reason"]}
                    except Exception as caught:
                        payload = None
                        error = {"stage": "payload_preparation", "type": type(caught).__name__, "message": str(caught)}
                    start = perf_counter()
                    liquid_trace_start, qwen_trace_start = len(traces), len(qwen.traces)
                    report = None
                    observed_runtime = None
                    if error is not None:
                        report = {"status": "unavailable", "error": "Evidence preparation failed; no model call was made."}
                    else:
                        try:
                            if model == "baseline":
                                report = first_grounded_action(plan)
                            elif plan["guard_reason"]:
                                report = triage_v3.report_from_plan(plan)
                                if design == "diagnostic": report.update(design="diagnostic", diagnostic_references=[])
                            else:
                                provider = liquid if model == "liquid" else qwen
                                metadata = await provider.ready()
                                observed_runtime = metadata
                                _check_ready(model, metadata, candidate, config)
                                if design == "diagnostic":
                                    report = await triage_diagnostic.diagnose_plan(plan, provider=qwen)
                                    if report.get("generation_input_sha256") is not None and report["generation_input_sha256"] != fingerprint(payload["messages"][1]["content"]):
                                        raise ValueError("diagnostic generation payload differs from the retained shared plan")
                                else:
                                    engine = liquid if model == "liquid" else Decilist([qwen])
                                    report = await triage_v3.rank_plan(plan, engine, diagnostic_scoring=True, runtime=metadata)
                                    decision = report.get("decision")
                                    expected_provider = "liquid-local" if model == "liquid" else "llama-cpp-local"
                                    expected_model = LiquidLocalProvider.model if model == "liquid" else config["model"]
                                    if decision and decision.get("status") == "accepted" and (decision.get("provider") != expected_provider or decision.get("model") != expected_model):
                                        raise ValueError("decision provenance differs from the selected factorial model")
                                    if decision and decision.get("status") == "abstained":
                                        error = {"stage": "provider_attempts", "outcomes": [attempt["outcome"] for attempt in decision.get("attempts", [])]}
                        except Exception as caught:
                            error = {"stage": "model_measurement", "type": type(caught).__name__, "message": str(caught)}
                            report = {**(report or {}), "status": "unavailable", "error": "Runtime/measurement failed; no substitute prediction was inferred."}
                    elapsed = (perf_counter()-start)*1000
                    if plan:
                        score = score_output(report, plan, case, cell=cell)
                    else:
                        score = {"kind": design, "guarded": False, "error": report["error"], "model_called": False,
                            "raw_top_valid": False, "raw_top_match": False, "accepted": False, "accepted_match": False,
                            "grounded_hypothesis": False, "diagnostic_reference_target_match": False, "action_target_available": False}
                    actual_model_attempt = any(trace.get("path") == "/v1/decisions" and trace.get("method") == "POST"
                                               for trace in traces[liquid_trace_start:]) or any(
                        trace.get("path") == "/completion" and trace.get("method") == "POST" for trace in qwen.traces[qwen_trace_start:])
                    score["model_called"] = actual_model_attempt
                    score["model_request_attempted"] = actual_model_attempt
                    if error is not None:
                        score.update(raw_top_valid=False, raw_top_match=False, accepted=False, accepted_match=False,
                                     grounded_hypothesis=False, diagnostic_reference_target_match=False)
                    rows.append({"case_id": case["id"], "role": case["role"], "split": case["split"], "cell": cell,
                        "repetition": repetition, "elapsed_ms": elapsed, "input_evidence": evidence,
                        "input_payload": payload, "input_payload_sha256": fingerprint(payload),
                        "model_state_sha256": fingerprint(plan["model_state"]) if plan else None,
                        "model_state_utf8_sha256": sha(plan["model_state"].encode("utf-8")) if plan and isinstance(plan["model_state"], str) else None,
                        "actions_sha256": fingerprint(plan["actions"]) if plan else None,
                        "choice_order": list(plan["options"]) if plan else [],
                        "zero_inference_baseline": model == "baseline",
                        "raw_choice_index": list(plan["options"]).index(report.get("raw_top_selection"))
                            if plan and report.get("raw_top_selection") in plan["options"] else None,
                        "plan_actions": plan["actions"] if plan else [], "measurement_error": error, "score": score, "report": report})
                    rows[-1]["observed_runtime"] = observed_runtime
    finally:
        await liquid.aclose()
        await qwen.aclose()
    traces.extend({"transport": "qwen", **trace} for trace in qwen.traces)
    result = {"suite": "triage-v3-matrix", "split": args.split, "measured_at": datetime.now(timezone.utc).isoformat(),
        "candidate_frozen": candidate is not None, "candidate": candidate,
        "candidate_manifest_sha256": sha(Path(args.candidate_freeze).read_bytes()) if args.candidate_freeze else None,
        "corpus_freeze_sha256": sha(lab.FREEZE.read_bytes()), "proof_sha256": sha(proof_path.read_bytes()),
        "runtime_config_sha256": sha(config_path.read_bytes()), "runtime_configuration": config,
        "sources_sha256_before": source_before, "sources_sha256_after": _source_hashes(),
        "specifications": {"ranking": triage_v3.specification(), "diagnostic": triage_diagnostic.specification()},
        "cell_order": list(args.cells),
        "repetitions": args.repetitions, "unique_selected_cases": len(selected),
        "excluded_trial_ids": list(lab.trial_case_ids()), "preparations": preparations,
        "summaries": {cell: summarize([row for row in rows if row["cell"] == cell]) for cell in args.cells}, "rows": rows,
        "limitations": ["Route agreement and action source-target matching are different outcomes, never a combined accuracy.",
            "Target availability/matching and valid diagnostic citations do not establish a correct causal hypothesis or repair.",
            "Only first observations measure unique-case quality; later repetitions measure timing/stability, not independent examples.",
            "Guarded controls receive no model correctness credit. All selected cases, errors, abstentions and outputs are retained.",
            "model_called/model_request_attempted records an observed inference-endpoint request, not proof that a GPU kernel executed; preflight-only failures do not count as inference requests.",
            "Assistance timings exclude model-server loading and shared evidence preparation. First calls may include protocol/token-cache setup.",
            "Per-cell elapsed_ms covers ready checks and the selected helper; shared source preparation and retained-payload reconstruction are recorded separately or excluded.",
            "Runtime identity HTTP checks are not cryptographic file attestation; artifact/binary hashes are retained external setup evidence.",
            "The first-grounded-action baseline performs no inference and has no learned confidence or probability threshold; its selection timing is separate from shared preparation.",
            "Native Liquid and alias-conditioned Qwen probabilities are uncalibrated and are not interchangeable confidence measures."],
        "unmeasured": {"causal_correctness": None, "human_time_saved": None, "cost_usd": None, "model_server_load_ms": None}}
    result["source_stable_during_measurement"] = result["sources_sha256_before"] == result["sources_sha256_after"]
    result["valid_frozen_comparison"] = candidate is not None and result["source_stable_during_measurement"]
    private = lab.LAB_ROOT / "measurements" / uuid4().hex
    private.mkdir(parents=True)
    raw_bytes = _json_bytes({"report": result, "decoded_transport_traces": traces})
    raw_file = private / "raw.json.gz"
    with gzip.GzipFile(filename=str(raw_file), mode="xb", mtime=0) as handle:
        handle.write(raw_bytes)
    public = _public(result)
    public["private_trace_record"] = {"workspace_relative": "work/triage-v3-lab/measurements/" + private.name + "/raw.json.gz",
        "compressed_sha256": sha(raw_file.read_bytes()), "decoded_json_sha256": sha(raw_bytes),
        "kind": "Full decoded JSON transport traces; not original HTTP byte framing"}
    public["transport_trace_summaries"] = [{"transport": trace["transport"], "method": trace.get("method"), "path": trace.get("path"),
        "stage": trace.get("stage"), "status_code": trace.get("status_code"), "request_sha256": fingerprint(trace.get("request", trace.get("request_text"))),
        "response_sha256": fingerprint(trace.get("response", trace.get("response_text"))), "error": _public(trace.get("error"))} for trace in traces]
    for raw_row, public_row in zip(rows, public["rows"]):
        public_row["public_input_payload_sha256"] = fingerprint(public_row["input_payload"])
        public_row["public_input_payload_matches_private_input"] = public_row["input_payload_sha256"] == public_row["public_input_payload_sha256"]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        handle.write(_json_bytes(public))
    print(json.dumps(public["summaries"], indent=2), flush=True)
    return public


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proof", required=True)
    parser.add_argument("--split", choices=("dev", "evaluation", "controls"), default="dev")
    parser.add_argument("--model-config", required=True)
    parser.add_argument("--candidate-freeze")
    parser.add_argument("--liquid-url", default="http://127.0.0.1:8765")
    parser.add_argument("--qwen-url", default="http://127.0.0.1:8792")
    parser.add_argument("--cells", choices=CELLS, nargs="+", default=list(CELLS))
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--output", default="reports/local/triage-v3-matrix.json")
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    main()
