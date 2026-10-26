from copy import deepcopy
import json
from pathlib import Path

import pytest

from benchmarks import run_triage_v6 as runner
from test_v6_measurement_support import (make_fixture, candidate_file, proof_file, args, Provider,
                                        abstain, applied, mode_proof)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    return make_fixture(tmp_path, monkeypatch)


def test_candidate_requires_both_actual_profiles_without_any_runtime_call(fixture):
    candidate = runner.candidate_snapshot(fixture.profiles)
    assert set(candidate["profiles"]) == {"current", "stronger"} and Provider.calls == []
    assert candidate["specification"]["context_chars"] == 24000
    assert candidate["specification"]["max_generated_tokens"] == 1200
    assert candidate["specification"]["provider_token_cap"] == 1536
    assert candidate["profiles"]["stronger"]["protocol_metadata"]["generation_mode_proof"]["per_request_template_override"] is False
    candidate_file(fixture)
    runner.verify_candidate(fixture.root / "candidate.json", fixture.profiles)


@pytest.mark.parametrize("change", ["source", "inactive_config", "template_proof", "weights", "missing_profile"])
def test_all_profiles_and_source_drift_are_rejected_before_case_membership(fixture, change):
    candidate = candidate_file(fixture)
    if change == "source":
        (fixture.root / "source.py").write_text("VALUE = 2\n")
    elif change == "inactive_config":
        path = fixture.configs["stronger"]
        config = json.loads(path.read_text()); config["model"] = "different-stronger"
        path.write_text(json.dumps(config))
    else:
        manifest = json.loads(fixture.profiles.read_text())
        if change == "template_proof":
            manifest["profiles"]["stronger"]["protocol_metadata"]["generation_mode_proof"]["per_request_template_override"] = True
        elif change == "weights":
            manifest["profiles"]["stronger"]["protocol_metadata"]["model_artifact_sha256"] = "0" * 64
        else:
            del manifest["profiles"]["stronger"]
        fixture.profiles.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        runner.verify_candidate(candidate, fixture.profiles)
    assert fixture.lab.membership_calls == []


async def test_capture_one_available_profile_is_actual_native_probe_without_inference_or_cases(fixture):
    setup = {"model_artifact_sha256": runner.EXPECTED_WEIGHTS["current"], "runtime_binary_sha256": "c" * 64,
             "launch_settings": {"slots": 1, "ctx": 16384}}
    result = await runner.capture_profile(fixture.configs["current"], model_profile="current",
        setup_metadata=setup, provider_factory=Provider)
    assert result["ready_identity_stable"] and not result["inference_requested"]
    assert [kind for _, kind in Provider.calls] == ["ready", "native_template", "ready"]
    assert fixture.lab.freeze_calls == fixture.lab.membership_calls == []
    assert result["generation_mode_proof"]["status"] == "verified"


def test_capture_cli_is_write_once_and_does_not_load_the_corpus(fixture, capsys):
    setup = fixture.root / "setup.json"
    setup.write_text(json.dumps({"model_artifact_sha256": runner.EXPECTED_WEIGHTS["current"], "runtime_binary_sha256": "c" * 64}))
    output = fixture.root / "native-current.json"
    command = ["--capture-profile", "current", "--model-config", str(fixture.configs["current"]),
               "--setup-metadata", str(setup), "--capture-output", str(output)]
    assert runner.main(command) == 0
    assert json.loads(capsys.readouterr().out)["inference_requested"] is False
    original = output.read_bytes()
    before = len(Provider.calls)
    assert runner.main(command) == 2
    assert output.read_bytes() == original and len(Provider.calls) == before
    assert fixture.lab.freeze_calls == fixture.lab.membership_calls == []


