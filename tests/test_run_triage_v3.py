import argparse
import copy
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks import run_triage_v3 as runner


def plan(design="action", *, guarded=False):
    evidence = {"schema_version": 1, "log": "AssertionError: fixture observation", "references": [], "signals": []}
    actions = [
        {"id": "first", "title": "First inspection", "objective": "Inspect the observed neighboring boundary.", "source_refs": [{"path": "other.py", "symbol": "Other.read", "line": 1}]},
        {"id": "target", "title": "Second inspection", "objective": "Inspect the observed target boundary.", "source_refs": [{"path": "target.py", "symbol": "Target.read", "line": 2}]},
        {"id": "manual_review", "title": "Review", "objective": "Collect evidence.", "source_refs": []},
    ]
    refs = [{"path": "target.py", "line": 2, "excerpt": "2 | value = input_value", "origins": [],
             "implementation_context": [{"symbol": "Target.read"}]}]
    return {"design": design, "evidence": evidence, "inspected": {"facts": {"source_references": refs}, "ambiguity": []},
            "model_state": '{"fixture":"observed source only"}', "actions": actions,
            "options": {action["id"]: action["title"] for action in actions} if design == "action" else {"application_logic": "Logic", "manual_review": "Review"},
            "instructions": "Test fixture " + design, "guard_reason": "Insufficient fixture" if guarded else None,
            "model_state_sha256": runner.fingerprint('{"fixture":"observed source only"}'),
            "action_candidates_sha256": runner.fingerprint(actions)}


CASE = {"acceptable_routes": ["application_logic"], "inspection_targets": [{"path": "target.py", "symbols": ["Target.read"]}]}


def test_raw_top_and_policy_acceptance_are_separate_route_outcomes():
    report = {"status": "needs_review", "raw_top_selection": "application_logic", "selection": None,
              "decision": {"status": "accepted"}, "confidence": .6}
    result = runner.score_output(report, plan("route"), CASE, cell="liquid_route")
    assert result["raw_top_valid"] and result["raw_top_match"]
    assert not result["accepted"] and not result["accepted_match"]
    assert result["model_called"]


def test_action_target_match_and_candidate_availability_are_distinct_from_causality():
    prepared = plan()
    wrong = runner.score_output({"status": "suggested", "selection": "first", "raw_top_selection": "first"}, prepared, CASE, cell="qwen_action")
    assert wrong["action_target_available"] and wrong["matching_action_ids"] == ["target"]
    assert not wrong["raw_top_match"] and not wrong["accepted_match"]
    right = runner.score_output({"status": "suggested", "selection": "target", "raw_top_selection": "target"}, prepared, CASE, cell="qwen_action")
    assert right["raw_top_match"] and right["accepted_match"]
    assert right["action_match_causal_correctness"] is None
    assert not runner.refs_match([{"path": "target.py", "symbol": "Other.read"}], CASE["inspection_targets"])


def test_diagnostic_grounding_is_not_hypothesis_correctness():
    score = runner.score_output({"status": "grounded_hypothesis", "generated_text": "unit-test output",
        "diagnostic_references": [{"path": "target.py", "symbols": ["Target.read"]}]}, plan(), CASE, cell="qwen_diagnostic")
    assert score["grounded_hypothesis"] and score["diagnostic_reference_target_match"]
    assert score["hypothesis_causal_correctness"] is None
    assert "raw_top_match" not in score


def measured(case_id, *, repetition=1, role="regression", accepted=False, matched=False, raw=True, elapsed=10, guarded=False):
    return {"case_id": case_id, "role": role, "repetition": repetition, "elapsed_ms": elapsed,
        "measurement_error": None, "report": {"status": "suggested" if accepted else "needs_review", "selection": "target" if accepted else None},
        "score": {"kind": "action", "guarded": guarded, "error": None, "model_called": not guarded,
                  "raw_top_valid": raw, "raw_top_match": matched, "accepted": accepted, "accepted_match": accepted and matched,
                  "action_target_available": True}}


