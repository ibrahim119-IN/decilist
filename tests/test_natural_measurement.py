"""Only synthetic stubs. No natural bridge import, sealed data, model or trial."""
from copy import deepcopy
import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from benchmarks import natural_candidate as candidate
from benchmarks import natural_trial as trial
from benchmarks import report_natural_trials as reporting
from benchmarks import triage_agent_trial as clocks
from decilist.triage_hypothesis_v6 import generation_profile_specification


def digest(data):
    return hashlib.sha256(data).hexdigest()


def source_hashes(project):
    return candidate.production_hashes(Path(project), ["src"])


def all_protected(project):
    project = Path(project)
    return {p.relative_to(project).as_posix(): digest(p.read_bytes()) for p in project.rglob("*") if p.is_file()
            and (not p.relative_to(project).as_posix().startswith("src/") or p.name == "LICENSE.md")}


class Bridge:
    """A made-up arithmetic package; it contains no actual transfer mechanism."""
    def __init__(self, root):
        self.ROOT = root
        self.identity = "1" * 64
        self.prepared, self.verifications, self.runs = [], [], []
        self.protected = {}
        self.skip = self.timeout = self.wrong_source = self.wrong_group = self.changed_collection = False
        self.proof_ok = True

    def verify_identity(self, expected):
        self.verifications.append(expected)
        return {"verified": self.proof_ok, "fixture_set_identity": self.identity,
            **{key: True for key in ("expected_identity_match", "protected_canonical_ok", "upstream_source_identities_ok",
                                   "proof_verified", "main_environment_unchanged")}}

    def prepare_case(self, case_id, destination):
        destination = Path(destination)
        (destination / "src").mkdir(parents=True)
        (destination / "tests").mkdir()
        (destination / "vendor").mkdir()
        (destination / "src/app.py").write_text("def add(a, b):\n    return a - b\n")
        (destination / "src/LICENSE.md").write_text("Synthetic copied BSD-style license fixture\n")
        (destination / "tests/test_checks.py").write_text("# three immutable synthetic checks\n")
        (destination / "vendor/LICENSE.md").write_text("Synthetic dependency license fixture\n")
        (destination / "pytest.ini").write_text("[pytest]\n")
        self.protected[str(destination)] = all_protected(destination)
        self.prepared.append(case_id)
        return {"case_id": case_id, "project_root": str(destination), "source_roots": ["src"],
            "allowed_production_roots": ["src"], "failing_tests": ["-q", "tests/test_checks.py::test_observed"],
            "regression_tests": ["-q", "tests/test_checks.py::test_existing_a", "tests/test_checks.py::test_existing_b"],
            "full_tests": ["-q", "tests"], "fixture_set_identity": self.identity,
            "protected_sha256": self.protected[str(destination)], "canonical_source_sha256": source_hashes(destination),
            "expected_check_count": 3, "participant_task": "Repair the observed Python behavior and preserve all checks."}

    def run_case(self, case_id, project, evidence, *, group="all", timeout_seconds=30):
        self.runs.append((case_id, str(project), str(evidence)))
        current = source_hashes(project)
        intact = self.protected[str(project)] == all_protected(project)
        passed = "return a + b" in (Path(project) / "src/app.py").read_text()
        code = 0 if passed else 1
        nodes = ["tests/test_checks.py::test_observed", "tests/test_checks.py::test_existing_a", "tests/test_checks.py::test_existing_b"]
        if self.changed_collection:
            nodes[-1] = "tests/test_checks.py::different_check"
        reports = [{"nodeid": name, "when": "call", "outcome": "passed" if passed or i else "failed"} for i, name in enumerate(nodes)]
        if self.skip:
            reports[-1]["outcome"] = "skipped"
        result = {"case_id": case_id, "group": "observed" if self.wrong_group else "all", "exit_code": None if self.timeout else code,
            "state": "timeout" if self.timeout else "passed" if passed else "failed", "stdout": "Synthetic retained test output\n",
            "stderr": "", "run_sha256": "f" * 64, "protected_files_ok": intact, "source_stable": True,
            "timed_out": self.timeout, "checks_complete": True, "test_execution_complete": True,
            "expected_check_count": 3, "collection_sha256": digest(json.dumps(nodes, sort_keys=True).encode()),
            "editable_sha256_before": {} if self.wrong_source else current, "editable_sha256_after": current,
            "execution_audit": {"collected": nodes, "reports": reports, "collection_reports": [], "collection_errors": []},
            "fixture_set_identity": self.identity, "evidence_path": str(Path(evidence) / "synthetic.json")}
        return result

    def validate_repair(self, case_id, project, evidence, *, timeout_seconds=30):
        run = self.run_case(case_id, project, evidence)
        accepted = run["exit_code"] == 0 and run["protected_files_ok"] and not self.timeout
        return {"passed": accepted, "accepted": accepted, "integrity_ok": run["protected_files_ok"], "run": run}


