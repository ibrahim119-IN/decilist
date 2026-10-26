import json
from pathlib import Path

import pytest

from benchmarks import report_triage_v6_trials as reporter
from benchmarks import triage_v6_trial as trial
from test_triage_v6_trial import prepare
from test_v6_measurement_support import make_fixture, Provider, abstain, applied, repair, change_audit_collection


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    value = make_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(trial, "_workflow_module", lambda: value.workflow)
    return value


async def record(fixture, slot=1, suffix="", *, solved=True):
    directory, state = await prepare(fixture, slot, suffix)
    trial.start_trial(directory, lab=fixture.lab)
    if state["treatment"] == "verified_assistant":
        report = await trial.hint_trial(directory, callback=applied if solved else abstain,
            provider_factory=Provider, lab=fixture.lab)
        if solved:
            assert report["trial_verification"]["verified_endpoint"]
            return directory
    action = trial.begin_attempt(directory, "actual synthetic work", phase="repair", lab=fixture.lab)
    if solved:
        repair(state)
    trial.end_attempt(directory, action, exit_code=0, lab=fixture.lab)
    if solved:
        assert trial.verify_trial(directory, lab=fixture.lab)["verified_endpoint"]
    else:
        trial.stop_trial(directory, reason="unsolved", lab=fixture.lab)
    return directory


async def test_strict_reporter_rechecks_all_eight_after_clock_without_ledger_edits(fixture):
    directories = [await record(fixture, slot) for slot in range(1, 9)]
    originals = [(directory / "trial.json").read_bytes() for directory in directories]
    before = len(fixture.lab.calls)
    report = reporter.build_report(directories, lab=fixture.lab)
    assert report["complete_unique_plan"] and all(slot["timed_success_confirmed"] for slot in report["slots"])
    assert len(fixture.lab.calls) == before + 8
    assert originals == [(directory / "trial.json").read_bytes() for directory in directories]
    assert all(slot["records"][0]["root_verification"]["outside_agent_clock"] for slot in report["slots"])
    assert len(report["paired_same_case_differences"]) == 4 and report["human_minutes_saved"] is None


async def test_missing_duplicate_and_failed_runs_are_not_dropped(fixture):
    first = await record(fixture, 1)
    duplicate = await record(fixture, 1, "-duplicate")
    failed = await record(fixture, 2, solved=False)
    report = reporter.build_report([first, duplicate, failed], lab=fixture.lab)
    assert report["slots"][0]["outcome"] == "duplicate" and report["slots"][1]["outcome"] == "unsolved"
    assert all(row["outcome"] == "missing" for row in report["slots"][2:])
    assert not report["complete_unique_plan"]


@pytest.mark.parametrize("tamper", ["source", "test", "hint", "profile", "collection"])
async def test_current_source_test_hint_profile_and_collection_drift_invalidates_timing(fixture, monkeypatch, tamper):
    directory = await record(fixture, 2)
    state = trial._read(directory, fixture.lab)
    if tamper == "source":
        (directory / "case/src/app.py").write_text("def add(a,b):\n return a+b+0\n")
    elif tamper == "test":
        (directory / "case/.triage/test_case.py").write_text("assert True\n")
    elif tamper == "hint":
        Path(state["hints"][0]["report_path"]).write_text('{"state":"unverified"}')
    elif tamper == "profile":
        fixture.profiles.write_text(fixture.profiles.read_text() + "\n")
    else:
        validate = fixture.lab.validate_repair
        monkeypatch.setattr(fixture.lab, "validate_repair", lambda project: change_audit_collection(project, validate(project)))
    row = reporter.build_report([directory], lab=fixture.lab)["slots"][1]
    assert row["outcome"] == "invalid" and not row["timed_success_confirmed"]


async def test_public_and_private_exports_are_new_and_source_diffs_remain_reviewable(fixture):
    directory = await record(fixture, 2)
    report = reporter.build_report([directory], lab=fixture.lab)
    assert "return a + b" in report["slots"][1]["records"][0]["source_audit"]["diff"]
    public, private = fixture.root / "trial.public.json", fixture.root / "trial.private.json"
    reporter.export_report(report, public_output=public, private_output=private)
    assert "private_full_records" not in public.read_text() and "private_full_records" in private.read_text()
    with pytest.raises(ValueError, match="never overwritten"):
        reporter.export_report(report, public_output=public, private_output=private)