def test_summary_uses_unique_first_observations_and_keeps_controls_out_of_quality():
    rows = [measured("one", accepted=True, matched=True), measured("two", accepted=True, matched=False, elapsed=20),
            measured("three", accepted=False, matched=True, elapsed=30),
            measured("one", repetition=2, accepted=False, matched=False, elapsed=100),
            measured("control", role="mixed_control", guarded=True, elapsed=999)]
    result = runner.summarize(rows)
    assert result["unique_regressions"] == 3 and result["observations"] == 5
    assert result["raw_top_matches"] == 2 and result["raw_top_match_rate"] == pytest.approx(2/3)
    assert result["accepted"] == 2 and result["coverage"] == pytest.approx(2/3)
    assert result["accepted_match_rate"] == .5 and result["wrong_accepted"] == ["two"]
    assert result["first_observation_median_ms"] == 20 and result["warm_repeat_median_ms"] == 100
    assert result["controls"][0]["model_correctness_credit"] is None
    assert result["changed_outcome_cases"] == ["one"]
    assert result["causal_correctness"] is result["human_time_saved"] is None


def test_first_grounded_action_baseline_never_invents_confidence():
    report = runner.first_grounded_action(plan())
    assert report["selection"] == "first" and report["status"] == "suggested"
    assert report["decision"] is report["confidence"] is report["model_policy"] is None
    guarded = runner.first_grounded_action(plan(guarded=True))
    assert guarded["selection"] is None and guarded["status"] == "needs_review"


def test_generation_stability_detects_changed_hypotheses_with_same_status():
    rows = [measured("one", repetition=index) for index in (1, 2)]
    for index, row in enumerate(rows):
        row["report"] = {"status": "grounded_hypothesis", "generated_diagnostic": {"hypothesis": f"Observed hypothesis {index}"}}
        row["score"].update(kind="diagnostic", grounded_hypothesis=True, diagnostic_reference_target_match=True)
    result = runner.summarize(rows)
    assert result["unique_regressions"] == 1 and result["grounded_hypotheses"] == 1
    assert result["changed_outcome_cases"] == ["one"]


def test_diagnostic_payload_retains_declared_generation_settings():
    payload = runner._payload(plan(), diagnostic=True)
    assert payload["samplers"] == ["temperature"] and payload["temperature"] == 0
    assert payload["n_predict"] == runner.triage_diagnostic.MAX_TOKENS
    assert payload["seed"] == 42 and payload["n_probs"] == 0 and payload["cache_prompt"] is False


def protocol_metadata():
    return {"liquid": {"ready_identity": {"service": "test-liquid"}, "model_artifact_sha256": "a"*64},
            "qwen": {"ready_identity": {"model": "fixture-model", "model_path": "C:\\Users\\fixture\\private\\model.gguf"},
                     "model_artifact_sha256": "b"*64, "runtime_binary_sha256": "c"*64}}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    (tmp_path / "source.py").write_bytes(b"version = 1\n")
    case_freeze = tmp_path / "case-freeze.json"
    case_freeze.write_bytes(b'{"fixture":"corpus"}\n')
    config = tmp_path / "qwen-config.json"
    config.write_bytes(b'{"model":"fixture-model","expected_build":"fixture-build"}\n')
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "SOURCE_FILES", ("source.py",))
    monkeypatch.setattr(runner.lab, "FREEZE", case_freeze)
    monkeypatch.setattr(runner.lab, "LAB_ROOT", tmp_path / "private-lab")
    monkeypatch.setattr(runner.triage_v3, "specification", lambda: {"fixture": "ranking", "threshold": .7})
    monkeypatch.setattr(runner.triage_diagnostic, "specification", lambda: {"fixture": "diagnostic"})
    return tmp_path, config


