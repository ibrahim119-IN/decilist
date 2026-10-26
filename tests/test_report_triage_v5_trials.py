"""Reporter checks synthetic sessions and actual tiny checks, never reserved data."""
import json
from pathlib import Path

import pytest

from benchmarks import report_triage_v5_trials as reporting
from benchmarks import triage_v5_trial as trial
from benchmarks import triage_agent_trial as clocks
from test_triage_v5_trial import Lab, Provider, CANDIDATE, prepare, repair, change_audit_collection


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    lab = Lab(tmp_path / "v5-lab")
    config, candidate = tmp_path / "config.json", tmp_path / "candidate.json"
    config.write_text(json.dumps({"model": "fake", "expected_build": "fake-build"}))
    candidate.write_text(json.dumps(CANDIDATE))
    monkeypatch.setattr(trial, "verify_candidate", lambda *args, **kwargs: CANDIDATE)
    monkeypatch.setattr(reporting, "verify_candidate", lambda *args, **kwargs: CANDIDATE)
    monkeypatch.setattr(trial, "_provider", Provider)
    return lab, lab.LAB_ROOT / "agent-trials" / "slot", config, candidate


async def record(fixture, slot=1, *, suffix="", solve=True):
    lab, root, config, candidate = fixture
    directory = root.with_name("slot-" + str(slot) + suffix)
    state = await prepare((lab, directory, config, candidate), slot=slot)
    trial.start_trial(directory, lab=lab)
    if state["treatment"] == "verified_assistant":
        async def callback(evidence, **kwargs):
            return {"state": "unverified", "reason": "Observed synthetic abstention", "attempts": [],
                    "raw_protocol_trace": {"sentinel": "PRIVATE_TRACE"}}
        await trial.hint_trial(directory, callback=callback, lab=lab)
    action = trial.begin_attempt(directory, "synthetic actual repair action", phase="repair", lab=lab)
    if solve:
        repair(state)
    trial.end_attempt(directory, action, exit_code=0, lab=lab)
    if solve:
        assert trial.verify_trial(directory, lab=lab)["verified_endpoint"]
    else:
        trial.stop_trial(directory, reason="unsolved", lab=lab)
    return directory


async def test_complete_plan_retains_all_slots_and_root_rechecks_outside_clock(fixture):
    lab, _, _, _ = fixture
    directories = [await record(fixture, slot) for slot in range(1, 9)]
    terminal = [(path / "trial.json").read_bytes() for path in directories]
    count = len(lab.calls)
    report = reporting.build_report(directories, lab=lab)
    assert report["complete_unique_plan"] and report["observed_records"] == 8
    assert len(lab.calls) == count + 8
    assert all(row["timed_success_confirmed"] for row in report["slots"])
    assert all(row["records"][0]["root_verification"]["outside_agent_clock"] for row in report["slots"])
    assert all("return a + b" in row["records"][0]["source_audit"]["diff"] for row in report["slots"])
    assert terminal == [(path / "trial.json").read_bytes() for path in directories]
    assert len(report["paired_same_case_differences"]) == 4
    assert report["per_treatment"]["verified_assistant"]["confirmed_solved"] == 4
    assert report["human_minutes_saved"] is None


async def test_missing_duplicate_and_unsolved_records_stay_visible(fixture):
    lab, _, _, _ = fixture
    first = await record(fixture, 1)
    duplicate = await record(fixture, 1, suffix="-duplicate")
    failed = await record(fixture, 2, solve=False)
    report = reporting.build_report([first, duplicate, failed], lab=lab)
    assert report["slots"][0]["outcome"] == "duplicate"
    assert report["slots"][1]["outcome"] == "unsolved"
    assert all(row["outcome"] == "missing" for row in report["slots"][2:])
    assert not report["complete_unique_plan"]
    assert report["paired_same_case_differences"][0]["difference_seconds_assistant_minus_normal"] is None


