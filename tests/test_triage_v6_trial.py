import json
from pathlib import Path

import pytest

from benchmarks import triage_v6_trial as trial
from benchmarks import triage_agent_trial as clocks
from test_v6_measurement_support import make_fixture, candidate_file, Provider, abstain, applied, repair


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    value = make_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(trial, "_workflow_module", lambda: value.workflow)
    monkeypatch.setattr(trial, "_lab_api", lambda: value.lab)
    return value


async def prepare(fixture, slot=1, suffix=""):
    directory = fixture.lab.LAB_ROOT / "agent-trials" / ("slot-" + str(slot) + suffix)
    state = await trial.prepare_trial_async(trial.plan_trials()[slot - 1], directory, agent_id="synthetic-" + str(slot),
        candidate_freeze=candidate_file(fixture), profiles_path=fixture.profiles, provider_factory=Provider, lab=fixture.lab)
    return directory, state


def test_fixed_balanced_plan_preserves_four_pairs_and_settings():
    assert [(row["case_id"], row["treatment"]) for row in trial.plan_trials()] == [
        ("r01", "normal_tools"), ("r02", "verified_assistant"), ("r03", "normal_tools"),
        ("r04", "verified_assistant"), ("r01", "verified_assistant"), ("r02", "normal_tools"),
        ("r03", "verified_assistant"), ("r04", "normal_tools")]
    assert trial.CAP_SECONDS == 900 and clocks.SETTINGS == {"model": "gpt-6.1-sol", "reasoning_effort": "xhigh"}


async def test_preparation_demands_both_frozen_profiles_and_actual_stronger_mode_before_cases(fixture):
    directory, state = await prepare(fixture)
    assert state["model_profile"] == "stronger" and state["context_policy"] == "v6"
    assert state["preparation_runtime_preflight"]["generation_mode_proof"]["generation_profile"] == "qwen3_6_closed_thinking"
    assert state["retained_corpus_proof_required"] and state["start_clock"] is None
    assert fixture.lab.freeze_calls == [True] and not state["verified_repair"]


async def test_assisted_hint_is_first_inside_clock_and_verified_apply_finishes_in_same_command(fixture):
    directory, state = await prepare(fixture, 2)
    trial.start_trial(directory, lab=fixture.lab)
    with pytest.raises(trial.TrialError, match="initial live"):
        trial.begin_attempt(directory, "inspect source", lab=fixture.lab)
    with pytest.raises(trial.TrialError, match="initial live"):
        trial.verify_trial(directory, lab=fixture.lab)
    reply = await trial.hint_trial(directory, callback=applied, provider_factory=Provider, lab=fixture.lab)
    assert reply["trial_verification"]["verified_endpoint"] and reply["trial_status"] == "solved"
    ledger = trial._read(directory, fixture.lab)
    assert len(ledger["hints"]) == len(ledger["verifications"]) == 1
    assert clocks._elapsed(ledger["start_clock"], ledger["hints"][0]["start_clock"])[1] is None
    assert clocks._elapsed(ledger["hints"][0]["end_clock"], ledger["verifications"][0]["start_clock"])[1] is None
    assert ledger["end_clock"] == ledger["verifications"][0]["end_clock"]


async def test_failed_initial_assistance_stays_in_arm_then_manual_repair_can_finish(fixture):
    directory, state = await prepare(fixture, 2)
    trial.start_trial(directory, lab=fixture.lab)
    async def error(evidence, **kwargs):
        raise RuntimeError("observed synthetic local-generation failure")
    reply = await trial.hint_trial(directory, callback=error, provider_factory=Provider, lab=fixture.lab)
    assert reply["state"] == "unavailable" and trial.status(directory, lab=fixture.lab)["status"] == "active"
    action = trial.begin_attempt(directory, "actual synthetic manual repair", phase="repair", lab=fixture.lab)
    repair(state)
    trial.end_attempt(directory, action, exit_code=0, lab=fixture.lab)
    assert trial.verify_trial(directory, lab=fixture.lab)["verified_endpoint"]
    assert trial.status(directory, lab=fixture.lab)["treatment"] == "verified_assistant"
    with pytest.raises(trial.TrialError):
        await trial.hint_trial(directory, callback=applied, provider_factory=Provider, lab=fixture.lab)


async def test_normal_tools_never_receives_assistance_and_tracks_broad_root_changes(fixture):
    directory, state = await prepare(fixture, 1)
    trial.start_trial(directory, lab=fixture.lab)
    with pytest.raises(trial.TrialError, match="assisted arm"):
        await trial.hint_trial(directory, callback=applied, provider_factory=Provider, lab=fixture.lab)
    action = trial.begin_attempt(directory, "repair using ordinary tools", phase="repair", lab=fixture.lab)
    repair(state)
    (Path(state["case_dir"]) / "src/helper.py").write_text("VALUE = 1\n")
    trial.end_attempt(directory, action, exit_code=0, lab=fixture.lab)
    assert trial.verify_trial(directory, lab=fixture.lab)["verified_endpoint"]
    assert "src/helper.py" in trial._read(directory, fixture.lab)["final_source_sha256"]


async def test_wrong_context_or_runtime_mode_never_earns_assisted_success(fixture):
    directory, _ = await prepare(fixture, 2)
    trial.start_trial(directory, lab=fixture.lab)
    async def wrong(evidence, **kwargs):
        return {"state": "unverified", "context_policy": "v5", "attempts": []}
    report = await trial.hint_trial(directory, callback=wrong, provider_factory=Provider, lab=fixture.lab)
    assert report["state"] == "unavailable" and "context" in report["error"]["message"]
    ledger = trial._read(directory, fixture.lab)
    assert ledger["hints"][0]["status"] == "error" and not ledger["verified_repair"]


async def test_single_initial_apply_is_required_and_missing_mode_before_callback_invalidates(fixture):
    directory, _ = await prepare(fixture, 2)
    trial.start_trial(directory, lab=fixture.lab)
    with pytest.raises(trial.TrialError, match="--apply"):
        await trial.hint_trial(directory, callback=abstain, apply=False, provider_factory=Provider, lab=fixture.lab)
    class Wrong(Provider):
        async def apply_template(self, messages):
            self.generation_mode_proof = None
    report = await trial.hint_trial(directory, callback=abstain, provider_factory=Wrong, lab=fixture.lab)
    assert report["state"] == "unavailable" and trial.status(directory, lab=fixture.lab)["status"] == "invalid"


async def test_time_cap_and_unchanged_protected_checks_remain_required(fixture):
    directory, state = await prepare(fixture, 1)
    trial.start_trial(directory, lab=fixture.lab)
    repair(state)
    ledger = trial._read(directory, fixture.lab)
    ledger["start_clock"]["monotonic_ns"] -= 901_000_000_000
    ledger["start_clock"]["wall_ns"] -= 901_000_000_000
    clocks._write(directory, ledger)
    result = trial.verify_trial(directory, lab=fixture.lab)
    assert result["verified_endpoint"] and result["trial_status"] == "capped"
    assert trial.status(directory, lab=fixture.lab)["unmeasured"]["human_minutes_saved"] is None


async def test_tampering_with_tests_cannot_become_solved(fixture):
    directory, state = await prepare(fixture, 1)
    trial.start_trial(directory, lab=fixture.lab)
    repair(state)
    (Path(state["case_dir"]) / ".triage/test_case.py").write_text("assert True\n")
    assert not trial.verify_trial(directory, lab=fixture.lab)["verified_endpoint"]
    assert trial.status(directory, lab=fixture.lab)["status"] == "invalid"