def test_synthetic_mode_records_satisfy_the_real_pure_validator_without_live_requests(fixture):
    from decilist.triage_hypothesis_v6 import validate_generation_mode_proof
    for path in fixture.configs.values():
        config = json.loads(path.read_text())
        proof = mode_proof(config)
        assert validate_generation_mode_proof(proof, generation_profile=config["generation_profile"],
            expected_chat_template_sha256=config["expected_chat_template_sha256"], model=config["model"])["status"] == "verified"
        invalid = {**proof, "per_request_template_override": True}
        with pytest.raises(ValueError):
            validate_generation_mode_proof(invalid, generation_profile=config["generation_profile"],
                expected_chat_template_sha256=config["expected_chat_template_sha256"], model=config["model"])


async def test_mode_or_postprobe_identity_failure_is_retained_without_consuming_first_batch(fixture):
    class Wrong(Provider):
        async def apply_template(self, messages):
            await super().apply_template(messages)
            self.generation_mode_proof["actual_prompt_suffix"] = "open-thinking"
    report = await runner.run(args(fixture), lab_api=fixture.lab, callback=abstain, provider_factory=Wrong)
    assert report["status"] == "operational_failure" and not report["first_observation_consumed"]
    assert fixture.lab.membership_calls == [] and fixture.lab.prepared == []
    assert runner.matrix_progress(fixture.matrix_root)["next_batch"] == "current-v5"
    assert json.loads((fixture.root / "one.private.json").read_text())["case_access_started"] is False


async def test_storage_failure_happens_before_runtime_and_heldout_access(fixture):
    settings = args(fixture)
    settings.output.write_text("prior evidence")
    report = await runner.run(settings, lab_api=fixture.lab, callback=abstain, provider_factory=Provider)
    assert report["status"] == "operational_failure" and settings.output.read_text() == "prior evidence"
    assert Provider.calls == [] and fixture.lab.membership_calls == []
    assert list((fixture.lab.LAB_ROOT / "measurements/operational").glob("*.private.json"))


async def test_missing_strict_retained_proof_is_an_operational_failure(fixture, monkeypatch):
    def missing(*, require_retained_proof=False):
        assert require_retained_proof is True
        raise FileNotFoundError("original retained fixture proof unavailable")
    monkeypatch.setattr(fixture.lab, "verify_freeze", missing)
    report = await runner.run(args(fixture), lab_api=fixture.lab, callback=abstain, provider_factory=Provider)
    assert report["status"] == "operational_failure" and not report["case_access_started"]
    assert fixture.lab.membership_calls == []


async def test_single_available_profile_development_needs_no_full_matrix_freeze(fixture):
    proof = proof_file(fixture, runner.CASE_ORDER["development"])
    settings = args(fixture, split="development", candidate_freeze=None, profiles=None, matrix_root=None,
                    proof=proof, model_profile="current", context_policy="v6")
    report = await runner.run(settings, lab_api=fixture.lab, callback=abstain, provider_factory=Provider)
    assert len(report["rows"]) == 2 and not report["valid_frozen_comparison"]
    assert fixture.lab.freeze_calls == [True] and not report["first_observation"]


async def test_four_fixed_batches_retain_all_eight_rows_and_paired_summary(fixture):
    proof = proof_file(fixture)
    records = []
    for index, batch in enumerate(runner.BATCHES):
        settings = args(fixture, index=str(index), proof=proof, batch=batch["batch"])
        report = await runner.run(settings, lab_api=fixture.lab, callback=applied, provider_factory=Provider)
        assert report["selection"] == batch and report["case_order"] == list(runner.HELDOUT_ORDER)
        assert len(report["rows"]) == 8 and report["summary"]["root_protected_repairs"] == 6
        assert report["valid_frozen_comparison"] and report["summary"]["unique_regressions"] == 6
        assert [row["case_id"] for row in report["rows"][-2:]] == ["c01", "c02"]
        records.append(report)
    progress = runner.matrix_progress(fixture.matrix_root)
    assert progress["complete"] and [row["batch"] for row in progress["batches"]] == [row["batch"] for row in runner.BATCHES]
    combined = runner.matrix_summary(records)
    assert combined["complete_unique_matrix"] and len(combined["paired_same_case"]) == 6
    assert combined["human_time_saved"] is None
    assert all(value is True for value in fixture.lab.freeze_calls)
    changed = deepcopy(records)
    changed[0]["rows"][1]["case_id"] = changed[0]["rows"][0]["case_id"]
    assert runner.matrix_summary(changed)["cells"][0]["outcome"] == "invalid"
    with_replay = runner.matrix_summary([*records, {**records[0], "replay": True}])
    assert with_replay["complete_unique_matrix"] and len(with_replay["replay_records"]) == 1