def config_record(config):
    profile = generation_profile_specification(config["generation_profile"], config["expected_chat_template_sha256"])
    ready = {"model": config["model"], "build_info": config["expected_build"], "n_ctx": 16384,
             "chat_template_sha256": config["expected_chat_template_sha256"]}
    proof = {"schema_version": 1, "status": "verified", "generation_profile": config["generation_profile"],
        "mode": profile["mode"], "expected_chat_template_sha256": config["expected_chat_template_sha256"],
        "actual_chat_template_sha256": config["expected_chat_template_sha256"], "expected_prompt_suffix": profile["prompt_suffix"],
        "actual_prompt_suffix": profile["prompt_suffix"], "prompt_suffix_sha256": profile["prompt_suffix_sha256"],
        "prompt_sha256": "f" * 64, "suffix_matches": True, "per_request_template_override": False,
        "reasoning_capability_evaluated": False, "build_info": config["expected_build"], "n_ctx": 16384, "model": config["model"]}
    return ready, proof


class Provider:
    def __init__(self, config, endpoint):
        self.config, self.endpoint = config, endpoint
        self.generation_mode_proof = None

    async def ready(self):
        return config_record(self.config)[0]

    async def apply_template(self, messages):
        self.generation_mode_proof = config_record(self.config)[1]
        return "Synthetic native profile probe"

    async def complete(self, *args, **kwargs):
        raise AssertionError("No model calls are permitted in draft tests")

    async def aclose(self):
        pass


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    repo = tmp_path / "fake-active-repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "packages/python/src/decilist").mkdir(parents=True)
    (repo / "packages/python/src/decilist/unit.py").write_text("VERSION = 1\n")
    (repo / "docs/triage-natural-transfer-protocol.md").write_text("Six fixed synthetic unit slots; no real cases.\n")
    monkeypatch.setattr(candidate, "REPO_ROOT", repo)
    bridge = Bridge(tmp_path / "fake-natural-research")
    safe_code = tmp_path / "safe-bridge.py"
    safe_code.write_text("# Synthetic safe bridge; never imported by candidate freeze\n")
    safe = {"schema_version": 1, "fixture_set_identity": bridge.identity, "strict_cases": 3,
        "bridge_code_sha256": digest(safe_code.read_bytes()), "frozen_support_sha256": {},
        "descriptor": {"strict_case_ids": list(candidate.CASE_IDS), "allowed_production_roots": ["src"],
                       "cases": [{"id": case, "all_count": 3} for case in candidate.CASE_IDS]}}
    safe_path = tmp_path / "bridge-freeze-safe.json"
    safe_path.write_text(json.dumps(safe))
    config = {"model": "synthetic-stronger", "expected_build": "b11889-d854d98ba",
        "generation_profile": "qwen3_6_closed_thinking", "expected_chat_template_sha256": "b" * 64}
    config_path = tmp_path / "model.json"
    config_path.write_text(json.dumps(config))
    ready, proof = config_record(config)
    profiles = tmp_path / "natural-profile.json"
    profiles.write_text(json.dumps({"schema_version": 1, "bridge_freeze_path": str(safe_path), "bridge_path": str(safe_code),
        "profiles": {"stronger": {"config_path": str(config_path), "endpoint": "http://127.0.0.1:8792",
            "protocol_metadata": {"ready_identity": ready, "generation_mode_proof": proof,
                "model_artifact_sha256": candidate.EXPECTED_WEIGHTS["stronger"], "runtime_binary_sha256": "c" * 64}}}}))
    lab = candidate.NaturalLab(bridge, expected_identity=bridge.identity, expected_counts={case: 3 for case in candidate.CASE_IDS})
    return {"repo": repo, "bridge": bridge, "lab": lab, "profiles": profiles, "safe": safe_path,
            "config": config_path, "candidate": tmp_path / "candidate.json"}


