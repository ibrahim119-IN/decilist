"""Post-trial reporter tests use synthetic ledgers and a nonexecuting fake lab."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from benchmarks import report_triage_v3_trials as reporting
from benchmarks import triage_v3_trial as trial
from benchmarks import triage_agent_trial as clocks


def clock(seconds):
    wall = 1_700_000_000_000_000_000 + int(seconds * 1e9)
    return {"utc": datetime.fromtimestamp(wall / 1e9, timezone.utc).isoformat(),
            "wall_ns": wall, "monotonic_ns": 1_000_000_000_000 + int(seconds * 1e9),
            "host_sha256": "synthetic-host", "boot": {"kind": "linux_boot_id", "value": "synthetic-boot"}}


class Lab:
    def __init__(self, root):
        self.LAB_ROOT = root
        (root / "agent-trials").mkdir(parents=True)
        self.calls = []
        self.protected = {}
        self.fail = set()
        self.error = set()
        self.mutate = set()

    def trial_case_ids(self):
        return ("r01", "r02")

    def get_case_role(self, case_id):
        assert case_id in self.trial_case_ids()
        return "trial_regression"

    def validate_repair(self, directory):
        self.calls.append(directory)
        if directory in self.error:
            raise RuntimeError("Observed synthetic validator failure")
        intact = reporting._digest(directory / ".triage/test_case.py") == self.protected[directory]
        if directory in self.mutate:
            (directory / "vendor/httpx/_urls.py").write_text("# Unexpected mutation during the fake root check\n")
        passed = intact and directory not in self.fail
        return {"passed": passed, "integrity_ok": intact, "run": {
            "exit_code": 0 if passed else 1, "protected_files_ok": intact,
            "source_stable": True, "timed_out": False, "elapsed_ms": 1,
            "command": ["python", "-m", "pytest", ".triage/test_case.py"],
            "stdout": "3 passed\n" if passed else "FAILED synthetic protected check\n",
        }}


def write_state(directory, state):
    (directory / "trial.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


def make_record(lab, planned, *, suffix="", outcome="solved", elapsed=100):
    directory = lab.LAB_ROOT / "agent-trials" / f"slot-{planned['slot']}{suffix}"
    case_dir = directory / "case"
    (case_dir / ".triage").mkdir(parents=True)
    source = case_dir / "vendor/httpx/_urls.py"
    source.parent.mkdir(parents=True)
    source.write_text("# Synthetic terminal production bytes\n")
    test = case_dir / ".triage/test_case.py"
    test.write_text("# Protected synthetic checks\n")
    lab.protected[case_dir] = reporting._digest(test)
    paths = ["vendor/httpx/_urls.py"]
    hashes = {paths[0]: reporting._digest(source)}
    manifest = {"case_id": planned["case_id"], "editable_paths": paths,
                "protected_sha256": {".triage/test_case.py": lab.protected[case_dir]}}
    (case_dir / ".triage/manifest.json").write_text(json.dumps(manifest))
    log = "FAILED synthetic protected check\n"
    (directory / "original-failure.log").write_bytes(log.encode())
    state = {
        "schema_version": 1, "run_type": "agent-run", "experiment": "triage-v3-repair",
        "agent_id": "synthetic-unit-agent", "agent_settings": dict(clocks.SETTINGS),
        "assignment": dict(planned), "case_id": planned["case_id"], "condition": planned["treatment"],
        "treatment": planned["treatment"], "case_dir": str(case_dir), "editable_paths": paths,
        "manifest_sha256": reporting._digest(case_dir / ".triage/manifest.json"),
        "plan_sha256": trial._plan_hash(), "cap_seconds": 900, "status": outcome,
        "start_clock": clock(0), "end_clock": clock(elapsed), "agent_elapsed_seconds": elapsed,
        "timing_issue": None, "verified_repair": outcome == "solved", "attempts": [],
        "original_buggy_source_sha256": {paths[0]: "b"*64}, "final_source_sha256": hashes,
        "baseline_run": {"run_sha256": hashlib.sha256(log.encode()).hexdigest()},
        "verifications": [], "hints": [],
    }
    if outcome == "solved":
        state["verifications"].append({"kind": "verify", "verified_endpoint": True,
            "start_clock": clock(elapsed-1), "end_clock": clock(elapsed),
            "validation": {"passed": True, "integrity_ok": True, "run": {
                "exit_code": 0, "protected_files_ok": True, "source_stable": True,
                "timed_out": False, "editable_sha256_after": hashes,
            }}})
    if planned["treatment"] != "normal_tools":
        folder = directory / "hints"
        folder.mkdir()
        report = {"status": "grounded_hypothesis" if planned["treatment"] == "qwen_diagnostic" else "suggested",
                  "source": planned["treatment"], "runtime": {"model": "stub-qwen-runtime"},
                  "provenance": {"model_path": r"C:\Users\private-unit\models\model.gguf"},
                  "arbitrary_path": "/unusual-private-root/session/source.py",
                  "raw_vocab_mask": [0, 1]*100}
        target = folder / "hint-1.json"
        target.write_text(json.dumps(report))
        state["hints"].append({"callback_invoked": True, "status": report["status"],
            "callback_source_stable": True, "case_source_stable": True,
            "start_clock": clock(1), "end_clock": clock(3), "elapsed_seconds": 2,
            "report_path": str(target), "report_sha256": reporting._digest(target), "version": "synthetic-v3"})
    write_state(directory, state)
    return directory, state


@pytest.fixture
def suite(tmp_path):
    lab = Lab(tmp_path / "triage-v3-lab")
    durations = {("r01", "normal_tools"): 100, ("r02", "normal_tools"): 200,
                 ("r01", "qwen_route"): 120, ("r02", "qwen_route"): 150,
                 ("r01", "qwen_action"): 90, ("r02", "qwen_action"): 160,
                 ("r01", "qwen_diagnostic"): 110, ("r02", "qwen_diagnostic"): 250}
    pairs = [make_record(lab, row, elapsed=durations[(row["case_id"], row["treatment"])]) for row in trial.plan_trials()]
    return lab, pairs


def test_all_eight_get_fresh_verification_and_descriptive_same_case_differences(suite):
    lab, pairs = suite
    original = {path: (path / "trial.json").read_bytes() for path, _ in pairs}
    report = reporting.build_report([path for path, _ in pairs], lab=lab)
    assert len(report["slots"]) == len(lab.calls) == 8 and report["complete_unique_plan"]
    assert all(row["outcome"] == "solved" and row["timed_success_confirmed"] for row in report["slots"])
    assert all(row["records"][0]["root_verification"]["outside_agent_clock"] for row in report["slots"])
    assert report["per_treatment"]["normal_tools"]["median_seconds_confirmed_solved"] == 150
    contrasts = report["paired_same_case_differences"]
    assert len(contrasts) == 6
    action_r01 = next(row for row in contrasts if row["case_id"] == "r01" and row["treatment"] == "qwen_action")
    assert action_r01["difference_seconds_treatment_minus_normal"] == -10
    assert report["human_minutes_saved"] is report["human_elapsed_seconds"] is None
    assert all((path / "trial.json").read_bytes() == original[path] for path, _ in pairs)


def test_missing_and_duplicate_assignments_are_retained_without_selecting_a_winner(suite):
    lab, pairs = suite
    duplicate, _ = make_record(lab, trial.plan_trials()[0], suffix="-duplicate", elapsed=1)
    report = reporting.build_report([path for path, _ in pairs[:-1]]+[duplicate], lab=lab)
    assert len(report["slots"]) == 8
    assert report["slots"][0]["outcome"] == "duplicate" and report["slots"][0]["record_count"] == 2
    assert report["slots"][0]["timed_success_confirmed"] is False
    assert report["slots"][7]["outcome"] == "missing"
    assert report["complete_unique_plan"] is False
    assert len(lab.calls) == 8


def test_changed_source_after_timed_verification_invalidates_timing_even_if_checks_pass(suite):
    lab, pairs = suite
    source = Path(pairs[0][1]["case_dir"]) / "vendor/httpx/_urls.py"
    source.write_text("# Different later production bytes\n")
    report = reporting.build_report([path for path, _ in pairs], lab=lab)
    row = report["slots"][0]
    assert row["outcome"] == "invalid" and not row["timed_success_confirmed"]
    assert row["records"][0]["root_verification"]["repair_reproduced"] is True
    assert row["records"][0]["source_matches_timed_verification"] is False
    assert "source_changed_after_timed_terminal_or_hashes_missing" in row["records"][0]["issues"]
    assert all(item["difference_seconds_treatment_minus_normal"] is None for item in report["paired_same_case_differences"] if item["case_id"] == "r01")


def test_integrity_failure_and_fresh_test_failure_never_reproduce_a_timed_success(suite):
    lab, pairs = suite
    test = Path(pairs[0][1]["case_dir"]) / ".triage/test_case.py"
    test.write_text("# Weakened synthetic test\n")
    lab.fail.add(Path(pairs[1][1]["case_dir"]))
    report = reporting.build_report([path for path, _ in pairs], lab=lab)
    assert report["slots"][0]["outcome"] == report["slots"][1]["outcome"] == "invalid"
    assert "fresh_root_integrity_failed" in report["slots"][0]["records"][0]["issues"]
    assert "timed_success_did_not_reproduce" in report["slots"][1]["records"][0]["issues"]
    assert len(lab.calls) == 8


def test_unsolved_remains_unsolved_when_post_trial_tests_happen_to_pass(suite):
    lab, pairs = suite
    path, state = pairs[0]
    state["status"], state["verified_repair"], state["verifications"] = "unsolved", False, []
    write_state(path, state)
    report = reporting.build_report([directory for directory, _ in pairs], lab=lab)
    row = report["slots"][0]
    assert row["outcome"] == "unsolved" and row["timing_eligible"]
    assert not row["timed_success_confirmed"] and row["agent_elapsed_seconds"] == 100
    assert row["records"][0]["root_verification"]["repair_reproduced"]
    assert report["per_treatment"]["normal_tools"]["confirmed_solved"] == 1


def test_invalid_assignments_and_malformed_ledgers_stay_in_report(suite):
    lab, pairs = suite
    path, state = pairs[0]
    state["treatment"] = "qwen_diagnostic"
    write_state(path, state)
    broken = lab.LAB_ROOT / "agent-trials/broken"
    broken.mkdir()
    (broken / "trial.json").write_text("{not-json}")
    report = reporting.build_report([directory for directory, _ in pairs]+[broken], lab=lab)
    assert report["observed_records"] == 9 and len(report["unassigned_records"]) == 1
    assert report["slots"][0]["outcome"] == "invalid" and not report["complete_unique_plan"]
    assert "case_or_treatment_changed" in report["slots"][0]["records"][0]["issues"]
    assert report["private_full_records"][-1]["raw_ledger_text"] == "{not-json}"


def test_root_verifier_error_is_retained_and_other_cases_continue(suite):
    lab, pairs = suite
    lab.error.add(Path(pairs[0][1]["case_dir"]))
    report = reporting.build_report([directory for directory, _ in pairs], lab=lab)
    first = report["slots"][0]["records"][0]
    assert len(lab.calls) == 8 and first["root_verification_attempted"]
    assert first["root_verification_error"]["type"] == "RuntimeError"
    assert first["root_verification"]["elapsed_seconds"] >= 0
    assert report["slots"][0]["outcome"] == "invalid"


def test_source_movement_during_fresh_check_is_not_misreported_as_readonly_success(suite):
    lab, pairs = suite
    lab.mutate.add(Path(pairs[0][1]["case_dir"]))
    report = reporting.build_report([directory for directory, _ in pairs], lab=lab)
    record = report["slots"][0]["records"][0]
    assert report["slots"][0]["outcome"] == "invalid"
    assert "source_changed_during_root_verification" in record["issues"]
    assert not record["root_verification"]["repair_reproduced"]


def test_bad_clock_and_missing_terminal_hashes_keep_invalid_records(suite):
    lab, pairs = suite
    path, state = pairs[0]
    state["end_clock"]["boot"]["value"] = "different-boot"
    write_state(path, state)
    second, second_state = pairs[1]
    del second_state["final_source_sha256"]
    write_state(second, second_state)
    report = reporting.build_report([directory for directory, _ in pairs], lab=lab)
    assert report["slots"][0]["outcome"] == report["slots"][1]["outcome"] == "invalid"
    assert report["slots"][0]["agent_elapsed_seconds"] is None
    assert report["slots"][0]["records"][0]["recorded_elapsed_seconds"] == 100
    assert len(lab.calls) == 8


@pytest.mark.parametrize("change", ["same_status_tamper", "missing_creation_hash"])
def test_hint_creation_hash_is_required_and_same_status_tampering_invalidates(suite, change):
    lab, pairs = suite
    directory, state = pairs[1]
    entry = state["hints"][0]
    target = Path(entry["report_path"])
    original = json.loads(target.read_text())
    if change == "same_status_tamper":
        altered = {**original, "source": "Different content with unchanged status"}
        assert altered["status"] == entry["status"]
        target.write_text(json.dumps(altered))
        expected = "hint_report_hash_changed"
    else:
        del entry["report_sha256"]
        write_state(directory, state)
        expected = "hint_report_creation_hash_missing"
    report = reporting.build_report([path for path, _ in pairs], lab=lab)
    row = report["slots"][1]
    assert row["outcome"] == "invalid" and not row["timed_success_confirmed"]
    assert expected in row["records"][0]["issues"]
    assert row["records"][0]["hints"][0]["report_hash_matches_creation"] is False
    assert row["records"][0]["root_verification"]["repair_reproduced"] is True


def test_model_summary_uses_runtime_then_decision_when_top_level_is_missing(suite):
    lab, pairs = suite
    directory, state = pairs[1]
    target = Path(state["hints"][0]["report_path"])
    body = json.loads(target.read_text())
    body.pop("runtime")
    body["decision"] = {"model": "stub-qwen-decision"}
    target.write_text(json.dumps(body))
    state["hints"][0]["report_sha256"] = reporting._digest(target)
    write_state(directory, state)
    report = reporting.build_report([path for path, _ in pairs], lab=lab)
    assert report["per_treatment"]["qwen_route"]["reported_models"] == ["stub-qwen-decision", "stub-qwen-runtime"]
    assert all(row["outcome"] == "solved" for row in report["slots"])
    assert reporting._reported_model({"model": "top", "runtime": {"model": "runtime"}}) == "top"


@pytest.mark.parametrize("count", [0, 2])
def test_assisted_run_requires_one_hint_and_retains_missing_or_extra_records(suite, count):
    lab, pairs = suite
    directory, state = pairs[1]
    initial = deepcopy(state["hints"][0])
    state["hints"] = [] if count == 0 else [initial, deepcopy(initial)]
    write_state(directory, state)
    report = reporting.build_report([path for path, _ in pairs], lab=lab)
    slot = report["slots"][1]
    record = slot["records"][0]
    assert slot["outcome"] == "invalid" and not slot["timed_success_confirmed"]
    assert "assisted_run_requires_exactly_one_initial_hint" in record["issues"]
    assert len(record["hints"]) == record["initial_hint_protocol"]["recorded_hint_records"] == count
    assert len(report["slots"]) == len(lab.calls) == 8
    assert report["private_full_records"][1]["state"]["hints"] == state["hints"]


def test_normal_tools_rejects_even_an_uncalled_error_hint_record(suite):
    lab, pairs = suite
    directory, state = pairs[0]
    state["hints"] = [{"status": "error", "callback_invoked": False,
                       "start_clock": clock(1), "end_clock": clock(2), "elapsed_seconds": 1}]
    write_state(directory, state)
    report = reporting.build_report([path for path, _ in pairs], lab=lab)
    record = report["slots"][0]["records"][0]
    assert report["slots"][0]["outcome"] == "invalid"
    assert "normal_tools_requires_zero_hint_records" in record["issues"]
    assert record["hints"][0]["entry"]["status"] == "error"


@pytest.mark.parametrize("tool,command", [("shell", "Read case source and original failure log"), ("edit", "Apply a production edit")])
def test_recorded_inspection_or_edit_before_initial_hint_finishes_is_invalid(suite, tool, command):
    lab, pairs = suite
    directory, state = pairs[1]
    attempt = {"tool": tool, "command": command, "start_clock": clock(2), "end_clock": clock(4)}
    state["attempts"] = [attempt]  # The initial hint ends at clock(3).
    write_state(directory, state)
    report = reporting.build_report([path for path, _ in pairs], lab=lab)
    record = report["slots"][1]["records"][0]
    assert report["slots"][1]["outcome"] == "invalid"
    assert "recorded_task_began_before_initial_hint_finished" in record["issues"]
    assert record["attempts"] == [attempt]
    assert record["initial_hint_protocol"]["unrecorded_reads_verifiable"] is False


@pytest.mark.parametrize("hint_status", ["needs_review", "error"])
def test_single_initial_abstention_or_error_is_still_the_assigned_treatment(suite, hint_status):
    lab, pairs = suite
    directory, state = pairs[1]
    entry = state["hints"][0]
    entry["status"] = hint_status
    if hint_status == "error":
        entry["report_path"], entry["report_sha256"] = None, None
    else:
        target = Path(entry["report_path"])
        body = json.loads(target.read_text())
        body["status"] = hint_status
        target.write_text(json.dumps(body))
        entry["report_sha256"] = reporting._digest(target)
    state["attempts"] = [{"tool": "shell", "command": "Read the allowed source",
                           "start_clock": clock(3), "end_clock": clock(4)}]
    write_state(directory, state)
    report = reporting.build_report([path for path, _ in pairs], lab=lab)
    record = report["slots"][1]["records"][0]
    assert report["slots"][1]["timed_success_confirmed"]
    assert record["hints"][0]["entry"]["status"] == hint_status
    assert record["initial_hint_protocol"]["recorded_hint_records"] == 1


def test_missing_task_start_clock_cannot_prove_inspection_order(suite):
    lab, pairs = suite
    directory, state = pairs[1]
    state["attempts"] = [{"tool": "edit", "command": "Observed edit without its start timestamp"}]
    write_state(directory, state)
    report = reporting.build_report([path for path, _ in pairs], lab=lab)
    assert report["slots"][1]["outcome"] == "invalid"
    assert "initial_hint_task_order_unverifiable" in report["slots"][1]["records"][0]["issues"]


def test_public_export_redacts_paths_omits_only_bulky_traces_and_private_keeps_full(suite, tmp_path):
    lab, pairs = suite
    report = reporting.build_report([directory for directory, _ in pairs], lab=lab)
    public, private = tmp_path / "public.json", tmp_path / "private.json"
    reporting.export_report(report, public_output=public, private_output=private)
    text = public.read_text()
    assert "private-unit" not in text and "/unusual-private-root" not in text
    public_hint = json.loads(text)["slots"][1]["records"][0]["hints"][0]["report"]
    assert "raw_vocab_mask" not in public_hint and public_hint["public_trace_omissions"][0]["field"] == "raw_vocab_mask"
    full_hint = json.loads(private.read_text())["private_full_records"][1]["hints"][0]["report"]
    assert full_hint["raw_vocab_mask"] == [0, 1]*100
    assert full_hint["provenance"]["model_path"].startswith("C:\\Users")
    assert b"\r\n" not in public.read_bytes() and b"\r\n" not in private.read_bytes()
    with pytest.raises(ValueError, match="existing evidence"):
        reporting.export_report(report, public_output=public, private_output=private)
    with pytest.raises(ValueError, match="outside every protected case"):
        reporting.export_report(report, public_output=Path(pairs[0][1]["case_dir"]) / "report.json", private_output=tmp_path / "other-private.json")


def test_cli_exports_all_fake_slots_and_never_overwrites_existing_evidence(suite, tmp_path, monkeypatch, capsys):
    lab, _ = suite
    monkeypatch.setattr(reporting.trial, "_lab_api", lambda: lab)
    public, private = tmp_path / "cli-public.json", tmp_path / "cli-private.json"
    arguments = ["--trials-root", str(lab.LAB_ROOT / "agent-trials"),
                 "--output", str(public), "--private-output", str(private)]
    assert reporting.main(arguments) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["expected_slots"] == summary["observed_records"] == 8
    assert summary["outcomes"] == {"solved": 8} and len(lab.calls) == 8
    before = public.read_bytes(), private.read_bytes()
    assert reporting.main(arguments) == 2
    assert (public.read_bytes(), private.read_bytes()) == before and len(lab.calls) == 8