async def test_out_of_order_or_duplicate_first_batch_is_not_silently_retried(fixture):
    proof = proof_file(fixture)
    wrong = await runner.run(args(fixture, proof=proof, batch="current-v6"), lab_api=fixture.lab,
                             callback=abstain, provider_factory=Provider)
    assert wrong["status"] == "operational_failure" and not wrong["first_observation_consumed"]
    first = await runner.run(args(fixture, index="first", proof=proof), lab_api=fixture.lab,
                             callback=abstain, provider_factory=Provider)
    duplicate = await runner.run(args(fixture, index="duplicate", proof=proof), lab_api=fixture.lab,
                                 callback=abstain, provider_factory=Provider)
    assert first["first_observation"] and duplicate["status"] == "operational_failure"
    replayed = await runner.run(args(fixture, index="replay", proof=proof, replay=True, matrix_root=None),
                               lab_api=fixture.lab, callback=abstain, provider_factory=Provider)
    assert replayed["replay"] and not replayed["first_observation"] and not replayed["valid_frozen_comparison"]
    assert len(runner.matrix_progress(fixture.matrix_root)["batches"]) == 1


async def test_every_workflow_error_and_context_drift_remains_visible(fixture):
    proof = proof_file(fixture)
    async def wrong_context(evidence, **kwargs):
        return {"state": "unverified", "context_policy": "not-the-assigned-policy", "attempts": []}
    report = await runner.run(args(fixture, proof=proof), lab_api=fixture.lab,
                              callback=wrong_context, provider_factory=Provider)
    assert len(report["rows"]) == report["summary"]["errors"] == 8
    assert all(row["measurement_error"] for row in report["rows"])
    progress = runner.matrix_progress(fixture.matrix_root)
    assert progress["next_batch"] == "current-v6"
    observations = Path(progress["batches"][0]["run_root"]) / "observations"
    assert len(list(observations.glob("*.json"))) == 8


async def test_failure_after_membership_access_consumes_first_and_is_persisted_honestly(fixture):
    report = await runner.run(args(fixture), lab_api=fixture.lab, callback=abstain, provider_factory=Provider)
    assert report["case_access_started"] and report["first_observation_consumed"]
    raw = json.loads((fixture.root / "one.private.json").read_text())
    assert raw["case_access_started"] and raw["first_observation_consumed"]
    assert runner.matrix_progress(fixture.matrix_root)["batches"][0]["status"] == "incomplete"


def test_explicit_interruption_keeps_original_batch_and_allows_next_fixed_batch(fixture):
    fixture.matrix_root.mkdir(parents=True)
    candidate = candidate_file(fixture)
    runner._reserve(fixture.matrix_root, runner.BATCHES[0], runner.sha(candidate.read_bytes()), fixture.lab.LAB_ROOT / "old-run")
    runner._matrix_update(fixture.matrix_root, "current-v5", case_access_started=True)
    assert runner.matrix_progress(fixture.matrix_root)["next_batch"] is None
    result = runner.mark_incomplete(fixture.matrix_root, reason="process interrupted after opening first case")
    assert result["batches"][0]["status"] == "incomplete" and result["next_batch"] == "current-v6"
    assert "interrupted" in result["batches"][0]["interruption"]


