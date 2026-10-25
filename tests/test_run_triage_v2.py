import argparse
import copy
import hashlib
import json

import pytest

from benchmarks import run_triage_v2 as runner
from benchmarks import run_triage_v2_controls as controls


def row(case_id, *, role="regression", route="application_logic", status="suggested",
        repetition=1, elapsed_ms=10, targets=None):
    return {"case_id": case_id, "role": role, "route": route, "status": status,
            "repetition": repetition, "elapsed_ms": elapsed_ms,
            "acceptable_routes": ["application_logic"], "inspection_targets": targets or []}


def test_summary_distinguishes_unique_quality_accepted_accuracy_and_repeats():
    rows = [
        row("correct", targets=[{"path_present": True, "symbol_present": True}]),
        row("wrong", route="contract_validation", elapsed_ms=20,
            targets=[{"path_present": True, "symbol_present": False}]),
        row("uncertain", status="needs_review", route="manual_review", elapsed_ms=30),
        row("explicit-review", route="manual_review", elapsed_ms=40),
        row("correct", repetition=2, route="decision_policy", elapsed_ms=100),
        row("control", role="mixed_control", route="contract_validation", elapsed_ms=9999),
    ]
    metrics = runner.summarize(rows)
    assert metrics["unique_regressions"] == 4
    assert metrics["accepted"] == 2 and metrics["correct_accepted"] == 1
    assert metrics["coverage"] == .5 and metrics["accuracy_accepted"] == .5
    assert metrics["wrong_accepted"] == [{"case_id": "wrong", "route": "contract_validation",
                                          "acceptable_routes": ["application_logic"]}]
    assert metrics["changed_outcome_cases"] == ["correct"]
    assert metrics["median_assistant_ms"] == 30  # all five regression attempts; control excluded
    assert metrics["cases_with_target_path"] == 2 and metrics["cases_with_target_symbol"] == 1
    assert metrics["controls"] == [{"case_id": "control", "status": "suggested",
                                    "route": "contract_validation", "specific_suggestion": True}]
    assert metrics["human_time_saved"] is None


def test_empty_or_control_only_summary_has_unknown_quality_not_perfect_accuracy():
    for rows in ([], [row("control", role="insufficient_control", status="needs_review", route="manual_review")]):
        metrics = runner.summarize(rows)
        assert metrics["unique_regressions"] == metrics["accepted"] == 0
        assert metrics["coverage"] is metrics["accuracy_accepted"] is None
        assert metrics["median_assistant_ms"] is None
        assert metrics["wrong_accepted"] == []


@pytest.fixture
def candidate_sandbox(tmp_path, monkeypatch):
    source = tmp_path / "assistant.py"
    source.write_bytes(b"value = 1\n")
    case_freeze = tmp_path / "case-freeze.json"
    case_freeze.write_bytes(b'{"fixture":"frozen-cases"}\n')
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "SOURCE_FILES", ["assistant.py"])
    monkeypatch.setattr(runner, "CANDIDATE", tmp_path / "candidate-freeze.json")
    monkeypatch.setattr(runner.lab, "FREEZE", case_freeze)
    specification = {"fixture": "test-only", "threshold": .7}
    monkeypatch.setattr(runner.triage_assistant, "specification", lambda: copy.deepcopy(specification))
    return source, case_freeze, specification


def test_candidate_freeze_is_exclusive_and_accepts_identical_snapshot(candidate_sandbox):
    frozen = runner.freeze_candidate()
    assert runner.verify_candidate() == frozen
    original = runner.CANDIDATE.read_bytes()
    with pytest.raises(FileExistsError):
        runner.freeze_candidate()
    assert runner.CANDIDATE.read_bytes() == original


@pytest.mark.parametrize("change,field", [("source", "files_sha256"),
    ("specification", "assistant"), ("case-freeze", "case_freeze_sha256")])
def test_candidate_rejects_source_policy_or_case_freeze_drift(candidate_sandbox, change, field):
    source, case_freeze, specification = candidate_sandbox
    runner.freeze_candidate()
    if change == "source":
        source.write_bytes(b"value = 2\n")
    elif change == "specification":
        specification["threshold"] = .5
    else:
        case_freeze.write_bytes(b'{"fixture":"changed-cases"}\n')
    with pytest.raises(ValueError, match=field):
        runner.verify_candidate()


def args(tmp_path, **changes):
    values = {"split": "evaluation", "repetitions": 1, "output": str(tmp_path / "result.json"),
              "proof": str(tmp_path / "proof.json"), "url": "http://127.0.0.1:8765",
              "variants": ["rules_v2"]}
    return argparse.Namespace(**{**values, **changes})


