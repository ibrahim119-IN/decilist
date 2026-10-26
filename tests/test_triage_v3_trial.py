"""Stub cases only: these tests do not start any reserved live agent session."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from benchmarks import triage_agent_trial as v2
from benchmarks import triage_v3_trial as trial


class Lab:
    def __init__(self, root):
        self.LAB_ROOT = root
        root.mkdir()
        self.prepared = []
        self.run_count = 0
        self.passed = False
        self.role = "trial_regression"
        self.protected = {}

    def trial_case_ids(self):
        return ("r01", "r02")

    def get_case_role(self, case_id):
        assert case_id in self.trial_case_ids()
        return self.role

    def prepare_case(self, case_id, directory):
        directory.mkdir()
        (directory / ".triage").mkdir()
        relative = "vendor/httpx/_urls.py"
        path = directory / relative
        path.parent.mkdir(parents=True)
        path.write_text("# Synthetic production fixture\n")
        test = directory / ".triage/test_case.py"
        test.write_text("# Frozen synthetic checks\n")
        self.protected[str(directory)] = trial._digest(test)
        manifest = {"case_id": case_id, "project": "synthetic", "editable_paths": [relative],
                    "protected_sha256": {".triage/test_case.py": self.protected[str(directory)]}}
        (directory / ".triage/manifest.json").write_text(json.dumps(manifest))
        self.prepared.append(case_id)
        return manifest

    def run_case(self, directory):
        self.run_count += 1
        manifest = json.loads((directory / ".triage/manifest.json").read_text())
        intact = trial._digest(directory / ".triage/test_case.py") == self.protected[str(directory)]
        output = "3 passed\n" if self.passed else "FAILED synthetic protected check\n"
        return {"exit_code": 0 if self.passed else 1, "stdout": output, "elapsed_ms": 1,
                "run_sha256": hashlib.sha256(output.encode()).hexdigest(), "protected_files_ok": intact,
                "source_stable": True, "timed_out": False, "command": ["python", "-m", "pytest"],
                "editable_sha256_after": {path: trial._digest(directory / path) for path in manifest["editable_paths"]}}

    def validate_repair(self, directory):
        run = self.run_case(directory)
        return {"passed": self.passed and run["protected_files_ok"], "integrity_ok": run["protected_files_ok"], "run": run}

    def prepare_case_evidence(self, directory, observed):
        assert observed["run_sha256"] == hashlib.sha256(observed["stdout"].encode()).hexdigest()
        return {"schema_version": 1, "log": observed["stdout"], "exit_code": observed["exit_code"], "references": []}


@pytest.fixture
def fixture(tmp_path):
    lab = Lab(tmp_path / "triage-v3-lab")
    directory = lab.LAB_ROOT / "agent-trials" / "stub-trial"
    return lab, directory


def prepare(fixture, treatment="normal_tools"):
    lab, directory = fixture
    planned = next(item for item in trial.plan_trials() if item["treatment"] == treatment)
    return trial.prepare_trial(planned, directory, agent_id="unit-test-agent", lab=lab)


def test_plan_predeclares_all_eight_case_treatment_combinations():
    plan = trial.plan_trials()
    assert len(plan) == 8
    assert {(item["case_id"], item["treatment"]) for item in plan} == {
        (case, treatment) for case in trial.CASE_IDS for treatment in trial.TREATMENTS
    }
    for treatment in trial.TREATMENTS:
        assert sum(item["slot"] for item in plan if item["treatment"] == treatment) == 9


def test_prepare_is_neutral_and_clock_is_unstarted(fixture):
    lab, directory = fixture
    state = prepare(fixture)
    assert state["run_type"] == "agent-run" and state["experiment"] == "triage-v3-repair"
    assert state["start_clock"] is state["agent_elapsed_seconds"] is None
    assert state["cap_seconds"] == 900 and state["agent_settings"] == v2.SETTINGS
    assert state["original_buggy_source_sha256"] and state["plan_sha256"]
    assert "expected_route" not in state and "reference_repair" not in state
    assert not (directory / "case/trial.json").exists() and lab.prepared == [state["case_id"]]
    assert all(value is None for value in state["unmeasured"].values())


def test_nontrial_roles_and_wrong_root_are_rejected_before_preparation(fixture):
    lab, directory = fixture
    lab.role = "evaluation_regression"
    with pytest.raises(trial.TrialError, match="trial_regression"):
        prepare(fixture)
    assert lab.prepared == []
    lab.role = "trial_regression"
    with pytest.raises(trial.TrialError, match="agent-trials"):
        trial.prepare_trial(trial.plan_trials()[0], lab.LAB_ROOT / "outside", agent_id="unit", lab=lab)
    assert lab.prepared == []


def test_wrappers_preserve_v2_globals_and_record_real_tool_intervals(fixture):
    lab, directory = fixture
    saved_pairs, saved_conditions, saved_loader = deepcopy(v2.PAIRS), set(v2.CONDITIONS), v2._lab_api
    prepare(fixture)
    trial.start_trial(directory, lab=lab)
    attempt = trial.begin_attempt(directory, "observed external inspection", lab=lab)
    record = trial.end_attempt(directory, attempt, exit_code=1, observation="Observed command failure", lab=lab)
    assert record["elapsed_seconds"] >= 0 and record["executed_by_runner"] is False
    trial.stop_trial(directory, reason="unsolved", lab=lab)
    state = trial.status(directory, lab=lab)
    assert state["status"] == "unsolved" and state["failed_tool_attempts"] == 1
    assert v2.PAIRS == saved_pairs and v2.CONDITIONS == saved_conditions and v2._lab_api is saved_loader


def test_verification_keeps_failures_and_rejects_changed_tests(fixture):
    lab, directory = fixture
    state = prepare(fixture)
    trial.start_trial(directory, lab=lab)
    assert not trial.verify_trial(directory, lab=lab)["verified_endpoint"]
    (Path(state["case_dir"]) / ".triage/test_case.py").write_text("# Weakened checks")
    lab.passed = True
    assert not trial.verify_trial(directory, lab=lab)["verified_endpoint"]
    result = trial.status(directory, lab=lab)
    assert result["status"] == "invalid" and result["failed_verifications"] == 2


@pytest.mark.parametrize("treatment,status", [
    ("qwen_route", "needs_review"), ("qwen_action", "suggested"),
    ("qwen_diagnostic", "grounded_hypothesis"),
])
async def test_each_treatment_executes_live_after_start_and_accepts_distinct_statuses(fixture, treatment, status):
    lab, directory = fixture
    prepare(fixture, treatment)
    trial.start_trial(directory, lab=lab)
    with pytest.raises(trial.TrialError, match="live callback"):
        trial.verify_trial(directory, lab=lab)
    calls = []
    async def callback(evidence, *, project, treatment, endpoint):
        current = trial._read(directory, lab)
        assert current["status"] == "active" and current["start_clock"] is not None
        assert project == directory / "case" and endpoint == "http://127.0.0.1:8792"
        assert evidence["exit_code"] == 1 and "expected_route" not in evidence
        calls.append(treatment)
        return {"status": status, "source": treatment, "model": {"name": "stub-qwen"},
                "provenance": {"request": "live-stub"}}
    report = await trial.hint_trial(directory, callback=callback, callback_version="triage-v3.0", treatment=treatment, lab=lab)
    assert calls == [treatment] and report["status"] == status and "confidence" not in report
    entry = trial._read(directory, lab)["hints"][0]
    assert entry["callback_invoked"] and entry["elapsed_seconds"] >= 0
    assert entry["case_source_stable"] and entry["callback_source_stable"]
    assert entry["model"] == {"name": "stub-qwen"} and entry["provenance"]["request"] == "live-stub"
    assert entry["report_sha256"] == trial._digest(Path(entry["report_path"]))
    lab.passed = True
    assert trial.verify_trial(directory, lab=lab)["verified_endpoint"]
    assert trial.status(directory, lab=lab)["hint_statuses"] == [status]


async def test_treatment_mismatch_remote_and_precomputed_inputs_never_call_hint(fixture):
    lab, directory = fixture
    prepare(fixture)
    trial.start_trial(directory, lab=lab)
    calls = []
    async def callback(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Must not run")
    with pytest.raises(trial.TrialError, match="does not match"):
        await trial.hint_trial(directory, callback=callback, callback_version="v3", treatment="qwen_route", lab=lab)
    with pytest.raises(ValueError, match="loopback"):
        await trial.hint_trial(directory, callback=callback, callback_version="v3", treatment="qwen_route", endpoint="https://external.example", lab=lab)
    with pytest.raises(trial.TrialError, match="live callback"):
        await trial.hint_trial(directory, callback={"precomputed": True}, callback_version="v3", treatment="qwen_route", lab=lab)
    assert calls == []


async def test_edits_require_fresh_timed_evidence_and_callback_errors_are_retained(fixture):
    lab, directory = fixture
    state = prepare(fixture, "qwen_action")
    trial.start_trial(directory, lab=lab)
    (Path(state["case_dir"]) / state["editable_paths"][0]).write_text("# Changed synthetic production")
    calls = []
    async def callback(evidence, **kwargs):
        calls.append(evidence)
        raise RuntimeError("Observed live provider failure")
    with pytest.raises(trial.TrialError, match="fresh protected"):
        await trial.hint_trial(directory, callback=callback, callback_version="v3", treatment="qwen_action", lab=lab)
    assert calls == []
    with pytest.raises(RuntimeError):
        await trial.hint_trial(directory, callback=callback, callback_version="v3", treatment="qwen_action", fresh_run=True, lab=lab)
    entry = trial._read(directory, lab)["hints"][-1]
    assert entry["callback_invoked"] and entry["status"] == "error" and entry["elapsed_seconds"] >= 0
    assert entry["evidence_run"]["exit_code"] == 1 and lab.run_count == 2
    assert trial.status(directory, lab=lab)["trusted_test_calls"] == 1


def test_elapsed_cap_is_enforced_without_faking_human_time(fixture):
    lab, directory = fixture
    prepare(fixture)
    trial.start_trial(directory, lab=lab)
    # A crafted unit-record clock, not a real 901-second agent session.
    state = trial._read(directory, lab)
    state["start_clock"]["monotonic_ns"] -= 901_000_000_000
    state["start_clock"]["wall_ns"] -= 901_000_000_000
    v2._write(directory, state)
    lab.passed = True
    assert trial.verify_trial(directory, lab=lab)["verified_endpoint"]
    result = trial.status(directory, lab=lab)
    assert result["status"] == "capped" and result["agent_elapsed_seconds"] >= 901
    assert result["unmeasured"]["human_minutes_saved"] is None


def test_cli_prepare_status_start_and_live_hint_use_explicit_bridge(fixture, monkeypatch, capsys):
    lab, directory = fixture
    monkeypatch.setattr(trial, "_lab_api", lambda: lab)
    assert trial.main(["prepare", "--trial", str(directory), "--slot", "2", "--agent-id", "unit-cli"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"
    assert trial.main(["status", "--trial", str(directory)]) == 0
    assert json.loads(capsys.readouterr().out)["agent_elapsed_seconds"] is None
    assert trial.main(["start", "--trial", str(directory)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "active"
    calls = []
    async def callback(evidence, *, project, treatment, endpoint):
        calls.append((treatment, endpoint))
        return {"status": "needs_review", "source": "unit-live-bridge"}
    monkeypatch.setattr(trial, "_callback", lambda name: callback)
    assert trial.main(["hint", "--trial", str(directory), "--callback", "decilist.triage_v3:trial_hint",
                       "--callback-version", "triage-v3.0", "--treatment", "qwen_route"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "needs_review"
    assert calls == [("qwen_route", "http://127.0.0.1:8792")]
    lab.passed = True
    assert trial.main(["verify", "--trial", str(directory)]) == 0
    assert json.loads(capsys.readouterr().out)["trial_status"] == "solved"


def test_text_hint_presentation_preserves_complete_json_report(fixture, monkeypatch, capsys):
    lab, directory = fixture
    prepare(fixture, "qwen_diagnostic")
    trial.start_trial(directory, lab=lab)
    monkeypatch.setattr(trial, "_lab_api", lambda: lab)
    report = {
        "design": "diagnostic", "status": "grounded_hypothesis",
        "hypothesis": "A synthetic hypothesis tied to observed evidence.",
        "next_check": {"title": "Inspect the observed boundary", "steps": ["Compare the frozen assertion."]},
        "diagnostic_references": [{"path": "vendor/httpx/_urls.py", "line": 5, "symbol": "synthetic"}],
        "model": {"name": "stub-qwen"}, "unshown_raw": {"sentinel": "FULL_JSON_PRESERVED"},
    }
    async def callback(evidence, **kwargs):
        return report
    monkeypatch.setattr(trial, "_callback", lambda name: callback)
    assert trial.main(["hint", "--trial", str(directory), "--callback", "decilist.triage_v3:trial_hint",
                       "--callback-version", "triage-v3.0", "--treatment", "qwen_diagnostic", "--format", "text"]) == 0
    output = capsys.readouterr().out
    assert "diagnostic | grounded_hypothesis" in output
    assert "Hypothesis (unverified):" in output
    assert "Code: vendor/httpx/_urls.py:5 synthetic" in output
    assert "FULL_JSON_PRESERVED" not in output and "confidence" not in output
    entry = trial._read(directory, lab)["hints"][-1]
    assert json.loads(Path(entry["report_path"]).read_text()) == report
    assert entry["report_sha256"] == trial._digest(Path(entry["report_path"]))