def test_interrupted_reservation_before_case_access_retains_operation_without_consuming_first(fixture):
    fixture.matrix_root.mkdir(parents=True)
    candidate = candidate_file(fixture)
    runner._reserve(fixture.matrix_root, runner.BATCHES[0], runner.sha(candidate.read_bytes()), fixture.lab.LAB_ROOT / "unused-run")
    result = runner.mark_incomplete(fixture.matrix_root, reason="stopped before case membership access")
    assert result["batches"] == [] and result["next_batch"] == "current-v5"
    assert result["operational_attempts"][0]["first_observation_consumed"] is False


async def test_drift_during_preflight_is_detected_before_case_membership(fixture):
    class Changing(Provider):
        async def apply_template(self, messages):
            result = await super().apply_template(messages)
            (fixture.root / "source.py").write_text("VALUE = 2\n")
            return result
    result = await runner.run(args(fixture), lab_api=fixture.lab, callback=abstain, provider_factory=Changing)
    assert result["status"] == "operational_failure" and not result["case_access_started"]
    assert fixture.lab.membership_calls == []


async def test_inactive_profile_drift_during_batch_invalidates_frozen_comparison(fixture):
    proof = proof_file(fixture)
    calls = []
    async def change_inactive(evidence, **kwargs):
        calls.append(1)
        if len(calls) == 8:
            path = fixture.configs["stronger"]
            config = json.loads(path.read_text()); config["model"] = "modified-after-last-case"
            path.write_text(json.dumps(config))
        return await abstain(evidence, **kwargs)
    result = await runner.run(args(fixture, proof=proof), lab_api=fixture.lab, callback=change_inactive, provider_factory=Provider)
    assert len(result["rows"]) == 8 and result["source_stable_during_measurement"]
    assert not result["candidate_stable_during_measurement"] and not result["valid_frozen_comparison"]
    assert result["status"] == "identity_drift"
    assert result["summary"]["post_case_operational_errors"] == 1


async def test_source_drift_at_batch_end_retains_rows_and_fails_comparison(fixture):
    proof = proof_file(fixture)
    count = []
    async def change_source(evidence, **kwargs):
        count.append(1)
        if len(count) == 8:
            (fixture.root / "source.py").write_text("VALUE = 2\n")
        return await abstain(evidence, **kwargs)
    result = await runner.run(args(fixture, proof=proof), lab_api=fixture.lab,
                              callback=change_source, provider_factory=Provider)
    assert len(result["rows"]) == 8 and not result["source_stable_during_measurement"]
    assert result["summary"]["errors"] == 1 and result["status"] == "identity_drift"


async def test_public_context_retention_and_async_settings_pass_through_same_workflow(fixture, monkeypatch):
    async def workflow(project, tests, regressions, work_dir, config_path, **kwargs):
        assert kwargs["context_policy"] == "v5" and kwargs["max_context_chars"] == 24000
        assert tests[-2:] == regressions[-2:] == ["-o", "asyncio_mode=auto"]
        path = Path(work_dir) / "evidence/context.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"schema_version": 5, "functions": [{"id": "f1", "path": "src/app.py"}]}))
        return {"state": "unverified", "context_policy": "v5", "context_path": str(path), "attempts": []}
    fixture.workflow.run_triage_v6 = workflow
    report = await runner.workflow_bridge({"truncated": True}, project=fixture.root / "project",
        manifest={"failing_tests": ["tests/test_bug.py"], "regression_tests": ["tests/test_existing.py"],
                  "test_command": ["python", "-m", "pytest", "-o", "asyncio_mode=auto"], "allowed_production_roots": ["src/"]},
        work_dir=fixture.lab.LAB_ROOT / "context-copy", config_path=fixture.configs["current"],
        endpoint="http://127.0.0.1:8792", context_policy="v5")
    public = runner.public_report({"nested": report})["nested"]
    assert report["actual_context"]["schema_version"] == 5
    assert public["public_actual_context_sha256"] == runner.fingerprint(public["actual_context"])
    assert report["actual_context_json"]