def candidate_file(sandbox):
    root, config = sandbox
    path = root / "candidate.json"
    path.write_bytes(runner._json_bytes(runner.candidate_snapshot(config, protocol_metadata=protocol_metadata())))
    return path


def test_candidate_builder_is_public_safe_and_runtime_path_changes_are_detected(sandbox):
    _, config = sandbox
    path = candidate_file(sandbox)
    text = path.read_text()
    assert "Users" not in text and "private\\\\model" not in text
    saved = runner.verify_candidate(path, config, liquid_url="http://127.0.0.1:8765", qwen_url="http://127.0.0.1:8792")
    actual = {"model": "fixture-model", "build_info": "fixture-build", "model_path": "C:\\Users\\fixture\\private\\model.gguf"}
    saved["protocol_metadata"]["qwen"]["ready_identity"] = runner._public(actual)
    runner._check_ready("qwen", actual, saved, {"model":"fixture-model", "expected_build":"fixture-build"})
    with pytest.raises(ValueError, match="ready identity"):
        runner._check_ready("qwen", {**actual, "model_path": "C:\\Users\\different\\private\\model.gguf"}, saved,
                            {"model":"fixture-model", "expected_build":"fixture-build"})


@pytest.mark.parametrize("changed", ["source", "config", "corpus"])
def test_candidate_gate_rejects_frozen_drift(sandbox, changed):
    root, config = sandbox
    path = candidate_file(sandbox)
    target = {"source": root / "source.py", "config": config, "corpus": runner.lab.FREEZE}[changed]
    if changed == "config":
        target.write_bytes(b'{"model":"fixture-model","expected_build":"changed-build"}\n')
    else:
        target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="drift"):
        runner.verify_candidate(path, config, liquid_url="http://127.0.0.1:8765", qwen_url="http://127.0.0.1:8792")


def args(root, config, **changes):
    values = {"split":"dev", "proof":str(root / "development-proof.json"), "model_config":str(config),
              "candidate_freeze":None, "liquid_url":"http://127.0.0.1:8765", "qwen_url":"http://127.0.0.1:8792",
              "cells":["first_grounded_action", "liquid_route", "qwen_action"], "repetitions":1,
              "output":str(root / "result.json")}
    return argparse.Namespace(**{**values, **changes})


async def test_evaluation_requires_candidate_before_reading_case_gold(sandbox, monkeypatch):
    root, config = sandbox
    monkeypatch.setattr(runner.lab, "load_catalog", lambda: pytest.fail("evaluation catalog read before candidate gate"))
    monkeypatch.setattr(runner, "Client", lambda *_: pytest.fail("runtime constructed before candidate gate"))
    with pytest.raises(ValueError, match="candidate-freeze"):
        await runner.run(args(root, config, split="evaluation"))


def test_proof_membership_integrity_and_no_posthoc_filtering():
    run = {"exit_code":0, "stdout":"3 passed\n", "source_stable":True, "protected_files_ok":True,
           "timed_out":False, "run_sha256":runner.sha(b"3 passed\n")}
    failed = {**run, "exit_code":1, "stdout":"3 failed\n", "run_sha256":runner.sha(b"3 failed\n")}
    fixture = {"case_id":"dev", "verified":True, "baseline":run, "mutation":failed, "reference_repair":{"passed":True,"run":run}}
    cases = [{"id":"dev","split":"dev"}, {"id":"heldout","split":"evaluation"}, {"id":"trial","split":"agent_trial"}]
    assert runner._validate_proof({"cases":[fixture]}, cases, split="dev")["dev"] == fixture
    with pytest.raises(ValueError, match="membership"):
        runner._validate_proof({"cases":[fixture]}, cases, split="evaluation")
    bad = copy.deepcopy(fixture)
    bad["mutation"]["stdout"] = "edited after hashing"
    with pytest.raises(ValueError, match="integrity"):
        runner._validate_proof({"cases":[bad]}, cases, split="dev")