def frozen(fixture):
    path = fixture["candidate"]
    if not path.exists():
        path.write_bytes(candidate._json_bytes(candidate.candidate_snapshot(fixture["profiles"])))
    return path


async def prepare(fixture, slot=1, suffix=""):
    root = fixture["lab"].LAB_ROOT / "agent-trials" / (f"slot-{slot}" + suffix)
    state = await trial.prepare_trial_async(trial.plan_trials()[slot - 1], root, agent_id=f"fresh-stub-{slot}",
        candidate_freeze=frozen(fixture), profiles_path=fixture["profiles"], provider_factory=Provider, lab=fixture["lab"])
    return root, state


def repair(state):
    (Path(state["case_dir"]) / "src/app.py").write_text("def add(a, b):\n    return a + b\n")


async def applied(evidence, **kwargs):
    repair({"case_dir": str(kwargs["project"])})
    return {"schema_version": 6, "state": "hypothesis_supported_by_tests", "context_policy": "v6", "attempts": [],
        "apply": {"requested": True, "applied": True}}


async def abstain(evidence, **kwargs):
    return {"schema_version": 6, "state": "unverified", "context_policy": "v6", "attempts": [],
        "reason": "Synthetic retained abstention", "apply": {"requested": True, "applied": False}}


async def finish(fixture, slot, *, assisted_success=True, final_success=True, suffix=""):
    directory, state = await prepare(fixture, slot, suffix)
    trial.start_trial(directory, lab=fixture["lab"])
    if state["treatment"] == "verified_assistant":
        result = await trial.hint_trial(directory, callback=applied if assisted_success else abstain,
            provider_factory=Provider, lab=fixture["lab"])
        if assisted_success:
            assert result["trial_verification"]["verified_endpoint"]
            return directory
    action = trial.begin_attempt(directory, "Synthetic ordinary work", phase="repair", lab=fixture["lab"])
    if final_success:
        repair(state)
    trial.end_attempt(directory, action, exit_code=0, lab=fixture["lab"])
    if final_success:
        assert trial.verify_trial(directory, lab=fixture["lab"])["verified_endpoint"]
    else:
        trial.stop_trial(directory, reason="unsolved", lab=fixture["lab"])
    return directory


def test_candidate_snapshot_reads_only_safe_code_profile_and_descriptor(fixture):
    snapshot = candidate.candidate_snapshot(fixture["profiles"])
    assert fixture["bridge"].prepared == fixture["bridge"].runs == fixture["bridge"].verifications == []
    assert snapshot["fixture_set_identity"] == fixture["bridge"].identity
    assert snapshot["specification"]["no_prior_transfer_assistant_evaluation"]
    assert set(snapshot["measurement_sources_sha256"]) >= {"natural_candidate.py", "natural_trial.py", "report_natural_trials.py"}


def test_prespecified_six_slot_order_and_excluded_overlap_control():
    assert [(row["case_id"], row["treatment"]) for row in trial.plan_trials()] == [
        ("NAT-01", "normal_tools"), ("NAT-03", "verified_assistant"), ("NAT-04", "normal_tools"),
        ("NAT-01", "verified_assistant"), ("NAT-03", "normal_tools"), ("NAT-04", "verified_assistant")]
    assert "NAT-02" not in trial.CASE_IDS and trial.CAP_SECONDS == 900


@pytest.mark.parametrize("change", ["code", "profile", "safe_identity", "protocol"])
async def test_candidate_drift_stops_before_any_case_preparation(fixture, change):
    frozen(fixture)
    path = {"code": fixture["repo"] / "packages/python/src/decilist/unit.py", "profile": fixture["config"],
            "safe_identity": fixture["safe"], "protocol": fixture["repo"] / "docs/triage-natural-transfer-protocol.md"}[change]
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError):
        await prepare(fixture)
    assert fixture["bridge"].prepared == []