@pytest.mark.parametrize("tamper,issue", [
    ("source", "source_changed_after_timed_terminal_or_missing_hashes"),
    ("test", "protected_tests_or_configuration_changed"),
    ("hint", "hint_report_hash_changed_or_creation_hash_missing"),
    ("phase", "recorded_phase_precedes_initial_hint_or_order_unverifiable"),
    ("clock", "agent_clock_invalid_or_elapsed_disagrees"),
    ("initial", "original_source_snapshot_changed_or_missing"),
])
async def test_post_trial_and_recorded_protocol_drift_invalidates_timing(fixture, tamper, issue):
    lab, _, _, _ = fixture
    directory = await record(fixture, 2)
    state = trial._read(directory, lab)
    if tamper == "source":
        (directory / "case/src/app.py").write_text("def add(a,b):\n return a+b+0\n")
    elif tamper == "test":
        (directory / "case/.triage/test_case.py").write_text("assert True\n")
    elif tamper == "hint":
        Path(state["hints"][0]["report_path"]).write_text('{"state":"unverified","altered":true}')
    elif tamper == "phase":
        state["attempts"][0]["start_clock"] = state["start_clock"]
        clocks._write(directory, state)
    elif tamper == "clock":
        state["agent_elapsed_seconds"] += 20
        clocks._write(directory, state)
    elif tamper == "initial":
        (directory / "initial-source/src/app.py").write_text("# mutated audit snapshot\n")
    row = reporting.build_report([directory], lab=lab)["slots"][1]
    assert row["outcome"] == "invalid" and not row["timed_success_confirmed"]
    assert issue in row["records"][0]["issues"]


async def test_actual_tiny_root_validation_reruns_and_detects_failed_repair(fixture):
    lab, _, _, _ = fixture
    lab.execute = True
    directory = await record(fixture)
    report = reporting.build_report([directory], lab=lab)
    assert report["slots"][0]["timed_success_confirmed"]
    (directory / "case/src/app.py").write_text("def add(a,b):\n return a-b\n")
    report = reporting.build_report([directory], lab=lab)
    assert not report["slots"][0]["timed_success_confirmed"]
    assert not report["slots"][0]["records"][0]["root_verification"]["repair_reproduced"]


async def test_root_exception_and_mutation_never_credit_success(fixture):
    lab, _, _, _ = fixture
    directory = await record(fixture)
    lab.error = True
    result = reporting.build_report([directory], lab=lab)["slots"][0]
    assert result["outcome"] == "invalid" and "root_verification_failed" in result["records"][0]["issues"]
    lab.error, lab.mutate_during_validation = False, True
    result = reporting.build_report([directory], lab=lab)["slots"][0]
    assert result["outcome"] == "invalid"
    assert "source_or_tests_changed_during_root_verification" in result["records"][0]["issues"]


async def test_write_once_public_redacted_and_private_full_artifacts(fixture, tmp_path):
    lab, _, _, _ = fixture
    directory = await record(fixture, 2)
    report = reporting.build_report([directory], lab=lab)
    public, private = tmp_path / "public.json", tmp_path / "private.json"
    reporting.export_report(report, public_output=public, private_output=private)
    visible = public.read_text()
    assert "PRIVATE_TRACE" not in visible and "private_full_records" not in visible
    assert "PRIVATE_TRACE" in private.read_text()
    assert str(directory) not in visible
    with pytest.raises(ValueError, match="never overwritten"):
        reporting.export_report(report, public_output=public, private_output=private)


async def test_source_citation_has_no_success_credit(fixture):
    lab, _, _, _ = fixture
    directory = await record(fixture, 2, solve=False)
    report = reporting.build_report([directory], lab=lab)
    assert not report["slots"][1]["timed_success_confirmed"]
    assert report["per_treatment"]["verified_assistant"]["confirmed_solved"] == 0


async def test_root_collection_drift_invalidates_an_otherwise_passing_trial(fixture, monkeypatch):
    lab, _, _, _ = fixture
    directory = await record(fixture)
    validate = lab.validate_repair
    monkeypatch.setattr(lab, "validate_repair", lambda project: change_audit_collection(project, validate(project)))
    slot = reporting.build_report([directory], lab=lab)["slots"][0]
    assert not slot["timed_success_confirmed"] and slot["outcome"] == "invalid"
    assert "root_protected_collection_changed" in slot["records"][0]["issues"]