def install_matrix_fixtures(sandbox, monkeypatch, *, stale=False):
    root, config = sandbox
    cases = [{"id":"dev-one", "split":"dev", "role":"regression", "editable_paths":["target.py"], **CASE},
             {"id":"trial", "split":"agent_trial", "role":"trial_regression"}]
    run = {"exit_code":0,"stdout":"3 passed\n","source_stable":True,"protected_files_ok":True,"timed_out":False,"run_sha256":runner.sha(b"3 passed\n")}
    failed = {**run,"exit_code":1,"stdout":"AssertionError: fixture\n","run_sha256":runner.sha(b"AssertionError: fixture\n")}
    folder = root / "dev-one"
    folder.mkdir()
    (folder / "target.py").write_bytes(b"observed = 1\n")
    fixture = {"case_id":"dev-one","verified":True,"baseline":run,"mutation":failed,
               "reference_repair":{"passed":True,"run":run},"editable_mutation_sha256":{"target.py":runner.sha(b"observed = 1\n")}}
    (root / "development-proof.json").write_bytes(runner._json_bytes({"cases":[fixture]}))
    monkeypatch.setattr(runner.lab,"verify_freeze",lambda:{"fixture":True})
    monkeypatch.setattr(runner.lab,"load_catalog",lambda:{"cases":cases})
    monkeypatch.setattr(runner.lab,"trial_case_ids",lambda:("trial",))
    def prepared(*_):
        result = {"schema_version":1,"log":"AssertionError: fixture","references":[],"signals":[]}
        if stale: result["source_context_omitted"] = "changed"
        return result
    monkeypatch.setattr(runner.lab,"prepare_machine_evidence",prepared)
    monkeypatch.setattr(runner.triage_v3,"prepare_plan",lambda evidence,project,design:plan(design))
    ready_liquid = {"provider":"liquid","local_only":True,"providers":[{"name":"liquid-local", "model":runner.LiquidLocalProvider.model,
                    "revision":runner.LiquidLocalProvider.revision,"locality":"local"}]}
    class FakeLiquid:
        def __init__(self,*_): self._http=SimpleNamespace(event_hooks={})
        async def ready(self): return ready_liquid
        async def aclose(self): pass
    class FakeQwen:
        name,model,locality="llama-cpp-local","fixture-model","local"
        def __init__(self,**_): self.traces=[]
        async def ready(self): raise ValueError("fixture runtime unavailable C:\\Users\\private\\model.gguf")
        async def aclose(self): pass
    monkeypatch.setattr(runner,"Client",FakeLiquid)
    monkeypatch.setattr(runner,"validate_local_client",lambda _:None)
    monkeypatch.setattr(runner,"TracedQwen",FakeQwen)
    calls=[]
    async def rank(prepared,engine,**_):
        calls.append(prepared["design"])
        output=runner.triage_v3.report_from_plan(prepared)
        output.update(status="suggested",selection="application_logic",raw_top_selection="application_logic",
                      decision={"status":"accepted","provider":"liquid-local","model":runner.LiquidLocalProvider.model},fixture="unit-test-only, no inference")
        return output
    monkeypatch.setattr(runner.triage_v3,"rank_plan",rank)
    return root,config,calls