async def test_preparation_retains_historical_canonical_source_and_protected_license(fixture):
    directory, state = await prepare(fixture)
    assert state["start_clock"] is None and state["status"] == "ready"
    assert "src/LICENSE.md" in state["protected_sha256"]
    assert (directory / "participant-manifest.json").is_file()
    assert not (directory / "case/.triage").exists()


async def test_initial_apply_is_timed_once_and_auto_verifies_protected_repair(fixture):
    directory, _ = await prepare(fixture, 2)
    trial.start_trial(directory, lab=fixture["lab"])
    with pytest.raises(trial.TrialError, match="initial live"):
        trial.begin_attempt(directory, "inspect before assistance", lab=fixture["lab"])
    result = await trial.hint_trial(directory, callback=applied, provider_factory=Provider, lab=fixture["lab"])
    ledger = trial._read(directory, fixture["lab"])
    assert result["trial_status"] == "solved" and len(ledger["hints"]) == len(ledger["verifications"]) == 1
    assert clocks._elapsed(ledger["start_clock"], ledger["hints"][0]["start_clock"])[1] is None
    assert ledger["end_clock"] == ledger["verifications"][0]["end_clock"]
    with pytest.raises(trial.TrialError):
        await trial.hint_trial(directory, callback=applied, provider_factory=Provider, lab=fixture["lab"])


async def test_callback_failure_then_manual_repair_remains_valid_assigned_arm(fixture):
    directory, state = await prepare(fixture, 2)
    trial.start_trial(directory, lab=fixture["lab"])
    async def error(evidence, **kwargs):
        raise RuntimeError("Observed stub assistance error")
    reply = await trial.hint_trial(directory, callback=error, provider_factory=Provider, lab=fixture["lab"])
    assert reply["state"] == "unavailable"
    action = trial.begin_attempt(directory, "manual continuation", phase="repair", lab=fixture["lab"])
    repair(state)
    trial.end_attempt(directory, action, exit_code=0, lab=fixture["lab"])
    assert trial.verify_trial(directory, lab=fixture["lab"])["verified_endpoint"]
    report = reporting.build_report([directory], lab=fixture["lab"])
    slot = report["slots"][1]
    assert slot["outcome"] == "solved" and slot["timed_success_confirmed"]
    assert not slot["initial_assistant_protected_repair"]


async def test_normal_tools_cannot_receive_a_hint(fixture):
    directory, _ = await prepare(fixture, 1)
    trial.start_trial(directory, lab=fixture["lab"])
    with pytest.raises(trial.TrialError, match="assisted arm"):
        await trial.hint_trial(directory, callback=applied, provider_factory=Provider, lab=fixture["lab"])


async def test_runtime_mode_failure_stops_before_private_bridge_verification(fixture):
    class Wrong(Provider):
        async def apply_template(self, messages):
            self.generation_mode_proof = None
    with pytest.raises(ValueError):
        await trial.prepare_trial_async(trial.plan_trials()[0], fixture["lab"].LAB_ROOT / "agent-trials/unstarted",
            agent_id="fresh-runtime-check", candidate_freeze=frozen(fixture), profiles_path=fixture["profiles"],
            provider_factory=Wrong, lab=fixture["lab"])
    assert fixture["bridge"].verifications == fixture["bridge"].prepared == []


async def test_timeout_hint_remains_assigned_then_manual_protected_repair_is_valid(fixture):
    directory, state = await prepare(fixture, 2)
    trial.start_trial(directory, lab=fixture["lab"])
    async def timeout(evidence, **kwargs):
        raise asyncio.TimeoutError("Observed stub generation timeout")
    reply = await trial.hint_trial(directory, callback=timeout, provider_factory=Provider, lab=fixture["lab"])
    assert reply["error"]["type"] == "TimeoutError"
    action = trial.begin_attempt(directory, "manual continuation after timeout", phase="repair", lab=fixture["lab"])
    repair(state)
    trial.end_attempt(directory, action, exit_code=0, lab=fixture["lab"])
    trial.verify_trial(directory, lab=fixture["lab"])
    slot = reporting.build_report([directory], lab=fixture["lab"])["slots"][1]
    assert slot["timed_success_confirmed"] and not slot["initial_assistant_protected_repair"]


