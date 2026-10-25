"""Protocol tests use synthetic clocks/temporary cases, never participant timings."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from benchmarks import triage_study as study


class Clock:
    def __init__(self):
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def now(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class FakeLab:
    """No code execution: isolate the study state machine from trusted lab tests."""
    def __init__(self, root):
        self.LAB_ROOT = root
        root.mkdir()
        self.prepared = []
        self.run_calls = []
        self.validation_calls = []
        self.passed = False
        self.integrity_ok = True

    def prepare_case(self, case_id, destination):
        destination.mkdir()
        (destination / ".triage").mkdir()
        source = destination / "packages/python/src/decilist/engine.py"
        source.parent.mkdir(parents=True)
        source.write_text("# Temporary fixture production file\n", encoding="utf-8")
        protected = destination / ".triage/test_case.py"
        protected.write_text("# Frozen fixture test\n", encoding="utf-8")
        manifest = {
            "schema_version": 1, "case_id": case_id, "base_revision": "frozen-fixture",
            "editable_paths": ["packages/python/src/decilist/engine.py"],
            "protected_sha256": {".triage/test_case.py": hashlib.sha256(protected.read_bytes()).hexdigest()},
            "test_command": ["python", "-m", "pytest", ".triage/test_case.py"],
        }
        (destination / ".triage/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.prepared.append(case_id)
        return {**manifest, "case_dir": str(destination)}

    def run_case(self, case_dir, *, exit_code=1):
        self.run_calls.append(case_dir)
        folder = case_dir / ".triage/runs"
        folder.mkdir(exist_ok=True)
        output = "FAILED .triage/test_case.py::test_contract - AssertionError\n"
        path = folder / f"run-{len(self.run_calls)}.log"
        path.write_text(output, encoding="utf-8")
        return {
            "exit_code": exit_code, "elapsed_ms": 2.5, "stdout": output,
            "log_path": str(path), "command": ["python", "-m", "pytest", ".triage/test_case.py"],
            "protected_files_ok": True, "source_stable": True,
            "run_sha256": hashlib.sha256(output.encode()).hexdigest(), "timed_out": False,
        }

    def validate_repair(self, case_dir):
        self.validation_calls.append(case_dir)
        expected = json.loads((case_dir / ".triage/manifest.json").read_text())["protected_sha256"]
        intact = all(hashlib.sha256((case_dir / path).read_bytes()).hexdigest() == digest for path, digest in expected.items())
        return {
            "passed": self.passed and self.integrity_ok and intact,
            "integrity_ok": self.integrity_ok and intact,
            "run": self.run_case(case_dir, exit_code=0 if self.passed else 1) if intact else None,
        }

    def prepare_case_evidence(self, case_dir, observed):
        assert observed["run_sha256"] == hashlib.sha256(observed["stdout"].encode()).hexdigest()
        self.evidence_case = case_dir
        return {
            "schema_version": 1, "log": observed["stdout"], "exit_code": observed["exit_code"],
            "references": [{"path": ".triage/test_case.py", "line": 1, "excerpt": "Frozen assertion context"}],
        }


@pytest.fixture
def protocol(tmp_path, monkeypatch):
    lab = FakeLab(tmp_path / "lab")
    clock = Clock()
    monkeypatch.setattr(study, "_now", clock.now)
    directory = lab.LAB_ROOT / "session"
    return lab, clock, directory


def test_create_prepares_both_logs_without_human_times_or_oracle(protocol):
    lab, _, directory = protocol
    result = study.create_session(directory, lab=lab)
    assert result["status"] == "ready"
    assert lab.prepared == ["t05", "t06"]
    assert len(lab.run_calls) == 2
    for trial in result["trials"]:
        assert trial["status"] == "ready"
        assert trial["started_at"] is trial["ended_at"] is None
        assert trial["human_elapsed_seconds"] is trial["wall_elapsed_seconds"] is None
        assert trial["active_intervals"] == trial["hint_requests"] == []
        assert Path(trial["baseline_log"]).read_text() == "FAILED .triage/test_case.py::test_contract - AssertionError\n"
        assert "expected_route" not in trial and "reference_repair" not in trial
    observed = study.status(directory, lab=lab)
    assert all(item["human_elapsed_seconds"] is item["hint_seconds"] is None for item in observed["trials"])


def test_four_sequences_balance_condition_order_and_case_assignment(protocol):
    lab, _, directory = protocol
    assignments = []
    for sequence in range(4):
        state = study.create_session(directory.with_name(f"sequence-{sequence}"), sequence=sequence, lab=lab)
        assignments.append(tuple((t["condition"], t["case_id"]) for t in state["trials"]))
    assert len(set(assignments)) == 4
    for number in (0, 1):
        assert sum(pair[number][0] == "normal" for pair in assignments) == 2
        assert sum(pair[number][1] == "t05" for pair in assignments) == 2


def test_verified_endpoint_requires_real_validation_and_preserves_interruptions(protocol):
    lab, clock, directory = protocol
    study.create_session(directory, lab=lab)
    study.start_trial(directory, lab=lab)
    clock.advance(10)
    study.interrupt_trial(directory, reason="An unrelated interruption", lab=lab)
    clock.advance(50)
    study.resume_trial(directory, lab=lab)
    clock.advance(20)
    failed = study.verify_trial(directory, lab=lab)
    assert failed["status"] == "active" and not failed["verified_repair"]
    assert len(lab.validation_calls) == 1
    lab.passed = True
    clock.advance(5)
    solved = study.verify_trial(directory, lab=lab)
    assert solved["status"] == "solved" and solved["verified_repair"]
    assert solved["human_elapsed_seconds"] == 35
    assert solved["wall_elapsed_seconds"] == 85
    assert solved["interruptions"][0]["reason"] == "An unrelated interruption"
    assert solved["commands"][-1]["command_origin"] == "trusted_lab"
    assert len(lab.validation_calls) == 2


def test_modified_test_cannot_be_a_solved_endpoint(protocol):
    lab, clock, directory = protocol
    study.create_session(directory, lab=lab)
    trial = study.start_trial(directory, lab=lab)
    (Path(trial["case_dir"]) / ".triage/test_case.py").write_text("# Changed assertions\n")
    lab.passed = True
    clock.advance(1)
    result = study.verify_trial(directory, lab=lab)
    assert result["status"] == "invalid"
    assert result["verified_repair"] is False
    assert result["verifications"][-1]["integrity_ok"] is False


def test_cap_is_recorded_even_when_repair_passes_late(protocol):
    lab, clock, directory = protocol
    study.create_session(directory, cap_seconds=10, lab=lab)
    study.start_trial(directory, lab=lab)
    clock.advance(11)
    lab.passed = True
    result = study.verify_trial(directory, lab=lab)
    assert result["status"] == "capped"
    assert result["verified_repair"] is True
    assert result["human_elapsed_seconds"] == 11


def test_unsolved_and_second_trial_do_not_reuse_first_clock(protocol):
    lab, clock, directory = protocol
    study.create_session(directory, lab=lab)
    study.start_trial(directory, lab=lab)
    clock.advance(12)
    first = study.stop_trial(directory, reason="unsolved", lab=lab)
    assert first["status"] == "unsolved"
    clock.advance(100)
    second = study.start_trial(directory, lab=lab)
    assert second["number"] == 2 and study._elapsed(second, study._stamp()) == (0, 0)
    study.stop_trial(directory, reason="withdrawn", lab=lab)
    assert study.status(directory, lab=lab)["status"] == "completed"


def test_reported_command_is_never_executed(protocol):
    lab, _, directory = protocol
    study.create_session(directory, lab=lab)
    study.start_trial(directory, lab=lab)
    result = study.record_command(directory, command="do-not-execute-this-command --delete", lab=lab)
    assert result["commands"][-1]["command_origin"] == "participant_reported"
    assert result["commands"][-1]["executed_by_study"] is False
    assert len(lab.run_calls) == 2
    noted = study.record_tool(directory, tool="Existing editor and source search", lab=lab)
    assert noted["tool_notes"][-1]["origin"] == "participant_reported"
    assert len(lab.run_calls) == 2


@pytest.mark.parametrize("url", [
    "https://external.example", "http://localhost.external.example", "http://127.0.0.1@external.example",
    "file:///tmp/model", "http://localhost:8765/path", "http://localhost:8765?token=secret",
])
async def test_remote_or_malformed_hint_endpoint_rejected_before_evidence(protocol, monkeypatch, url):
    lab, _, directory = protocol
    called = []
    async def never(*args):
        called.append(args)
        raise AssertionError("No evidence may be sent")
    monkeypatch.setattr(study, "_get_hint", never)
    with pytest.raises(study.StudyError):
        await study.request_hint(directory, url=url, lab=lab)
    assert called == [] and lab.prepared == []


@pytest.mark.parametrize("url", ["http://127.0.0.1:8765", "http://127.0.0.2:8765/", "http://[::1]:8765", "http://localhost:8765"])
def test_loopback_endpoint_forms(url):
    assert study.validate_loopback_url(url) == url.rstrip("/")


async def test_hint_is_only_for_hint_arm_and_records_actual_request_fields(protocol, monkeypatch):
    lab, clock, directory = protocol
    study.create_session(directory, sequence=1, lab=lab)
    study.start_trial(directory, lab=lab)
    expected = {"status": "suggested", "route": "decision_policy", "next_check": {"title": "Inspect policy", "steps": []}, "runtime": {"provider": "liquid", "providers": [{"model": "fixture", "revision": "test-revision"}]}}
    async def hint(*args):
        clock.advance(2)
        return expected
    monkeypatch.setattr(study, "_get_hint", hint)
    report = await study.request_hint(directory, lab=lab)
    assert report == expected
    _, state = study.load_session(directory, lab=lab)
    recorded = state["trials"][0]["hint_requests"][0]
    assert recorded["status"] == "suggested"
    assert recorded["finished_at"] != recorded["started_at"]
    assert recorded["elapsed_seconds"] >= 0
    assert recorded["runtime"] == expected["runtime"]
    assert recorded["provenance_stable"] is True
    assert recorded["provenance_before"]["diagnosis_contract_sha256"]
    assert recorded["provenance_before"]["source_sha256"]["decilist/triage.py"]
    assert json.loads(Path(recorded["report_path"]).read_text()) == expected
    study.stop_trial(directory, lab=lab)
    study.start_trial(directory, lab=lab)
    with pytest.raises(study.StudyError, match="normal-tools"):
        await study.request_hint(directory, lab=lab)


async def test_hint_uses_shared_lab_evidence_and_keeps_legitimate_test_references(protocol, monkeypatch):
    import decilist
    import decilist.triage
    lab, _, directory = protocol
    study.create_session(directory, sequence=1, lab=lab)
    study.start_trial(directory, lab=lab)
    class LocalClient:
        def __init__(self, base_url):
            assert base_url == "http://127.0.0.1:8765"
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
    async def diagnose(evidence, client):
        assert evidence["exit_code"] == 1
        assert evidence["references"][0]["path"] == ".triage/test_case.py"
        assert "expected_route" not in evidence and "reference_repair" not in evidence
        return {"status": "suggested", "route": "decision_policy"}
    monkeypatch.setattr(decilist, "Client", LocalClient)
    monkeypatch.setattr(decilist.triage, "diagnose", diagnose)
    report = await study.request_hint(directory, lab=lab)
    assert report["route"] == "decision_policy"
    assert lab.evidence_case == directory / "trial-1"


async def test_hint_records_mid_call_version_change_without_silent_comparability(protocol, monkeypatch):
    lab, _, directory = protocol
    study.create_session(directory, sequence=1, lab=lab)
    study.start_trial(directory, lab=lab)
    snapshots = iter([{"contract": "same", "source": "before"}, {"contract": "same", "source": "after"}])
    monkeypatch.setattr(study, "_hint_provenance", lambda: next(snapshots))
    async def hint(*args):
        return {"status": "needs_review", "route": "manual_review"}
    monkeypatch.setattr(study, "_get_hint", hint)
    await study.request_hint(directory, lab=lab)
    _, state = study.load_session(directory, lab=lab)
    entry = state["trials"][0]["hint_requests"][0]
    assert entry["status"] == "needs_review"
    assert entry["provenance_before"]["source"] == "before"
    assert entry["provenance_after"]["source"] == "after"
    assert entry["provenance_stable"] is False


def test_prepared_session_without_optional_provenance_remains_usable(protocol):
    lab, _, directory = protocol
    state = study.create_session(directory, lab=lab)
    del state["preparation_provenance"]
    del state["trials"][0]["tool_notes"]
    study._write(directory, state)
    assert study.status(directory, lab=lab)["status"] == "ready"
    study.start_trial(directory, lab=lab)
    noted = study.record_tool(directory, tool="Existing editor", lab=lab)
    assert noted["tool_notes"][0]["tool"] == "Existing editor"


def test_pending_hint_duration_is_unknown_rather_than_zero(protocol):
    lab, _, directory = protocol
    state = study.create_session(directory, lab=lab)
    state["trials"][1]["hint_requests"].append({"elapsed_seconds": None})
    study._write(directory, state)
    assert study.status(directory, lab=lab)["trials"][1]["hint_seconds"] is None


def test_original_logs_and_manifest_cannot_be_replaced(protocol):
    lab, _, directory = protocol
    state = study.create_session(directory, lab=lab)
    Path(state["trials"][0]["baseline_log"]).write_text("Different failure")
    with pytest.raises(study.StudyError, match="original failing log"):
        study.start_trial(directory, lab=lab)


def test_clock_regression_never_creates_a_duration_or_solved_time(protocol):
    lab, clock, directory = protocol
    study.create_session(directory, lab=lab)
    study.start_trial(directory, lab=lab)
    clock.advance(-10)
    lab.passed = True
    result = study.verify_trial(directory, lab=lab)
    assert result["status"] == "invalid"
    assert result["verified_repair"] is True
    assert result["human_elapsed_seconds"] is None and result["timing_valid"] is False


def test_existing_or_outside_directory_is_preserved(protocol, tmp_path):
    lab, _, directory = protocol
    directory.mkdir()
    (directory / "keep.txt").write_text("Existing work")
    with pytest.raises(study.StudyError, match="already exists"):
        study.create_session(directory, lab=lab)
    with pytest.raises(study.StudyError, match="children"):
        study.create_session(tmp_path / "outside", lab=lab)
    assert (directory / "keep.txt").read_text() == "Existing work"


def test_real_lab_pair_is_prepared_with_no_participant_clock(tmp_path, monkeypatch):
    from benchmarks import triage_lab
    monkeypatch.setattr(triage_lab, "LAB_ROOT", tmp_path / "real-lab")
    directory = triage_lab.LAB_ROOT / "prepared-only"
    result = study.create_session(directory, lab=triage_lab)
    assert result["status"] == "ready"
    assert all(item["started_at"] is None and item["human_elapsed_seconds"] is None for item in result["trials"])
    assert all(item["baseline_run"]["exit_code"] == 1 and item["baseline_run"]["source_stable"] for item in result["trials"])
    assert all(item["baseline_run"]["run_sha256"] for item in result["trials"])


def test_hint_cli_shows_the_same_evidence_and_status_as_normal_triage(monkeypatch, capsys):
    async def hint(*args, **kwargs):
        return {
            "status": "suggested", "route": "decision_policy", "confidence": 0.8,
            "next_check": {"title": "Inspect policy", "steps": ["Inspect the recorded threshold."]},
            "evidence": {
                "references": [{"path": "packages/python/src/decilist/engine.py", "line": 42}],
                "signals": [{"kind": "failure", "line": 4, "text": "AssertionError: observed failure"}],
            },
        }
    monkeypatch.setattr(study, "request_hint", hint)
    assert study.main(["hint"]) == 0
    output = capsys.readouterr().out
    assert "Triage status: suggested" in output
    assert "Route confidence: 80.0%" in output
    assert "Source evidence: packages/python/src/decilist/engine.py:42" in output
    assert "Observed failure" in output and "AssertionError: observed failure" in output
    assert "not a proven root cause" in output
    assert "generates no repair code or commands" in output
