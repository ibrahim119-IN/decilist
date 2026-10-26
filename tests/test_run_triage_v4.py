"""Candidate gates and measurements with fakes; never held-out content or models."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks import run_triage_v4 as runner
from test_triage_v4_trial import Lab, Provider, READY, repair


def metadata():
    return {"qwen": {"ready_identity": READY, "model_artifact_sha256": "a" * 64,
                     "runtime_binary_sha256": "b" * 64}}


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    source = tmp_path / "source.py"
    source.write_text("VALUE = 1\n")
    freeze, catalog, config = tmp_path / "freeze.json", tmp_path / "catalog.json", tmp_path / "config.json"
    freeze.write_text('{"fixture":"freeze"}')
    catalog.write_text('{"fixture":"catalog without cases"}')
    config.write_text(json.dumps({"model": "fake", "expected_build": "fake-build"}))
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "_source_files", lambda: ("source.py",))
    monkeypatch.setattr(runner, "_specification", lambda: {"fixture": "specification"})
    monkeypatch.setattr(runner.lab, "FREEZE", freeze)
    monkeypatch.setattr(runner.lab, "CATALOG", catalog)
    lab = Lab(tmp_path / "v4-lab")
    lab.case_ids = lambda split: ("d01", "d02") if split == "development" else ("e01", "e02") if split == "evaluation" else ("c01", "c02")
    lab.verify_freeze = lambda: {"verified": True}
    lab.prepare_machine_evidence = lab.prepare_case_evidence
    return tmp_path, config, lab


def candidate_file(fixture):
    root, config, _ = fixture
    path = root / "candidate.json"
    path.write_bytes(runner._json_bytes(runner.candidate_snapshot(config, protocol_metadata=metadata())))
    return path


def proof_file(fixture, ids=("d01", "d02")):
    root, _, lab = fixture
    rows = []
    for case_id in ids:
        project = lab.LAB_ROOT / "synthetic-proof" / case_id
        lab.prepare_case(case_id, project)
        seeded = lab.run_case(project)
        baseline = {**seeded, "exit_code": 0}
        rows.append({"case_id": case_id, "verified": True, "baseline": baseline, "mutation": seeded,
            "reference_repair": {"passed": True, "run": baseline},
            "seeded_source_sha256": seeded["editable_sha256_after"]})
    path = root / "proof.json"
    path.write_text(json.dumps({"cases": rows}))
    return path


def args(fixture, **changes):
    root, config, _ = fixture
    values = {"split": "dev", "repetitions": 1, "output": root / "public.json",
              "private_output": root / "private.json", "model_config": config, "candidate_freeze": None,
              "qwen_url": "http://127.0.0.1:8792", "proof": root / "proof.json", "apply": False}
    values.update(changes)
    return SimpleNamespace(**values)


def test_candidate_covers_current_file_membership_catalog_protocol_config_and_runtime(fixture):
    root, config, _ = fixture
    path = candidate_file(fixture)
    saved = runner.verify_candidate(path, config)
    assert saved["sources_sha256"] == {"source.py": runner.sha((root / "source.py").read_bytes())}
    assert saved["catalog_sha256"] == runner.sha(runner.lab.CATALOG.read_bytes())
    assert saved["runtime_config_sha256"] == runner.sha(config.read_bytes())
    runner.check_ready(READY, {"model": "fake", "expected_build": "fake-build"}, saved)
    with pytest.raises(ValueError, match="ready identity"):
        runner.check_ready({**READY, "context": 999}, {"model": "fake", "expected_build": "fake-build"}, saved)


@pytest.mark.parametrize("target", ["source", "catalog", "freeze", "config", "protocol"])
def test_candidate_rejects_every_frozen_identity_drift(fixture, monkeypatch, target):
    root, config, _ = fixture
    path = candidate_file(fixture)
    if target == "source":
        (root / "source.py").write_text("VALUE = 2\n")
    elif target == "catalog":
        runner.lab.CATALOG.write_text("{}")
    elif target == "freeze":
        runner.lab.FREEZE.write_text("{}")
    elif target == "config":
        config.write_text(json.dumps({"model": "fake", "expected_build": "other-build"}))
    else:
        monkeypatch.setattr(runner, "_specification", lambda: {"fixture": "changed specification"})
    with pytest.raises(ValueError, match="drift"):
        runner.verify_candidate(path, config)


def test_missing_candidate_and_missing_runtime_artifact_identity_rejected(fixture):
    _, config, _ = fixture
    with pytest.raises(ValueError, match="before case inspection"):
        runner.verify_candidate(None, config)
    with pytest.raises(ValueError, match="measured model/runtime"):
        runner.candidate_snapshot(config, protocol_metadata={"qwen": {"ready_identity": READY}})


def test_normalized_public_identity_preserves_original_path_hash_and_is_idempotent(fixture):
    _, config, _ = fixture
    identity = {**READY, "model_path": r"C:\private\weights\model.gguf"}
    protocol = metadata()
    protocol["qwen"]["ready_identity"] = identity
    saved = runner.candidate_snapshot(config, protocol_metadata=protocol)
    normalized = saved["protocol_metadata"]["qwen"]["ready_identity"]
    assert normalized["model_path_original_sha256"] == runner.sha(identity["model_path"].encode())
    assert runner._public(saved) == saved
    runner.check_ready(identity, {"model": "fake", "expected_build": "fake-build"}, saved)


@pytest.mark.parametrize("split", ["evaluation", "controls"])
async def test_heldout_manifest_gate_precedes_any_case_or_runtime_call(fixture, split):
    _, _, lab = fixture
    lab.case_ids = lambda *args: pytest.fail("case data touched before freeze")
    def provider(*args):
        pytest.fail("runtime contacted before candidate verified")
    with pytest.raises(ValueError, match="before case inspection"):
        await runner.run(args(fixture, split=split), lab_api=lab, provider_factory=provider)


async def test_actual_ready_identity_checked_before_case_membership(fixture):
    _, _, lab = fixture
    candidate = candidate_file(fixture)
    class Wrong(Provider):
        async def ready(self):
            return {**READY, "context_window": "changed"}
    lab.case_ids = lambda *args: pytest.fail("case data touched before ready check")
    with pytest.raises(ValueError, match="ready identity"):
        await runner.run(args(fixture, split="evaluation", candidate_freeze=candidate), lab_api=lab, provider_factory=Wrong)


async def test_dev_needs_no_candidate_and_preserves_full_attempts_times_and_outputs(fixture):
    root, _, lab = fixture
    proof = proof_file(fixture)
    original = proof.read_bytes()
    calls = []
    async def callback(evidence, **kwargs):
        calls.append(kwargs["project"])
        return {"state": "unverified", "attempts": [{"generation": {"raw_response": "retained model output"},
                "verification": {"state": "failed", "stdout": "observed rejection"}}],
                "raw_protocol_trace": {"sentinel": "PRIVATE_TRACE"}}
    report = await runner.run(args(fixture), lab_api=lab, callback=callback, provider_factory=Provider)
    assert len(calls) == 2 and proof.read_bytes() == original
    assert not report["valid_frozen_comparison"] and report["summary"]["unique_regressions"] == 2
    assert all(row["end_to_end_ms"] >= row["stage_timings_ms"]["workflow_ms"] >= 0 for row in report["rows"])
    assert all(row["input_evidence"] and row["source_sha256_before"] for row in report["rows"])
    assert "retained model output" in (root / "private.json").read_text()
    assert "PRIVATE_TRACE" not in (root / "public.json").read_text()


async def test_apply_only_fresh_lab_cases_and_root_endpoint_separate_from_workflow(fixture):
    root, _, lab = fixture
    proof_file(fixture)
    proof_source = {str(path): path.read_bytes() for path in (lab.LAB_ROOT / "synthetic-proof").rglob("app.py")}
    async def callback(evidence, **kwargs):
        assert kwargs["apply"] is True and "measurements" in str(kwargs["project"])
        repair({"case_dir": str(kwargs["project"])})
        return {"state": "hypothesis_supported_by_tests", "apply": {"applied": True}, "attempts": []}
    report = await runner.run(args(fixture, apply=True), lab_api=lab, callback=callback, provider_factory=Provider)
    assert report["summary"]["root_protected_repairs"] == report["summary"]["workflow_supported"] == 2
    assert all(row["root_validation"]["passed"] and "root_protected_validation_ms" in row["stage_timings_ms"] for row in report["rows"])
    assert proof_source == {str(path): path.read_bytes() for path in (lab.LAB_ROOT / "synthetic-proof").rglob("app.py")}


async def test_every_error_remains_an_attempted_row_without_substitute_prediction(fixture):
    _, _, lab = fixture
    proof_file(fixture)
    async def callback(evidence, **kwargs):
        raise RuntimeError("Observed workflow exception")
    report = await runner.run(args(fixture, repetitions=2), lab_api=lab, callback=callback, provider_factory=Provider)
    assert len(report["rows"]) == 4 and report["summary"]["errors"] == 4
    assert all(row["measurement_error"]["stage"] == "workflow" and row["report"]["state"] == "unavailable" for row in report["rows"])
    assert all(row["stage_timings_ms"]["workflow_ms"] >= 0 for row in report["rows"])


async def test_deliberately_incomplete_control_cannot_reconstruct_evidence_or_call_model(tmp_path, monkeypatch):
    called = []
    async def forbidden(*args, **kwargs):
        called.append(1)
        pytest.fail("incomplete control invoked product workflow")
    monkeypatch.setattr(runner._workflow_module(), "run_triage_v4", forbidden)
    result = await runner.workflow_bridge({"truncated": True}, project=tmp_path, manifest={},
        work_dir=tmp_path / "unused", config_path=tmp_path / "absent", endpoint="http://127.0.0.1:8792")
    assert result["status"] == "guarded" and not result["model_called"] and called == []


async def test_bridge_preserves_declared_async_test_semantics_for_both_groups(tmp_path, monkeypatch):
    seen = []
    async def workflow(project, tests, regressions, work_dir, config_path, **kwargs):
        seen.append((tests, regressions))
        return {"state": "unverified"}
    monkeypatch.setattr(runner._workflow_module(), "run_triage_v4", workflow)
    manifest = {"allowed_production_roots": ["src/"], "failing_tests": [".triage/test_case.py"],
        "regression_tests": [".triage/test_regression.py"],
        "test_command": ["python", "-m", "pytest", "-p", "pytest_asyncio.plugin", "-o", "asyncio_mode=auto"]}
    report = await runner.workflow_bridge({"log": "actual failure"}, project=tmp_path / "project", manifest=manifest,
        work_dir=tmp_path / "new", config_path=tmp_path / "config.json", endpoint="http://127.0.0.1:8792")
    expected = ([".triage/test_case.py", "-o", "asyncio_mode=auto"],
                [".triage/test_regression.py", "-o", "asyncio_mode=auto"])
    assert seen == [expected]
    assert report["measurement_test_args"] == {"failing_tests": expected[0], "regression_tests": expected[1]}


def test_neutral_command_is_not_used_as_an_arbitrary_flag_source():
    manifest = {"failing_tests": ["tests/test_synthetic.py"],
                "test_command": ["python", "-m", "pytest", "--deselect", "tests/test_synthetic.py", "-o", "custom=bad"]}
    assert runner.workflow_test_args(manifest, "failing_tests") == ["tests/test_synthetic.py"]


def test_citations_and_sandbox_support_are_not_root_protected_success():
    report = {"state": "hypothesis_supported_by_tests", "citations": ["target.py:2"]}
    scored = runner.score_output(report)
    assert scored["workflow_supported_by_unchanged_tests"] and not scored["root_protected_repair_verified"]
    assert scored["source_citation_correctness"] is None and scored["complete_causal_correctness"] is None


def test_intermediate_generation_patch_and_actual_execution_outcomes_are_separate():
    passed = {"state": "passed", "returncode": 0, "collected": ["test_one"],
              "reports": [{"when": "call", "nodeid": "test_one", "outcome": "passed"}]}
    skipped = {**passed, "reports": [{"when": "call", "nodeid": "test_one", "outcome": "skipped"}]}
    report = {"state": "unverified", "attempts": [{"attempt": 1,
        "generation": {"status": "hypothesis", "result": {}, "raw_output": "actual retained output"},
        "patch": {"replacements": [{"path": "src/app.py"}]}, "candidate": passed, "regression": skipped}]}
    score = runner.score_output(report)
    attempt = score["attempt_outcomes"][0]
    assert attempt["model_request_attempted"] and attempt["structurally_valid_hypothesis"]
    assert attempt["patched_disposable_copy"] and attempt["target_tests_all_passed"]
    assert not attempt["regression_tests_all_passed"] and not score["root_protected_repair_verified"]


async def test_bridge_async_mode_runs_real_async_tests_in_both_groups(fixture, monkeypatch):
    root, config, _ = fixture
    project = root / "async-project"
    (project / "src").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "src/app.py").write_text("def add(a, b):\n    return a - b\n")
    (project / "tests/test_bug.py").write_text(
        "from pathlib import Path\nimport runpy\n"
        "add = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'src/app.py'))['add']\n"
        "async def test_bug():\n    assert add(2, 3) == 5\n")
    (project / "tests/test_existing.py").write_text("async def test_existing():\n    assert 2 + 2 == 4\n")
    class IdentityProvider:
        model, expected_build = "fake", "fake-build"
    generations = []
    async def generation(context, **kwargs):
        generations.append(context)
        return {"status": "needs_review", "result": None}
    monkeypatch.setattr(runner._workflow_module(), "generate_hypothesis", generation)
    manifest = {"allowed_production_roots": ["src/"], "failing_tests": ["tests/test_bug.py"],
        "regression_tests": ["tests/test_existing.py"], "test_command": ["python", "-m", "pytest", "-o", "asyncio_mode=auto"]}
    report = await runner.workflow_bridge({"log": "observed failure"}, project=project, manifest=manifest,
        work_dir=root / "missing-workflow-parent" / "nested" / "async-copy", config_path=config,
        endpoint="http://127.0.0.1:8792", provider=IdentityProvider())
    assert report["baseline"]["state"] == "failed" and report["baseline"]["executed_count"] == 1
    assert report["baseline_regression"]["state"] == "passed" and report["baseline_regression"]["executed_count"] == 1
    assert len(generations) == 1


async def test_bridge_rejects_inside_project_before_creating_parent(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    manifest = {"allowed_production_roots": ["src/"], "failing_tests": ["tests/test_case.py"],
                "regression_tests": ["tests/test_existing.py"]}
    with pytest.raises(ValueError, match="outside the protected project"):
        await runner.workflow_bridge({"log": "observed failure"}, project=project, manifest=manifest,
            work_dir=project / "new-parent" / "snapshot", config_path=tmp_path / "not-read.json",
            endpoint="http://127.0.0.1:8792")
    assert not (project / "new-parent").exists()


async def test_returned_workflow_errors_cannot_be_reported_as_zero_errors(fixture):
    _, _, lab = fixture
    proof_file(fixture)
    async def callback(evidence, **kwargs):
        return {"state": "unverified", "reason": "workflow_guard_or_runtime_failure",
                "errors": [{"type": "ValueError", "message": "Observed snapshot parent failure"}], "attempts": []}
    report = await runner.run(args(fixture), lab_api=lab, callback=callback, provider_factory=Provider)
    assert report["summary"]["errors"] == report["summary"]["workflow_errors"] == 2
    assert report["summary"]["measurement_errors"] == 0
    assert report["summary"]["first_observation_attempt_counts"]["model_request_attempted"] == 0


def test_exports_refuse_aliases_and_any_overwrite(fixture):
    root, _, _ = fixture
    first, second = root / "first.json", root / "second.json"
    with pytest.raises(ValueError, match="distinct"):
        runner.export_report({}, public_output=first, private_output=first)
    runner.export_report({"raw_protocol_trace": "private"}, public_output=first, private_output=second)
    with pytest.raises(ValueError, match="never overwritten"):
        runner.export_report({}, public_output=first, private_output=second)


def test_export_cannot_write_into_protected_case(fixture):
    root, _, _ = fixture
    case = root / "protected-case"
    with pytest.raises(ValueError, match="outside every protected case"):
        runner.export_report({"rows": [{"case_dir": str(case)}]},
            public_output=case / "new-report.json", private_output=root / "new-private.json")


def test_proof_duplicates_and_nonexecuting_phases_rejected(fixture):
    proof = json.loads(proof_file(fixture).read_text())
    runner._validate_proof(proof, ("d01", "d02"))
    duplicate = deepcopy(proof)
    duplicate["cases"].append(duplicate["cases"][0])
    with pytest.raises(ValueError, match="duplicate"):
        runner._validate_proof(duplicate, ("d01", "d02"))
    proof["cases"][0]["mutation"]["timed_out"] = True
    with pytest.raises(ValueError, match="integrity"):
        runner._validate_proof(proof, ("d01", "d02"))