async def test_wrong_context_treatment_does_not_become_valid_after_manual_repair(fixture):
    directory, state = await prepare(fixture, 2)
    trial.start_trial(directory, lab=fixture["lab"])
    async def wrong(evidence, **kwargs):
        return {"state": "unverified", "context_policy": "v5", "attempts": []}
    await trial.hint_trial(directory, callback=wrong, provider_factory=Provider, lab=fixture["lab"])
    action = trial.begin_attempt(directory, "manual repair", phase="repair", lab=fixture["lab"])
    repair(state)
    trial.end_attempt(directory, action, exit_code=0, lab=fixture["lab"])
    trial.verify_trial(directory, lab=fixture["lab"])
    slot = reporting.build_report([directory], lab=fixture["lab"])["slots"][1]
    assert slot["outcome"] == "invalid" and not slot["timed_success_confirmed"]


async def test_claimed_model_success_without_a_repair_earns_no_initial_credit(fixture):
    directory, state = await prepare(fixture, 2)
    trial.start_trial(directory, lab=fixture["lab"])
    async def unsupported(evidence, **kwargs):
        return {"state": "hypothesis_supported_by_tests", "context_policy": "v6", "attempts": [],
                "apply": {"requested": True, "applied": True}}
    result = await trial.hint_trial(directory, callback=unsupported, provider_factory=Provider, lab=fixture["lab"])
    assert not result["trial_verification"]["verified_endpoint"] and result["trial_status"] == "active"
    action = trial.begin_attempt(directory, "manual repair after unsupported result", phase="repair", lab=fixture["lab"])
    repair(state)
    trial.end_attempt(directory, action, exit_code=0, lab=fixture["lab"])
    trial.verify_trial(directory, lab=fixture["lab"])
    slot = reporting.build_report([directory], lab=fixture["lab"])["slots"][1]
    assert slot["timed_success_confirmed"] and not slot["initial_assistant_protected_repair"]


@pytest.mark.parametrize("flag", ["wrong_source", "wrong_group", "skip", "timeout"])
async def test_provenance_subset_skips_and_timeouts_never_supply_a_protected_repair(fixture, flag):
    directory, state = await prepare(fixture)
    trial.start_trial(directory, lab=fixture["lab"])
    repair(state)
    setattr(fixture["bridge"], flag, True)
    result = trial.verify_trial(directory, lab=fixture["lab"])
    assert not result["verified_endpoint"]


async def test_changed_collection_and_license_are_rejected(fixture):
    directory, state = await prepare(fixture)
    trial.start_trial(directory, lab=fixture["lab"])
    repair(state)
    fixture["bridge"].changed_collection = True
    assert not trial.verify_trial(directory, lab=fixture["lab"])["verified_endpoint"]
    fixture["bridge"].changed_collection = False
    (Path(state["case_dir"]) / "src/LICENSE.md").write_text("removed license\n")
    assert not trial.verify_trial(directory, lab=fixture["lab"])["verified_endpoint"]


async def test_clock_cap_is_real_and_never_reported_as_human_savings(fixture):
    directory, state = await prepare(fixture)
    trial.start_trial(directory, lab=fixture["lab"])
    ledger = trial._read(directory, fixture["lab"])
    ledger["start_clock"]["monotonic_ns"] -= 901_000_000_000
    ledger["start_clock"]["wall_ns"] -= 901_000_000_000
    clocks._write(directory, ledger)
    repair(state)
    result = trial.verify_trial(directory, lab=fixture["lab"])
    assert result["verified_endpoint"] and result["trial_status"] == "capped"
    assert trial.status(directory, lab=fixture["lab"])["unmeasured"]["human_minutes_saved"] is None


async def test_reporter_runs_six_independent_protected_checks_outside_clock(fixture):
    directories = [await finish(fixture, slot) for slot in range(1, 7)]
    originals = [(directory / "trial.json").read_bytes() for directory in directories]
    before = len(fixture["bridge"].runs)
    report = reporting.build_report(directories, lab=fixture["lab"])
    assert len(fixture["bridge"].runs) == before + 6
    assert report["complete_unique_plan"] and all(row["timed_success_confirmed"] for row in report["slots"])
    assert report["per_treatment"]["verified_assistant"]["initial_assistant_protected_repairs"] == 3
    assert originals == [(directory / "trial.json").read_bytes() for directory in directories]
    assert len(report["paired_same_case_differences"]) == 3


