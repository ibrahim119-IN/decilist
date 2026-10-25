"""Synthetic unit trials only; no blind agent or human trial is started here."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from benchmarks import triage_agent_trial as trial


class Clock:
    def __init__(self):
        self.seconds = 0
        self.boot = "synthetic-boot"

    def advance(self, seconds):
        self.seconds += seconds

    def read(self):
        wall = 1_700_000_000_000_000_000 + int(self.seconds * 1e9)
        return {"utc": datetime.fromtimestamp(wall / 1e9, timezone.utc).isoformat(),
                "wall_ns": wall, "monotonic_ns": 1_000_000_000 + int(self.seconds * 1e9),
                "host_sha256": "synthetic-host", "boot": {"kind": "linux_boot_id", "value": self.boot}}


class FakeLab:
    def __init__(self, root):
        self.LAB_ROOT = root
        root.mkdir()
        self.prepared = []
        self.run_count = 0
        self.passed = False
        self.protected = {}

    def prepare_case(self, case_id, directory):
        directory.mkdir()
        (directory / ".triage").mkdir()
        path = "packages/python/src/decilist/contracts.py"
        production = directory / path
        production.parent.mkdir(parents=True)
        production.write_text("# Synthetic production fixture\n")
        test = directory / ".triage/test_case.py"
        test.write_text("# Frozen synthetic test\n")
        self.protected[str(directory)] = hashlib.sha256(test.read_bytes()).hexdigest()
        manifest = {"case_id": case_id, "project": "synthetic", "editable_paths": [path],
                    "protected_sha256": {".triage/test_case.py": self.protected[str(directory)]},
                    "test_command": ["python", "-m", "pytest", ".triage/test_case.py"]}
        (directory / ".triage/manifest.json").write_text(json.dumps(manifest))
        self.prepared.append(case_id)
        return manifest

    def run_case(self, directory):
        self.run_count += 1
        manifest = json.loads((directory / ".triage/manifest.json").read_text())
        source = {path: trial._digest(directory / path) for path in manifest["editable_paths"]}
        intact = trial._digest(directory / ".triage/test_case.py") == self.protected[str(directory)]
        output = "FAILED synthetic protected check\n" if not self.passed else "3 passed\n"
        return {"exit_code": 0 if self.passed else 1, "stdout": output,
                "run_sha256": hashlib.sha256(output.encode()).hexdigest(),
                "protected_files_ok": intact, "source_stable": True, "timed_out": False,
                "command": ["python", "-m", "pytest", ".triage/test_case.py"],
                "elapsed_ms": 5, "editable_sha256_after": source}

    def validate_repair(self, directory):
        run = self.run_case(directory)
        return {"passed": self.passed and run["protected_files_ok"], "integrity_ok": run["protected_files_ok"], "run": run}

    def prepare_case_evidence(self, directory, observed):
        assert observed["run_sha256"] == hashlib.sha256(observed["stdout"].encode()).hexdigest()
        return {"schema_version": 1, "log": observed["stdout"], "exit_code": observed["exit_code"], "references": []}


@pytest.fixture
def fixture_trial(tmp_path, monkeypatch):
    lab = FakeLab(tmp_path / "fresh-lab")
    clock = Clock()
    monkeypatch.setattr(trial, "_clock", clock.read)
    return lab, clock, lab.LAB_ROOT / "agent-case"


def prepare(fixture, condition="normal_tools", cap=900):
    lab, _, directory = fixture
    planned = next(item for item in trial.assignment("A", 0) if item["condition"] == condition)
    return trial.prepare_trial(planned["case_id"], condition, directory, agent_id="synthetic-agent",
                               planned_assignment=planned, cap_seconds=cap, lab=lab)


def test_counterbalance_uses_distinct_cases_and_balances_conditions():
    for pair in trial.PAIRS:
        orders = [trial.assignment(pair, value) for value in range(4)]
        assert len({row["case_id"] for row in orders[0]}) == 2
        assert all({row["condition"] for row in order} == trial.CONDITIONS for order in orders)
        assert sum(order[0]["condition"] == "normal_tools" for order in orders) == 2
        assert sum(order[0]["case_id"] == trial.PAIRS[pair][0] for order in orders) == 2


def test_prepare_has_source_hashes_but_no_agent_or_human_elapsed(fixture_trial):
    lab, _, directory = fixture_trial
    result = prepare(fixture_trial)
    assert result["run_type"] == "agent-run"
    assert result["agent_settings"] == {"model": "gpt-6.1-sol", "reasoning_effort": "xhigh"}
    assert result["start_clock"] is result["agent_elapsed_seconds"] is None
    assert all(value is None for value in result["unmeasured"].values())
    assert result["original_buggy_source_sha256"]
    assert result["baseline_run"]["exit_code"] == 1
    assert not (directory / "case/trial.json").exists()
    assert "expected_route" not in result and "reference_repair" not in result
    assert lab.run_count == 1


def test_tool_brackets_capture_elapsed_without_executing_text(fixture_trial):
    lab, clock, directory = fixture_trial
    prepare(fixture_trial)
    trial.start_trial(directory, lab=lab)
    identifier = trial.begin_attempt(directory, "never-execute --destructive", lab=lab)
    clock.advance(3)
    attempt = trial.end_attempt(directory, identifier, exit_code=1, observation="Observed tool failure", lab=lab)
    assert attempt["elapsed_seconds"] == 3
    assert attempt["executed_by_runner"] is False and attempt["exit_code"] == 1
    assert lab.run_count == 1
    with pytest.raises(trial.TrialError, match="restarted"):
        trial.start_trial(directory, lab=lab)


def test_failed_and_verified_repairs_are_separate_actual_checks(fixture_trial):
    lab, clock, directory = fixture_trial
    prepare(fixture_trial)
    trial.start_trial(directory, lab=lab)
    clock.advance(2)
    assert not trial.verify_trial(directory, lab=lab)["verified_endpoint"]
    assert trial.status(directory, lab=lab)["status"] == "active"
    lab.passed = True
    clock.advance(5)
    assert trial.verify_trial(directory, lab=lab)["verified_endpoint"]
    result = trial.status(directory, lab=lab)
    assert result["status"] == "solved" and result["verified_repair"]
    assert result["agent_elapsed_seconds"] == 7 and result["failed_verifications"] == 1
    assert result["unmeasured"]["human_minutes_saved"] is None


def test_protected_test_changes_make_trial_invalid(fixture_trial):
    lab, _, directory = fixture_trial
    state = prepare(fixture_trial)
    trial.start_trial(directory, lab=lab)
    (Path(state["case_dir"]) / ".triage/test_case.py").write_text("# Weakened test")
    lab.passed = True
    assert not trial.verify_trial(directory, lab=lab)["verified_endpoint"]
    result = trial.status(directory, lab=lab)
    assert result["status"] == "invalid" and result["verified_repair"] is False


def test_late_valid_repair_is_capped_not_counted_within_limit(fixture_trial):
    lab, clock, directory = fixture_trial
    prepare(fixture_trial, cap=2)
    trial.start_trial(directory, lab=lab)
    clock.advance(3)
    lab.passed = True
    trial.verify_trial(directory, lab=lab)
    result = trial.status(directory, lab=lab)
    assert result["status"] == "capped" and result["verified_repair"]
    assert result["agent_elapsed_seconds"] == 3


@pytest.mark.parametrize("mode", ["reboot", "negative"])
def test_reboot_or_negative_interval_cannot_create_a_duration(fixture_trial, mode):
    lab, clock, directory = fixture_trial
    prepare(fixture_trial)
    trial.start_trial(directory, lab=lab)
    if mode == "reboot":
        clock.boot = "different-boot"
    else:
        clock.advance(-2)
    lab.passed = True
    trial.verify_trial(directory, lab=lab)
    result = trial.status(directory, lab=lab)
    assert result["status"] == "invalid" and result["agent_elapsed_seconds"] is None
    assert result["timing_issue"]


async def test_explicit_hint_version_and_failures_remain_recorded(fixture_trial):
    lab, clock, directory = fixture_trial
    prepare(fixture_trial, condition="decilist_hint")
    trial.start_trial(directory, lab=lab)
    async def hint(evidence, *, url, project):
        assert evidence["exit_code"] == 1 and "expected_route" not in evidence
        assert project == directory / "case"
        clock.advance(2)
        return {"status": "needs_review", "route": "manual_review", "confidence": None,
                "runtime": {"provider": "explicit-v2-test"}}
    report = await trial.hint_trial(directory, callback=hint, hint_version="v2-test", lab=lab)
    assert report["confidence"] is None
    result = trial.status(directory, lab=lab)
    assert result["hint_requests"] == 1 and result["hint_seconds"] == 2
    async def failing(evidence, *, url, project):
        clock.advance(1)
        raise RuntimeError("Observed service failure")
    with pytest.raises(RuntimeError):
        await trial.hint_trial(directory, callback=failing, hint_version="v2-test", lab=lab)
    recorded = trial._read(directory)["hints"]
    assert recorded[-1]["status"] == "error" and recorded[-1]["elapsed_seconds"] == 1
    assert recorded[0]["version"] == "v2-test" and recorded[0]["source_stable"]
    assert recorded[0]["case_source_stable"] is True
    assert trial.status(directory, lab=lab)["hint_seconds"] == 3


async def test_changed_source_requires_explicit_fresh_run_and_records_its_cost(fixture_trial):
    lab, _, directory = fixture_trial
    prepared = prepare(fixture_trial, condition="decilist_hint")
    trial.start_trial(directory, lab=lab)
    production = Path(prepared["case_dir"]) / prepared["editable_paths"][0]
    production.write_text("# Changed synthetic production\n")
    calls = []
    async def hint(evidence, *, url, project):
        calls.append(project)
        return {"status": "needs_review", "route": "manual_review", "source": "facts",
                "assistant_version": "triage-v2.0", "specification_sha256": "synthetic-spec"}
    with pytest.raises(trial.TrialError, match="explicit fresh"):
        await trial.hint_trial(directory, callback=hint, hint_version="v2-test", lab=lab)
    assert calls == []
    await trial.hint_trial(directory, callback=hint, hint_version="v2-test", fresh_run=True, lab=lab)
    assert calls == [directory / "case"] and lab.run_count == 2
    recorded = trial._read(directory)["hints"][-1]
    assert recorded["fresh_run_requested"] and recorded["evidence_run"]["exit_code"] == 1
    assert recorded["assistant_source"] == "facts" and recorded["assistant_version"] == "triage-v2.0"
    assert trial.status(directory, lab=lab)["trusted_test_calls"] == 1


def test_real_host_clock_probe_is_self_consistent_without_starting_a_trial():
    before, after = trial._clock(), trial._clock()
    elapsed, issue = trial._elapsed(before, after)
    assert issue is None and elapsed >= 0


async def test_normal_arm_and_remote_endpoints_never_call_hint(fixture_trial):
    lab, _, directory = fixture_trial
    prepare(fixture_trial)
    trial.start_trial(directory, lab=lab)
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("No hint should run")
    with pytest.raises(trial.TrialError, match="normal-tools"):
        await trial.hint_trial(directory, callback=forbidden, hint_version="v2-test", lab=lab)
    with pytest.raises(ValueError, match="loopback"):
        await trial.hint_trial(directory, callback=forbidden, hint_version="v2-test", url="https://external.example", lab=lab)
    assert calls == []


def test_unsolved_and_incomplete_tool_work_are_not_dropped(fixture_trial):
    lab, clock, directory = fixture_trial
    prepare(fixture_trial)
    trial.start_trial(directory, lab=lab)
    trial.begin_attempt(directory, "Observed investigation still in progress", lab=lab)
    clock.advance(4)
    trial.stop_trial(directory, reason="unsolved", lab=lab)
    result = trial.status(directory, lab=lab)
    assert result["status"] == "unsolved" and result["incomplete_attempts"] == 1
    assert result["agent_elapsed_seconds"] == 4 and result["unmeasured"]["cost_usd"] is None