async def test_matrix_retains_every_error_exact_payload_and_zero_inference_baseline(sandbox, monkeypatch):
    root,config,calls=install_matrix_fixtures(sandbox,monkeypatch)
    result=await runner.run(args(root,config,repetitions=2))
    assert len(result["rows"])==6 and calls==["route","route"]
    baseline=[row for row in result["rows"] if row["cell"]=="first_grounded_action"]
    assert all(row["zero_inference_baseline"] and not row["score"]["model_called"] for row in baseline)
    assert baseline[0]["raw_choice_index"]==0 and baseline[0]["choice_order"]==["first","target","manual_review"]
    failures=[row for row in result["rows"] if row["cell"]=="qwen_action"]
    assert len(failures)==2 and all(row["measurement_error"] for row in failures)
    # A returned decision alone is not evidence of a transport/inference call.
    assert all(not row["score"]["model_request_attempted"] for row in result["rows"])
    assert all(row["input_payload_sha256"]==runner.fingerprint(row["input_payload"]) for row in result["rows"])
    assert all(row["public_input_payload_matches_private_input"] for row in result["rows"])
    assert "Users" not in (root/"result.json").read_text()
    rawfiles=list((runner.lab.LAB_ROOT/"measurements").glob("*/raw.json.gz"))
    assert len(rawfiles)==1
    private=json.loads(gzip.decompress(rawfiles[0].read_bytes()))
    assert "Users" in json.dumps(private)  # private trace retained, public artifact redacted
    assert not result["candidate_frozen"] and not result["valid_frozen_comparison"]


async def test_stale_source_evidence_prevents_all_model_calls_and_is_retained(sandbox, monkeypatch):
    root,config,calls=install_matrix_fixtures(sandbox,monkeypatch,stale=True)
    result=await runner.run(args(root,config))
    assert calls==[] and len(result["rows"])==3
    assert all(row["measurement_error"]["stage"]=="evidence_preparation" for row in result["rows"])


async def test_changed_mutant_bytes_prevent_measurement_before_enrichment(sandbox, monkeypatch):
    root,config,calls=install_matrix_fixtures(sandbox,monkeypatch)
    (root/"dev-one/target.py").write_bytes(b"changed = 2\n")
    monkeypatch.setattr(runner.lab,"prepare_machine_evidence",lambda *_:pytest.fail("changed proof source reached enrichment"))
    result=await runner.run(args(root,config))
    assert calls==[] and all(row["measurement_error"] for row in result["rows"])


async def test_frozen_control_guards_get_no_model_credit_and_trials_stay_excluded(sandbox, monkeypatch):
    root,config,calls=install_matrix_fixtures(sandbox,monkeypatch)
    source_proof=json.loads((root/"development-proof.json").read_text())
    fixture=source_proof["cases"][0]
    control_dir=root/"control"
    control_dir.mkdir()
    (control_dir/"target.py").write_bytes(b"observed = 1\n")
    cases=[{"id":"dev-one","split":"dev","role":"regression","editable_paths":["target.py"],**CASE},
           {"id":"control","split":"controls","role":"mixed_control","editable_paths":["target.py"],
            "acceptable_routes":["manual_review"],"inspection_targets":[]},
           {"id":"trial","split":"agent_trial","role":"trial_regression"}]
    full={"cases":[fixture,{**copy.deepcopy(fixture),"case_id":"control"},{**copy.deepcopy(fixture),"case_id":"trial"}]}
    full_path=root/"proof.json"
    full_path.write_bytes(runner._json_bytes(full))
    frozen=root/"candidate.json"
    frozen.write_bytes(b'{"fixture":true}\n')
    monkeypatch.setattr(runner,"verify_candidate",lambda *_args,**_kwargs:{"fixture":True})
    monkeypatch.setattr(runner.lab,"load_catalog",lambda:{"cases":cases})
    monkeypatch.setattr(runner.triage_v3,"prepare_plan",lambda evidence,project,design:plan(design,guarded=True))
    result=await runner.run(args(root,config,split="controls",proof=str(full_path),candidate_freeze=str(frozen),cells=list(runner.CELLS)))
    assert calls==[] and len(result["rows"])==len(runner.CELLS)
    assert {row["case_id"] for row in result["rows"]}=={"control"}
    assert all(row["score"]["guarded"] and not row["score"]["model_called"] for row in result["rows"])
    assert all(summary["unique_regressions"]==0 and summary["controls"][0]["model_correctness_credit"] is None
               for summary in result["summaries"].values())