async def test_evaluation_refuses_before_candidate_freeze_or_any_client(candidate_sandbox, tmp_path, monkeypatch):
    monkeypatch.setattr(runner.lab, "verify_freeze", lambda: {})
    def never_construct_client(*_):
        pytest.fail("missing candidate reached client construction")
    monkeypatch.setattr(runner, "Client", never_construct_client)
    with pytest.raises(FileNotFoundError):
        await runner.run(args(tmp_path))
    assert not (tmp_path / "result.json").exists()


async def test_evaluation_drift_is_rejected_before_proof_or_client(candidate_sandbox, tmp_path, monkeypatch):
    source, _, _ = candidate_sandbox
    runner.freeze_candidate()
    source.write_bytes(b"drift = True\n")
    monkeypatch.setattr(runner.lab, "verify_freeze", lambda: {})
    monkeypatch.setattr(runner, "Client", lambda *_: pytest.fail("candidate drift reached client"))
    with pytest.raises(ValueError, match="files_sha256"):
        await runner.run(args(tmp_path))


async def test_reserved_trials_cannot_be_selected_even_programmatically(tmp_path, monkeypatch):
    proof = tmp_path / "proof.json"
    proof.write_text('{"cases": []}', encoding="utf-8")
    monkeypatch.setattr(runner.lab, "verify_freeze", lambda: {})
    monkeypatch.setattr(runner.lab, "load_catalog", lambda: {"cases": [
        {"id": "reserved", "split": "agent_trial", "role": "trial_regression"}]})
    monkeypatch.setattr(runner, "Client", lambda *_: pytest.fail("reserved trial reached client"))
    with pytest.raises(ValueError, match="No cases"):
        await runner.run(args(tmp_path, split="agent_trial"))


async def test_control_wrapper_requires_candidate_before_running(tmp_path, monkeypatch):
    calls = []
    def reject():
        calls.append("verify")
        raise ValueError("candidate is not frozen")
    async def never_run(_):
        pytest.fail("unfrozen candidate reached control evaluation")
    monkeypatch.setattr(controls, "verify_candidate", reject)
    monkeypatch.setattr(controls, "run", never_run)
    with pytest.raises(ValueError, match="not frozen"):
        await controls.run_controls(args(tmp_path))
    assert calls == ["verify"]


async def test_control_wrapper_runs_only_controls_and_excludes_trials(tmp_path, monkeypatch):
    selected = []
    proofs = []
    catalog = []
    for index, role in enumerate(("mixed_control", "ambiguity_control", "insufficient_control"), 1):
        case_id = f"control-{index}"
        catalog.append({"id": case_id, "split": "controls", "role": role, "project": "test-fixture",
                        "provenance": "unit-test-fixture", "acceptable_routes": ["manual_review"]})
        stdout = "AssertionError: unit-test fixture failure"
        proofs.append({"case_id": case_id, "verified": True, "mutation": {
            "stdout": stdout, "run_sha256": hashlib.sha256(stdout.encode()).hexdigest()}})
    catalog.extend([{"id": "reserved", "split": "controls", "role": "trial_regression"},
                    {"id": "evaluation-only", "split": "evaluation", "role": "regression"}])
    proof_path = tmp_path / "proof.json"
    proof_path.write_text(json.dumps({"cases": proofs}), encoding="utf-8")
    freeze_path = tmp_path / "case-freeze.json"
    freeze_path.write_text('{"fixture": true}', encoding="utf-8")
    monkeypatch.setattr(runner.lab, "FREEZE", freeze_path)
    monkeypatch.setattr(runner.lab, "verify_freeze", lambda: {})
    monkeypatch.setattr(runner.lab, "load_catalog", lambda: {"cases": catalog})
    def prepared(project, observed):
        selected.append(project.name)
        return {"schema_version": 1, "log": observed["stdout"], "references": []}
    monkeypatch.setattr(runner.lab, "prepare_machine_evidence", prepared)
    monkeypatch.setattr(runner, "candidate_snapshot", lambda: {"fixture": "no-model-test"})
    checks = []
    monkeypatch.setattr(controls, "verify_candidate", lambda: checks.append("verified"))
    class LocalTestClient:
        def __init__(self, *_): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
    monkeypatch.setattr(runner, "Client", LocalTestClient)
    async def fake_diagnose(evidence, client, **_):
        return {"status": "needs_review", "route": "manual_review", "evidence": evidence,
                "fixture": "unit-test-only; no inference"}
    monkeypatch.setattr(runner.triage_assistant, "diagnose", fake_diagnose)
    result = await controls.run_controls(args(tmp_path, repetitions=2, split="evaluation"))
    assert checks == ["verified"]
    assert result["split"] == "controls"
    assert selected == ["control-1", "control-2", "control-3"] * 2
    assert len(result["rows"]) == 6
    summary = result["summaries"]["rules_v2"]
    assert summary["unique_regressions"] == 0 and summary["coverage"] is None
    assert {row["case_id"] for row in summary["controls"]} == {"control-1", "control-2", "control-3"}
