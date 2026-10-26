"""Synthetic measurements only: no reserved case content or local model calls."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from benchmarks import triage_v5_trial as trial
from benchmarks import triage_agent_trial as clocks


def change_audit_collection(project, result):
    path = Path(project) / result["run"]["audit_path"]
    audit = json.loads(path.read_text())
    audit["collected"] = ["different-protected-collection"]
    data = json.dumps(audit).encode()
    path.write_bytes(data)
    result["run"]["audit_sha256"] = hashlib.sha256(data).hexdigest()
    return result


READY = {"model": "fake", "build_info": "fake-build", "n_ctx": 16384}
CANDIDATE = {"protocol_metadata": {"qwen": {"ready_identity": READY}}}


class Provider:
    def __init__(self, config=None, endpoint=None):
        self.closed = False

    async def ready(self):
        return READY

    async def aclose(self):
        self.closed = True


class Lab:
    def __init__(self, root, *, execute=False):
        self.LAB_ROOT = root
        self.prepared, self.calls = [], []
        self.protected = {}
        self.execute = execute
        self.error = False
        self.mutate_during_validation = False

    def trial_case_ids(self):
        return trial.CASE_IDS

    def verify_freeze(self):
        return {"verified": True}

    def get_case_role(self, case_id):
        return "trial_regression" if case_id in trial.CASE_IDS else "control" if case_id.startswith("c") else "regression"

    def prepare_case(self, case_id, directory):
        directory = Path(directory)
        (directory / "src").mkdir(parents=True)
        (directory / "src/app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        if case_id == "c01":
            (directory / "src/app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (directory / ".triage").mkdir()
        checks = ("import runpy\nfrom pathlib import Path\n"
                  "add = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'src/app.py'))['add']\n"
                  "assert add(2, 3) == 5\nassert add(-4, 3) == -1\nassert add(0, 0) == 0\n")
        (directory / ".triage/test_case.py").write_text(checks, encoding="utf-8")
        (directory / ".triage/test_regression.py").write_text("assert (2 + 2) == 4\n", encoding="utf-8")
        manifest = {"case_id": case_id, "allowed_production_roots": ["src/"],
                    "failing_tests": [".triage/test_case.py"], "regression_tests": [".triage/test_regression.py"]}
        (directory / ".triage/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.protected[directory] = trial._protected_hashes(directory, ["src/"])
        self.prepared.append(case_id)
        return manifest

    def run_case(self, directory):
        directory = Path(directory)
        self.calls.append(directory)
        before = trial._source_hashes(directory, ["src/"])
        intact = trial._protected_hashes(directory, ["src/"]) == self.protected[directory]
        if self.execute:
            result = subprocess.run([sys.executable, "-I", str(directory / ".triage/test_case.py")],
                                    capture_output=True, text=True, timeout=5)
            code, output = result.returncode, result.stdout + result.stderr
            regression = subprocess.run([sys.executable, "-I", str(directory / ".triage/test_regression.py")],
                                        capture_output=True, text=True, timeout=5)
            code = code or regression.returncode
            output += regression.stdout + regression.stderr
        else:
            code = 0 if "return a + b" in (directory / "src/app.py").read_text() else 1
            output = "3 passed\n" if code == 0 else "FAILED synthetic multi-input check\n"
        after = trial._source_hashes(directory, ["src/"])
        run_dir = directory / ".triage/runs"
        run_dir.mkdir(exist_ok=True)
        audit = {"collected": ["synthetic-case", "synthetic-regression"], "reports": [
            {"nodeid": "synthetic-case", "when": "call", "outcome": "passed" if code == 0 else "failed"},
            {"nodeid": "synthetic-regression", "when": "call", "outcome": "passed"}], "exitstatus": code}
        audit_path = run_dir / ("run-" + str(len(self.calls)) + ".audit.json")
        audit_data = json.dumps(audit).encode()
        audit_path.write_bytes(audit_data)
        return {"exit_code": code, "stdout": output, "elapsed_ms": 1,
                "run_sha256": hashlib.sha256(output.encode()).hexdigest(), "protected_files_ok": intact,
                "source_stable": before == after, "timed_out": False, "command": ["python", "protected-checks"],
                "checks_complete": True, "test_execution_complete": True,
                "audit_path": audit_path.relative_to(directory).as_posix(), "audit_sha256": hashlib.sha256(audit_data).hexdigest(),
                "editable_sha256_before": before, "editable_sha256_after": after}

    def validate_repair(self, directory):
        if self.error:
            raise RuntimeError("Observed validator failure")
        run = self.run_case(directory)
        if self.mutate_during_validation:
            (Path(directory) / "src/app.py").write_text("# altered during root check\n")
        return {"passed": run["exit_code"] == 0 and run["protected_files_ok"],
                "integrity_ok": run["protected_files_ok"], "run": run}

    def prepare_case_evidence(self, directory, observed):
        return {"log": observed["stdout"], "exit_code": observed["exit_code"]}


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    lab = Lab(tmp_path / "v5-lab")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"model": "fake", "expected_build": "fake-build"}))
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps(CANDIDATE))
    monkeypatch.setattr(trial, "verify_candidate", lambda *args, **kwargs: CANDIDATE)
    monkeypatch.setattr(trial, "_provider", Provider)
    return lab, lab.LAB_ROOT / "agent-trials" / "synthetic", config, candidate


async def prepare(fixture, treatment="normal_tools", *, slot=None):
    lab, directory, config, candidate = fixture
    planned = trial.plan_trials()[slot - 1] if slot else next(row for row in trial.plan_trials() if row["treatment"] == treatment)
    return await trial.prepare_trial_async(planned, directory, agent_id="synthetic-agent", config_path=config,
                               candidate_freeze=candidate, lab=lab)


def repair(state):
    (Path(state["case_dir"]) / "src/app.py").write_text("def add(a, b):\n    return a + b\n")


def test_plan_is_the_prespecified_eight_slots():
    assert [(row["case_id"], row["treatment"]) for row in trial.plan_trials()] == [
        ("r01", "normal_tools"), ("r02", "verified_assistant"), ("r03", "normal_tools"),
        ("r04", "verified_assistant"), ("r01", "verified_assistant"), ("r02", "normal_tools"),
        ("r03", "verified_assistant"), ("r04", "normal_tools")]


async def test_candidate_gate_precedes_reserved_membership_and_preparation(fixture, monkeypatch):
    lab, _, _, _ = fixture
    monkeypatch.setattr(trial, "verify_candidate", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("drift")))
    monkeypatch.setattr(lab, "trial_case_ids", lambda: pytest.fail("case membership touched before gate"))
    with pytest.raises(ValueError, match="drift"):
        await prepare(fixture)
    assert lab.prepared == []


async def test_preparation_neutral_broad_roots_and_clock_start(fixture):
    lab, directory, _, _ = fixture
    state = await prepare(fixture)
    assert state["start_clock"] is None and state["status"] == "ready"
    assert state["editable_paths"] == ["src/"] and "src/app.py" in state["original_buggy_source_sha256"]
    assert all(value is None for value in state["unmeasured"].values())
    assert "reference_repair" not in json.dumps(state)
    trial.start_trial(directory, lab=lab)
    with pytest.raises(trial.TrialError, match="restarted"):
        trial.start_trial(directory, lab=lab)


async def test_normal_arm_cannot_request_assistant_and_repair_needs_protected_check(fixture):
    lab, directory, _, _ = fixture
    state = await prepare(fixture)
    trial.start_trial(directory, lab=lab)
    assert not trial.verify_trial(directory, lab=lab)["verified_endpoint"]
    attempt = trial.begin_attempt(directory, "fix synthetic source", phase="repair", lab=lab)
    repair(state)
    trial.end_attempt(directory, attempt, exit_code=0, observation="Actual synthetic edit", lab=lab)
    assert trial.verify_trial(directory, lab=lab)["verified_endpoint"]
    assert trial.status(directory, lab=lab)["status"] == "solved"


async def test_assistant_one_initial_call_then_failure_remains_in_assigned_arm(fixture):
    lab, directory, _, _ = fixture
    state = await prepare(fixture, "verified_assistant")
    trial.start_trial(directory, lab=lab)
    with pytest.raises(trial.TrialError, match="initial live"):
        trial.begin_attempt(directory, "inspect logs", lab=lab)
    with pytest.raises(trial.TrialError, match="initial live"):
        trial.verify_trial(directory, lab=lab)
    calls = []
    async def callback(evidence, **kwargs):
        assert trial._read(directory, lab)["start_clock"] is not None
        calls.append(evidence)
        raise RuntimeError("Observed live workflow failure")
    report = await trial.hint_trial(directory, callback=callback, lab=lab)
    assert report["state"] == "unavailable" and len(calls) == 1
    entry = trial._read(directory, lab)["hints"][0]
    assert entry["callback_invoked"] and entry["status"] == "error" and entry["case_source_stable"]
    assert entry["report_sha256"] == trial._digest(Path(entry["report_path"]))
    with pytest.raises(trial.TrialError, match="exactly one"):
        await trial.hint_trial(directory, callback=callback, lab=lab)
    action = trial.begin_attempt(directory, "repair after assistant failure", phase="repair", lab=lab)
    repair(state)
    trial.end_attempt(directory, action, exit_code=0, lab=lab)
    assert trial.verify_trial(directory, lab=lab)["verified_endpoint"]
    assert trial.status(directory, lab=lab)["treatment"] == "verified_assistant"


async def test_unavailable_readiness_retained_as_initial_attempt_and_allows_manual_repair(fixture):
    lab, directory, _, _ = fixture
    await prepare(fixture, "verified_assistant")
    trial.start_trial(directory, lab=lab)
    class Unavailable(Provider):
        async def ready(self):
            raise RuntimeError("Observed unavailable local runtime")
    report = await trial.hint_trial(directory, provider_factory=Unavailable, lab=lab)
    assert report["state"] == "unavailable"
    state = trial._read(directory, lab)
    assert state["status"] == "active" and state["hints"][0]["workflow_call_attempted"]
    assert not state["hints"][0]["callback_invoked"]
    trial.begin_attempt(directory, "inspect after unavailable initial call", lab=lab)


@pytest.mark.parametrize("verified,apply", [(False, True), (True, False), (True, True)])
async def test_only_explicit_verified_application_may_change_original(fixture, verified, apply):
    lab, directory, _, _ = fixture
    state = await prepare(fixture, "verified_assistant")
    trial.start_trial(directory, lab=lab)
    async def callback(evidence, **kwargs):
        repair(state)
        return {"state": "hypothesis_supported_by_tests" if verified else "unverified",
                "apply": {"applied": True}}
    await trial.hint_trial(directory, callback=callback, apply=apply, lab=lab)
    assert trial.status(directory, lab=lab)["status"] == ("solved" if verified and apply else "invalid")


async def test_test_tampering_never_becomes_solved_and_cap_remains_900(fixture):
    lab, directory, _, _ = fixture
    state = await prepare(fixture)
    trial.start_trial(directory, lab=lab)
    repair(state)
    (Path(state["case_dir"]) / ".triage/test_case.py").write_text("assert True\n")
    assert not trial.verify_trial(directory, lab=lab)["verified_endpoint"]
    assert trial.status(directory, lab=lab)["status"] == "invalid"


async def test_elapsed_clock_cap_is_observed_and_human_time_is_null(fixture):
    lab, directory, _, _ = fixture
    state = await prepare(fixture)
    trial.start_trial(directory, lab=lab)
    ledger = trial._read(directory, lab)
    ledger["start_clock"]["monotonic_ns"] -= 901_000_000_000
    ledger["start_clock"]["wall_ns"] -= 901_000_000_000
    clocks._write(directory, ledger)
    repair(state)
    result = trial.verify_trial(directory, lab=lab)
    assert result["verified_endpoint"] and result["trial_status"] == "capped"
    assert trial.status(directory, lab=lab)["unmeasured"]["human_minutes_saved"] is None


async def test_broad_roots_track_an_added_production_file(fixture):
    lab, directory, _, _ = fixture
    state = await prepare(fixture)
    trial.start_trial(directory, lab=lab)
    extra = Path(state["case_dir"]) / "src/helper.py"
    extra.write_text("VALUE = 3\n")
    repair(state)
    assert trial.verify_trial(directory, lab=lab)["verified_endpoint"]
    assert "src/helper.py" in trial._read(directory, lab)["final_source_sha256"]


async def test_actual_tiny_protected_fixtures_fail_then_repair_passes(fixture):
    lab, directory, _, _ = fixture
    lab.execute = True
    state = await prepare(fixture)
    trial.start_trial(directory, lab=lab)
    assert not trial.verify_trial(directory, lab=lab)["verified_endpoint"]
    repair(state)
    assert trial.verify_trial(directory, lab=lab)["verified_endpoint"]


async def test_preparation_runtime_gate_precedes_reserved_case_access(fixture, monkeypatch):
    lab, directory, config, candidate = fixture
    class Wrong(Provider):
        async def ready(self):
            return {**READY, "n_ctx": 8192}
    monkeypatch.setattr(lab, "trial_case_ids", lambda: pytest.fail("reserved membership touched before runtime gate"))
    with pytest.raises(ValueError, match="16384"):
        await trial.prepare_trial_async(trial.plan_trials()[0], directory, agent_id="fake", config_path=config,
            candidate_freeze=candidate, provider_factory=Wrong, lab=lab)
    assert lab.prepared == []


async def test_truncated_initial_log_still_runs_the_timed_active_workflow(fixture, monkeypatch):
    lab, directory, _, _ = fixture
    await prepare(fixture, "verified_assistant")
    trial.start_trial(directory, lab=lab)
    monkeypatch.setattr(lab, "prepare_case_evidence", lambda *args: {"truncated": True, "source_context_omitted": "old log"})
    calls = []
    async def callback(evidence, **kwargs):
        calls.append(evidence)
        return {"state": "unverified", "attempts": []}
    await trial.hint_trial(directory, callback=callback, lab=lab)
    assert len(calls) == 1 and trial.status(directory, lab=lab)["status"] == "active"


async def test_verified_hint_application_ends_at_actual_protected_verification_without_extra_command(fixture):
    lab, directory, _, _ = fixture
    state = await prepare(fixture, "verified_assistant")
    trial.start_trial(directory, lab=lab)
    async def callback(evidence, **kwargs):
        repair(state)
        return {"state": "hypothesis_supported_by_tests", "apply": {"requested": True, "applied": True}, "attempts": []}
    report = await trial.hint_trial(directory, callback=callback, apply=True, lab=lab)
    assert report["trial_verification"]["verified_endpoint"] and report["trial_status"] == "solved"
    ledger = trial._read(directory, lab)
    assert len(ledger["verifications"]) == 1 and ledger["end_clock"] == ledger["verifications"][0]["end_clock"]
    assert clocks._elapsed(ledger["hints"][0]["end_clock"], ledger["verifications"][0]["start_clock"])[1] is None


async def test_skipped_protected_checks_cannot_finish_a_trial(fixture, monkeypatch):
    lab, directory, _, _ = fixture
    state = await prepare(fixture)
    trial.start_trial(directory, lab=lab)
    repair(state)
    validate = lab.validate_repair
    def skipped(project):
        result = validate(project)
        result["run"].update(checks_complete=False, test_execution_complete=False)
        return result
    monkeypatch.setattr(lab, "validate_repair", skipped)
    assert not trial.verify_trial(directory, lab=lab)["verified_endpoint"]
    assert trial.status(directory, lab=lab)["status"] == "active"


async def test_changed_protected_collection_cannot_finish_a_trial(fixture, monkeypatch):
    lab, directory, _, _ = fixture
    state = await prepare(fixture)
    trial.start_trial(directory, lab=lab)
    repair(state)
    validate = lab.validate_repair
    monkeypatch.setattr(lab, "validate_repair", lambda project: change_audit_collection(project, validate(project)))
    assert not trial.verify_trial(directory, lab=lab)["verified_endpoint"]
    assert trial.status(directory, lab=lab)["status"] == "active"


@pytest.mark.parametrize("report", [{}, {"state": "unavailable"}, {"state": "unverified", "baseline": {}},
    {"state": "unavailable", "error": {"type": "ValueError", "message": "Observed formatting fixture"}}])
def test_minimal_and_failed_hint_text_never_needs_optional_report_fields(report):
    text = trial._workflow_module_text(report)
    assert text.startswith("Decilist V5:") and text.endswith("\n")


@pytest.mark.parametrize("location", ["hypothesis", "generation_result"])
def test_no_safe_intervention_still_shows_useful_unverified_hypothesis(location):
    hypothesis = {"hypothesis": "The observed merge loses the second input before validation.", "replacements": []}
    attempt = {"generation": {"status": "needs_review", "result": hypothesis}}
    if location == "hypothesis":
        attempt["hypothesis"] = hypothesis
    report = {"state": "unverified", "reason": "no_safe_intervention", "attempts": [attempt]}
    text = trial._workflow_module_text(report)
    assert "no_safe_intervention" in text and hypothesis["hypothesis"] in text
    assert "Hypothesis:" in text and "protected passing repair" not in text.lower()


def test_real_cli_prepare_and_start_work_with_sync_wrapper_and_safe_hint_text(fixture, monkeypatch, capsys):
    lab, directory, config, candidate = fixture
    monkeypatch.setattr(trial, "_lab_api", lambda: lab)
    assert trial.main(["prepare", "--trial", str(directory), "--slot", "2", "--agent-id", "cli-fixture",
        "--candidate-freeze", str(candidate), "--model-config", str(config)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"
    assert trial.main(["start", "--trial", str(directory)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "active"
    async def callback(evidence, **kwargs):
        return {"state": "unavailable", "error": {"message": "Observed synthetic live failure"}}
    monkeypatch.setattr(trial, "workflow_bridge", callback)
    assert trial.main(["hint", "--trial", str(directory), "--format", "text"]) == 0
    output = capsys.readouterr().out
    assert "Observed synthetic live failure" in output and "Decilist V5: unavailable" in output