async def test_reporter_preserves_missing_duplicate_and_unsolved_slots(fixture):
    first = await finish(fixture, 1)
    duplicate = await finish(fixture, 1, suffix="-duplicate")
    failed = await finish(fixture, 2, assisted_success=False, final_success=False)
    report = reporting.build_report([first, duplicate, failed], lab=fixture["lab"])
    assert report["slots"][0]["outcome"] == "duplicate" and report["slots"][1]["outcome"] == "unsolved"
    assert all(row["outcome"] == "missing" for row in report["slots"][2:])
    assert report["per_treatment"]["verified_assistant"]["median_seconds_all_finished_slots"] is None
    assert not report["prespecified_engineering_gate"]["met"]


def test_descriptive_median_includes_failed_finished_slots():
    rows = [{**planned, "outcome": "unsolved" if planned["slot"] == 6 else "solved", "timing_eligible": True,
             "agent_elapsed_seconds": float(planned["slot"] * 10), "timed_success_confirmed": planned["slot"] != 6}
            for planned in trial.plan_trials()]
    summaries, _ = reporting._summaries(rows)
    assert summaries["verified_assistant"]["median_seconds_all_finished_slots"] == 40
    assert summaries["verified_assistant"]["confirmed_solved"] == 2


async def test_public_private_outputs_are_write_once_and_redacted(fixture):
    directory = await finish(fixture, 2)
    report = reporting.build_report([directory], lab=fixture["lab"])
    public, private = fixture["repo"] / "public.json", fixture["repo"] / "private.json"
    reporting.export_report(report, public_output=public, private_output=private)
    assert "private_full_records" not in public.read_text() and "private_full_records" in private.read_text()
    with pytest.raises(ValueError, match="never overwritten"):
        reporting.export_report(report, public_output=public, private_output=private)


async def test_drift_is_checked_before_loading_bridge_code_from_a_ledger(fixture, monkeypatch):
    directory, _ = await prepare(fixture)
    fixture["profiles"].write_bytes(fixture["profiles"].read_bytes() + b"\n")
    monkeypatch.setattr(trial, "load_lab", lambda *args: pytest.fail("changed bridge must not be imported"))
    with pytest.raises(ValueError):
        trial._lab_api(directory)


@pytest.mark.parametrize("recorded_hash", ["candidate_manifest_sha256", "profiles_manifest_sha256", "model_config_sha256", "manifest_sha256"])
async def test_saved_creation_hashes_are_checked_before_bridge_import(fixture, monkeypatch, recorded_hash):
    directory, state = await prepare(fixture)
    state[recorded_hash] = "0" * 64
    clocks._write(directory, state)
    monkeypatch.setattr(trial, "load_lab", lambda *args: pytest.fail("bridge must not import before recorded-hash checks"))
    with pytest.raises(trial.TrialError, match="before bridge import"):
        trial._lab_api(directory)


def test_standalone_loader_rejects_drifted_bridge_before_executing_it(fixture):
    manifest = json.loads(fixture["profiles"].read_text())
    bridge_path = Path(manifest["bridge_path"])
    bridge_path.write_text("raise AssertionError('Bridge side effect must never execute')\n")
    with pytest.raises(ValueError, match="changed before import"):
        candidate.load_lab(fixture["profiles"])


async def test_repointed_bridge_is_rejected_before_any_import_side_effect(fixture, monkeypatch):
    directory, _ = await prepare(fixture)
    manifest = json.loads(fixture["profiles"].read_text())
    repointed = fixture["profiles"].parent / "unapproved-bridge.py"
    repointed.write_text("raise AssertionError('Repointed bridge must never execute')\n")
    manifest["bridge_path"] = str(repointed)
    fixture["profiles"].write_text(json.dumps(manifest))
    monkeypatch.setattr(trial, "load_lab", lambda *args: pytest.fail("repointed bridge reached import"))
    with pytest.raises(ValueError):
        trial._lab_api(directory)
