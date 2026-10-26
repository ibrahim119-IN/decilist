import asyncio
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import decilist
import httpx

from decilist import triage_v6 as workflow
from decilist import triage_verify_v6 as runner


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "trusted"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src/sample.py").write_text("def compute(value):\n    if value == 2:\n        return 3\n    return value + 1\n", encoding="utf-8")
    (root / "tests/test_failure.py").write_text("from sample import compute\n\ndef test_failure():\n    assert compute(2) == 4\n", encoding="utf-8")
    (root / "tests/test_regression.py").write_text("from sample import compute\n\ndef test_regression():\n    assert compute(0) == 1\n", encoding="utf-8")
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    config = tmp_path / "identity.json"
    config.write_text(json.dumps({"model": "stub-local", "expected_build": "b11889-d854d98ba", "generation_profile": "qwen3_coder_non_reasoning", "expected_chat_template_sha256": "a" * 64}), encoding="utf-8")
    return root, config


def generation(context, new="return 4"):
    source = next(row for row in context["functions"] if row["path"] == "src/sample.py" and row["editable"])
    return {"status": "hypothesis", "raw_completion": {"content": "retained raw completion", "timings": {"predicted_ms": 1}},
            "result": {"hypothesis": "The special branch returns one less than the selected test expects.",
                       "evidence_ids": [context["failures"][0]["evidence_id"], source["evidence_id"]],
                       "expected_test_behavior": "Returning four from this branch should satisfy the selected assertion.",
                       "replacements": [{"path": "src/sample.py", "old": "return 3", "new": new}]}}


def stub_generation(monkeypatch, candidates=("return 4",)):
    calls = []
    async def generate(context, *, provider, feedback=None):
        calls.append({"context": context, "feedback": feedback})
        return generation(context, candidates[min(len(calls) - 1, len(candidates) - 1)])
    monkeypatch.setattr(workflow, "generate_hypothesis", generate)
    return calls


def execute(project, tmp_path, **kwargs):
    root, config = project
    return asyncio.run(workflow.run_triage_v6(root, ["tests/test_failure.py"], ["tests/test_regression.py"],
        tmp_path / "experiment", config, provider=SimpleNamespace(model="stub-local", expected_build="b11889-d854d98ba", generation_profile="qwen3_coder_non_reasoning", expected_chat_template_sha256="a" * 64), **kwargs))


def test_real_workflow_retains_runtime_context_diff_and_test_evidence_without_applying(project, tmp_path, monkeypatch):
    root, _ = project
    before = {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    calls = stub_generation(monkeypatch)
    report = execute(project, tmp_path)
    assert report["state"] == workflow.SUPPORTED, report
    assert report["baseline"]["state"] == "failed"
    assert report["baseline_regression"]["state"] == "passed"
    attempt = report["attempts"][0]
    assert attempt["candidate"]["state"] == attempt["regression"]["state"] == "passed"
    assert attempt["candidate"]["pytest_args"] == report["baseline"]["pytest_args"]
    assert attempt["test_selection_unchanged"] and attempt["regression_selection_unchanged"]
    assert len(calls) == 1 and calls[0]["feedback"] is None
    assert report["apply"] == {"requested": False, "applied": False}
    assert {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
    context = json.loads(Path(report["context_path"]).read_text(encoding="utf-8"))
    assert context["kind"] == "runtime_python_context" and len(context["functions"]) == 2
    assert any(row["roles"] == ["test"] and "assert compute(2) == 4" in row["source"] for row in context["functions"])
    assert all(not row["failure_text_captured"] and "dynamic assertion and exception values omitted" in row["longrepr"] for row in context["failures"])
    assert len(json.dumps(context, ensure_ascii=True)) <= workflow.DEFAULT_MAX_CONTEXT_CHARS
    assert "--- a/src/sample.py" in report["candidate_diff"] and "+        return 4" in report["candidate_diff"]
    assert Path(report["diff_path"]).read_bytes().decode("utf-8") == report["candidate_diff"]
    persisted = json.loads(Path(report["report_path"]).read_text(encoding="utf-8"))
    assert persisted["state"] == workflow.SUPPORTED and persisted["timings_ms"]["total"] > 0
    assert persisted["attempts"][0]["generation"]["raw_completion"]["content"] == "retained raw completion"
    assert "not complete proof" in report["explanation"]


def test_explicit_apply_copies_only_verified_production_bytes_and_does_not_rerun_original(project, tmp_path, monkeypatch):
    root, _ = project
    tests_before = (root / "tests/test_failure.py").read_bytes()
    config_before = (root / "pytest.ini").read_bytes()
    stub_generation(monkeypatch)
    calls = []
    real_run = runner.run_pytest
    def run(snapshot, args, **kwargs):
        calls.append((snapshot.root, kwargs["label"]))
        return real_run(snapshot, args, **kwargs)
    monkeypatch.setattr(runner, "run_pytest", run)
    report = execute(project, tmp_path, apply=True)
    assert report["state"] == workflow.SUPPORTED and report["apply"]["applied"], report
    assert "return 4" in (root / "src/sample.py").read_text(encoding="utf-8")
    assert (root / "tests/test_failure.py").read_bytes() == tests_before
    assert (root / "pytest.ini").read_bytes() == config_before
    assert len(calls) == 4 and all(path != root for path, _ in calls)
    changed = report["apply"]["files"]
    assert len(changed) == 1 and changed[0]["sha256_before"] != changed[0]["sha256_after"]


def test_source_changed_after_verification_rejects_apply_without_overwriting_user_edit(project, tmp_path, monkeypatch):
    root, _ = project
    stub_generation(monkeypatch)
    real_apply = workflow.apply_verified_candidate
    def edited_apply(snapshot, patch, **kwargs):
        (root / "src/sample.py").write_text("def compute(value):\n    return 99\n", encoding="utf-8")
        return real_apply(snapshot, patch, **kwargs)
    monkeypatch.setattr(workflow, "apply_verified_candidate", edited_apply)
    report = execute(project, tmp_path, apply=True)
    assert report["state"] == workflow.SUPPORTED and not report["apply"]["applied"]
    assert "changed" in report["apply"]["error"] and "return 99" in (root / "src/sample.py").read_text(encoding="utf-8")


def test_one_actual_test_rejection_allows_one_revision_from_fresh_original(project, tmp_path, monkeypatch):
    calls = stub_generation(monkeypatch, ("return 5", "return 4"))
    report = execute(project, tmp_path, capture_failure_text=True)
    assert report["state"] == workflow.SUPPORTED and len(calls) == 2, report
    first, second = report["attempts"]
    assert first["candidate"]["state"] == "failed" and second["candidate"]["state"] == "passed"
    assert second["snapshot_directory"] != first["snapshot_directory"]
    assert second["patch"]["replacements"][0]["sha256_before"] == report["source_hashes"]["src/sample.py"]
    assert calls[1]["feedback"]["attempt"] == 1 and calls[1]["feedback"]["rejection"]["exit_code"] == 1
    assert "FAILED" in calls[1]["feedback"]["rejection"]["output"]
    assert len(report["attempts"]) == 2


def test_failed_regression_never_supports_hypothesis_and_budget_is_two(project, tmp_path, monkeypatch):
    root, _ = project
    (root / "tests/test_regression.py").write_text("from sample import compute\ndef test_regression():\n    assert compute(2) == 3\n", encoding="utf-8")
    calls = stub_generation(monkeypatch)
    report = execute(project, tmp_path)
    assert report["state"] == "unverified" and len(calls) == 2
    assert all(attempt["candidate"]["state"] == "passed" and attempt["regression"]["state"] == "failed" for attempt in report["attempts"])
    assert "longrepr" not in calls[1]["feedback"]["rejection"]["output"]
    assert not report["apply"]["applied"]


def test_baseline_pass_stops_without_generation_or_patch(project, tmp_path, monkeypatch):
    root, _ = project
    (root / "src/sample.py").write_text("def compute(value):\n    return value + 2\n", encoding="utf-8")
    calls = stub_generation(monkeypatch)
    report = execute(project, tmp_path, apply=True)
    assert report["state"] == "unverified" and report["baseline"]["state"] == "passed"
    assert not calls and not report["attempts"] and not report["apply"]["applied"]


def test_model_failure_retains_response_and_does_not_consume_feedback(project, tmp_path, monkeypatch):
    calls = []
    async def unavailable(context, **kwargs):
        calls.append(context)
        return {"status": "unavailable", "result": None, "raw_completion": {"content": "invalid json"}, "errors": [{"stage": "validation"}]}
    monkeypatch.setattr(workflow, "generate_hypothesis", unavailable)
    report = execute(project, tmp_path)
    assert report["state"] == "unverified" and len(calls) == 1
    assert report["attempts"][0]["generation"]["raw_completion"]["content"] == "invalid json"
    assert report["attempts"][0]["candidate"] is None


def test_supported_hypothesis_without_safe_intervention_is_preserved_and_visible(project, tmp_path, monkeypatch):
    calls = []
    async def no_intervention(context, **kwargs):
        calls.append(context)
        report = generation(context)
        report.update(status="needs_review", reason="no_safe_intervention")
        report["result"]["replacements"] = []
        return report
    monkeypatch.setattr(workflow, "generate_hypothesis", no_intervention)
    report = execute(project, tmp_path)
    assert report["state"] == "unverified" and report["reason"] == "no_safe_intervention"
    assert len(calls) == 1 and report["attempts"][0]["candidate"] is None
    assert report["attempts"][0]["hypothesis"]["replacements"] == []
    assert "Hypothesis: The special branch" in workflow.format_text(report)
    persisted = json.loads(Path(report["report_path"]).read_text(encoding="utf-8"))
    assert persisted["attempts"][0]["generation"]["result"] == report["attempts"][0]["hypothesis"]


def test_patch_guard_failure_does_not_trigger_feedback(project, tmp_path, monkeypatch):
    calls = stub_generation(monkeypatch, ("return eval('4')",))
    report = execute(project, tmp_path)
    assert report["state"] == "unverified" and report["reason"] == "candidate_patch_rejected"
    assert len(calls) == 1 and report["attempts"][0]["candidate"] is None


def test_changed_collection_rejects_passing_candidate_without_feedback(project, tmp_path, monkeypatch):
    calls = stub_generation(monkeypatch)
    real_run = runner.run_pytest
    def run(snapshot, args, **kwargs):
        report = real_run(snapshot, args, **kwargs)
        if kwargs["label"] == "candidate":
            report["collected"] = ["tests/test_other.py::test_other"]
        return report
    monkeypatch.setattr(runner, "run_pytest", run)
    report = execute(project, tmp_path)
    assert report["state"] == "unverified" and report["reason"] == "verification_integrity_failure"
    assert len(calls) == 1


def test_timeout_is_not_an_actual_feedback_test_failure(project, tmp_path, monkeypatch):
    calls = stub_generation(monkeypatch)
    real_run = runner.run_pytest
    def run(snapshot, args, **kwargs):
        report = real_run(snapshot, args, **kwargs)
        if kwargs["label"] == "candidate":
            report.update(state="timeout", timed_out=True, returncode=-1)
        return report
    monkeypatch.setattr(runner, "run_pytest", run)
    report = execute(project, tmp_path)
    assert report["state"] == "unverified" and len(calls) == 1


def test_apply_rejects_modified_verified_copy(project, tmp_path):
    root, _ = project
    snapshot = runner.snapshot_project(root, tmp_path / "candidate")
    patch = runner.apply_replacements(snapshot, [{"path": "src/sample.py", "old": "return 3", "new": "return 4"}])
    (snapshot.root / "src/sample.py").write_text("def compute(value):\n    return 8\n", encoding="utf-8")
    report = workflow.apply_verified_candidate(snapshot, patch)
    assert not report["applied"] and "snapshot changed" in report["error"]
    assert "return 3" in (root / "src/sample.py").read_text(encoding="utf-8")


def test_unified_diff_preserves_missing_final_newline_marker(project, tmp_path):
    root, _ = project
    path = root / "src/no_newline.py"
    path.write_bytes(b"def value():\n    return 1")
    snapshot = runner.snapshot_project(root, tmp_path / "candidate")
    replacement = [{"path": "src/no_newline.py", "old": "return 1", "new": "return 2"}]
    runner.apply_replacements(snapshot, replacement)
    diff = workflow._candidate_diff(snapshot, replacement)
    assert "-    return 1\n\\ No newline at end of file\n+    return 2\n\\ No newline at end of file\n" in diff


def test_apply_rolls_back_completed_file_when_later_atomic_replace_fails(project, tmp_path, monkeypatch):
    root, _ = project
    (root / "src/second.py").write_text("def second():\n    return 1\n", encoding="utf-8")
    snapshot = runner.snapshot_project(root, tmp_path / "candidate")
    patch = runner.apply_replacements(snapshot, [{"path": "src/sample.py", "old": "return 3", "new": "return 4"},
                                                {"path": "src/second.py", "old": "return 1", "new": "return 2"}])
    real_atomic = workflow._atomic_bytes
    calls = []
    def atomic(path, data):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("synthetic replace failure")
        real_atomic(path, data)
    monkeypatch.setattr(workflow, "_atomic_bytes", atomic)
    report = workflow.apply_verified_candidate(snapshot, patch)
    assert not report["applied"] and report["rollback"] == {"restored": ["src/sample.py"], "failed": []}
    assert runner._source_unchanged(snapshot) and len(calls) == 3


def test_existing_work_directory_is_never_reused(project, tmp_path, monkeypatch):
    (tmp_path / "experiment").mkdir()
    (tmp_path / "experiment/keep.txt").write_text("keep", encoding="utf-8")
    calls = stub_generation(monkeypatch)
    report = execute(project, tmp_path)
    assert report["state"] == "unverified" and not calls
    assert (tmp_path / "experiment/keep.txt").read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("context_policy", ["v5", "v6"])
def test_full_workflow_uses_real_hypothesis_validator_with_explicit_native_provider_stub(project, tmp_path, context_policy):
    class NativeStub(workflow.V6LlamaCppProvider):
        async def ready(self):
            return {"model": self.model, "build_info": self.expected_build}

        async def apply_template(self, messages):
            self.context = json.loads(messages[1]["content"])["runtime_context"]
            return "explicit synthetic template"

        async def complete(self, payload):
            assert payload["n_predict"] == 1200 and payload["temperature"] == 0
            return {"model": self.model, "content": json.dumps(generation(self.context)["result"]),
                    "tokens_predicted": 100, "tokens_evaluated": 500, "truncated": False,
                    "stopped_limit": False, "stop": True, "stop_type": "eos", "timings": {"predicted_ms": 1}}

    root, config = project
    async def run():
        async with NativeStub(model="stub-local", expected_build="b11889-d854d98ba", generation_profile="qwen3_coder_non_reasoning", expected_chat_template_sha256="a" * 64) as provider:
            return await workflow.run_triage_v6(root, ["tests/test_failure.py"], ["tests/test_regression.py"],
                tmp_path / "experiment", config, provider=provider, context_policy=context_policy)
    report = asyncio.run(run())
    assert report["state"] == workflow.SUPPORTED, report
    generated = report["attempts"][0]["generation"]
    assert generated["status"] == "hypothesis" and generated["source"] == "local_model_generation"
    assert generated["generation_input_sha256"] and generated["context_sha256"]
    assert generated["raw_completion"]["tokens_predicted"] == 100


def test_explicit_flat_root_apply_still_protects_tests_and_config(project, tmp_path):
    root, _ = project
    (root / "plain.py").write_text("def compute():\n    return 1\n", encoding="utf-8")
    snapshot = runner.snapshot_project(root, tmp_path / "candidate")
    patch = runner.apply_replacements(snapshot, [{"path": "plain.py", "old": "return 1", "new": "return 2"}],
                                      allowed_production_roots=["."])
    report = workflow.apply_verified_candidate(snapshot, patch, allowed_production_roots=["."])
    assert report["applied"] and "return 2" in (root / "plain.py").read_text(encoding="utf-8")
    for protected in ("test_plain.py", "tests/test_plain.py", "conftest.py", "config.py", "settings.py", "src/sample_config.py"):
        with pytest.raises(ValueError, match="production"):
            workflow._production_path(protected, ["."])


def test_provider_identity_mismatch_abstains_before_project_execution(project, tmp_path, monkeypatch):
    root, config = project
    calls = stub_generation(monkeypatch)
    report = asyncio.run(workflow.run_triage_v6(root, ["tests/test_failure.py"], ["tests/test_regression.py"],
        tmp_path / "experiment", config, provider=SimpleNamespace(model="different", expected_build="b11889-d854d98ba", generation_profile="qwen3_coder_non_reasoning", expected_chat_template_sha256="a" * 64)))
    assert report["state"] == "unverified" and not calls and report["baseline"] is None
    assert "identity differs" in report["errors"][0]["message"]


def test_cli_requires_explicit_inputs_and_prints_concise_diff_evidence(monkeypatch, capsys, tmp_path):
    captured = {}
    async def run(*args, **kwargs):
        captured.update(kwargs)
        return {"state": workflow.SUPPORTED, "reason": "failing_and_existing_regression_tests_passed",
                "explanation": runner.EXPLANATION, "attempts": [], "apply": {"requested": kwargs["apply"], "applied": False},
                "candidate_diff": "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-old\n+new\n", "report_path": str(tmp_path / "report.json")}
    monkeypatch.setattr(workflow, "run_triage_v6", run)
    argv = ["--project", str(tmp_path), "--test", "tests/test_a.py", "--regression", "tests/test_b.py",
            "--work-dir", str(tmp_path / "new"), "--model-config", str(tmp_path / "identity.json")]
    assert workflow.main(argv) == 0
    text = capsys.readouterr().out
    assert "--- a/src/a.py" in text and "Evidence:" in text and "not complete proof" in text
    assert not captured["apply"] and not captured["capture_failure_text"]
    with pytest.raises(SystemExit):
        workflow.main([])


@pytest.mark.parametrize("run", [
    {"state": "passed", "returncode": 0, "collected": ["one"], "reports": []},
    {"state": "passed", "returncode": 0, "collected": ["one"], "reports": [{"when": "call", "nodeid": "one", "outcome": "skipped"}]},
    {"state": "passed", "returncode": 0, "collected": ["one", "two"], "reports": [{"when": "call", "nodeid": "one", "outcome": "passed"}]},
])
def test_success_audit_rejects_skips_missing_calls_and_partial_execution(run):
    assert not workflow._all_passed(run)


def test_wall_stage_timings_include_both_attempts_application_and_final_integrity(project, tmp_path, monkeypatch):
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr(workflow, "perf_counter", lambda: clock.value)

    def delayed(target, name, seconds):
        original = getattr(target, name)
        def wrapped(*args, **kwargs):
            clock.value += seconds
            return original(*args, **kwargs)
        monkeypatch.setattr(target, name, wrapped)

    delayed(runner, "snapshot_project", 0.010)
    delayed(runner, "run_pytest", 0.020)
    delayed(workflow, "build_context", 0.030)
    delayed(runner, "apply_replacements", 0.050)
    delayed(workflow, "apply_verified_candidate", 0.060)
    delayed(runner, "_source_unchanged", 0.007)
    generations = []
    async def generate(context, **kwargs):
        clock.value += 0.040
        generations.append(context)
        return generation(context, "return 5" if len(generations) == 1 else "return 4")
    monkeypatch.setattr(workflow, "generate_hypothesis", generate)
    persisted_at = []
    original_write = workflow._write_json
    def write(path, value):
        if path.name == "workflow.json":
            persisted_at.append(clock.value)
        original_write(path, value)
    monkeypatch.setattr(workflow, "_write_json", write)

    report = execute(project, tmp_path, apply=True)
    assert report["state"] == workflow.SUPPORTED and report["apply"]["applied"], report
    timings = report["timings_ms"]
    for stage, minimum in {"snapshot": 20, "baseline": 40, "context": 30, "model": 80,
                           "patch": 100, "verification": 80, "application": 60}.items():
        assert timings[stage] >= minimum - 0.000001
    assert timings["final_integrity"] == pytest.approx(7)
    assert timings["total"] == pytest.approx(sum(timings[stage] for stage in workflow._TIMED_STAGES))
    # Persistence begins after final integrity; its own IO is outside this
    # report's total and remains inside the enclosing caller's wall clock.
    assert timings["total"] == pytest.approx(persisted_at[0] * 1000)
    first, second = report["attempts"]
    assert first["candidate"]["state"] == "failed" and second["candidate"]["state"] == "passed"
    assert first["timings_ms"]["snapshot"] == 0  # initial copy is a shared workflow stage
    assert second["timings_ms"]["snapshot"] == pytest.approx(10)
    assert all(attempt["timings_ms"]["model"] == pytest.approx(40) for attempt in (first, second))
    assert all(attempt["timings_ms"]["total"] >= sum(value for name, value in attempt["timings_ms"].items() if name != "total") - 0.000001
               for attempt in (first, second))
    assert any(event["stage"] == "snapshot" and event.get("attempt") == 2 for event in report["stage_timings"])
    persisted = json.loads(Path(report["report_path"]).read_text(encoding="utf-8"))
    assert persisted["timings_ms"] == timings and persisted["stage_timings"] == report["stage_timings"]


@pytest.mark.parametrize("failed_stage", ["model", "patch", "second_snapshot"])
def test_failed_stage_wall_time_and_failed_attempt_are_retained(project, tmp_path, monkeypatch, failed_stage):
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr(workflow, "perf_counter", lambda: clock.value)
    original_snapshot = runner.snapshot_project
    snapshot_calls = []
    def snapshot(*args, **kwargs):
        clock.value += 0.010
        snapshot_calls.append(args)
        if failed_stage == "second_snapshot" and len(snapshot_calls) == 2:
            raise OSError("synthetic second snapshot failure")
        return original_snapshot(*args, **kwargs)
    monkeypatch.setattr(runner, "snapshot_project", snapshot)
    async def generate(context, **kwargs):
        clock.value += 0.040
        if failed_stage == "model":
            raise RuntimeError("synthetic model failure")
        return generation(context, "return 5" if failed_stage == "second_snapshot" else "return 4")
    monkeypatch.setattr(workflow, "generate_hypothesis", generate)
    original_patch = runner.apply_replacements
    def patch(*args, **kwargs):
        clock.value += 0.050
        if failed_stage == "patch":
            raise ValueError("synthetic patch guard rejection")
        return original_patch(*args, **kwargs)
    monkeypatch.setattr(runner, "apply_replacements", patch)

    report = execute(project, tmp_path)
    assert report["state"] == "unverified"
    stage = "snapshot" if failed_stage == "second_snapshot" else failed_stage
    failed_attempt = report["attempts"][-1]
    assert failed_attempt["timings_ms"][stage] == pytest.approx({"snapshot": 10, "model": 40, "patch": 50}[stage])
    assert any(event["stage"] == stage and event.get("attempt") == failed_attempt["attempt"] for event in report["stage_timings"])
    assert report["timings_ms"][stage] >= failed_attempt["timings_ms"][stage]
    assert report["timings_ms"]["total"] == pytest.approx(clock.value * 1000)
    persisted = json.loads(Path(report["report_path"]).read_text(encoding="utf-8"))
    assert persisted["attempts"][-1]["timings_ms"] == failed_attempt["timings_ms"]
    if failed_stage == "second_snapshot":
        assert len(report["attempts"]) == 2 and failed_attempt["generation"] is None
        assert failed_attempt["snapshot_directory"] is None


def test_existing_async_project_uses_explicit_pytest_options_and_schema5_evidence(project, tmp_path, monkeypatch):
    root, config = project
    (root / "src/sample.py").write_text(
        "import asyncio\n\nasync def compute(value):\n    await asyncio.sleep(0)\n    if value == 2:\n        return 3\n    return value + 1\n", encoding="utf-8")
    (root / "tests/test_failure.py").write_text(
        "from sample import compute\n\nasync def test_failure():\n    assert await compute(2) == 4\n", encoding="utf-8")
    (root / "tests/test_regression.py").write_text(
        "from sample import compute\n\nasync def test_regression():\n    assert await compute(0) == 1\n", encoding="utf-8")
    before = {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    calls = stub_generation(monkeypatch)
    report = asyncio.run(workflow.run_triage_v6(root,
        ["tests/test_failure.py", "-o", "asyncio_mode=auto"],
        ["tests/test_regression.py", "-o", "asyncio_mode=auto"],
        tmp_path / "new" / "nested" / "experiment", config,
        provider=SimpleNamespace(model="stub-local", expected_build="b11889-d854d98ba", generation_profile="qwen3_coder_non_reasoning", expected_chat_template_sha256="a" * 64)))
    assert report["state"] == workflow.SUPPORTED and report["schema_version"] == 6, report
    assert len(calls) == 1 and calls[0]["context"]["schema_version"] == 6
    for run in (report["baseline"], report["baseline_regression"], report["attempts"][0]["candidate"], report["attempts"][0]["regression"]):
        assert run["executed_count"] == 1 and "--worker" in run["command"]
        assert Path(run["command"][3]).name == "triage_verify_v5.py"
        trace = json.loads(Path(run["trace_path"]).read_text(encoding="utf-8"))
        assert trace["schema_version"] == 5 and "coverage" in trace
        request = json.loads(Path(run["audit_path"]).with_name("request.json").read_text(encoding="utf-8"))
        assert Path(request["trace_plugin"]).name == "triage_trace_v5.py"
    assert {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
    assert Path(report["report_path"]).is_file()


@pytest.mark.parametrize("report", [None, {}, {"state": "unverified", "reason": "initial_evidence_guard"},
    {"state": "unverified", "errors": [None], "attempts": [None, {}, {"generation": {}}], "baseline": {"state": "error"}},
    {"apply": None, "attempts": "bad", "hypothesis": {}, "explanation": None},
    {"baseline": {"elapsed_seconds": "bad", "state": "error"}, "errors": [{"message": "guard\x1b[31m"}]},
    {"baseline": {"elapsed_seconds": 10 ** 1000, "state": "error"}, "errors": [{"message": "guard\x9b31m"}]},
])
def test_guarded_and_missing_field_reports_always_format(report):
    text = workflow.format_text(report)
    assert text.startswith("Decilist V6: ") and "not complete proof" in text
    assert "\x1b" not in text and "\x9b" not in text


def test_cli_guard_report_without_explanation_does_not_raise(monkeypatch, capsys, tmp_path):
    async def guard(*args, **kwargs):
        return {"state": "unverified", "reason": "initial_evidence_guard"}
    monkeypatch.setattr(workflow, "run_triage_v6", guard)
    assert workflow.main(["--project", str(tmp_path), "--test", "tests/test_a.py", "--regression", "tests/test_b.py",
        "--work-dir", str(tmp_path / "new"), "--model-config", str(tmp_path / "identity.json")]) == 1
    text = capsys.readouterr().out
    assert "initial_evidence_guard" in text and "not complete proof" in text


def test_actual_module_invocation_handles_configuration_guard_without_model_call(project, tmp_path):
    root, _ = project
    env = dict(os.environ, PYTHONPATH=str(Path(decilist.__file__).parents[1]))
    if Path(workflow.__file__).parent == Path(decilist.__file__).parent:
        command = [sys.executable, "-m", "decilist.triage_v6"]
    else:
        bootstrap = "import decilist,runpy; decilist.__path__=" + repr(list(decilist.__path__)) + "; runpy.run_module('decilist.triage_v6',run_name='__main__')"
        command = [sys.executable, "-c", bootstrap]
    result = subprocess.run([*command, "--project", str(root),
        "--test", "tests/test_failure.py", "--regression", "tests/test_regression.py", "--work-dir", str(tmp_path / "new"),
        "--model-config", str(tmp_path / "missing_identity.json")], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 1 and "unverified" in result.stdout
    assert "not complete proof" in result.stdout and "Traceback" not in result.stderr
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize("apply", [False, True])
def test_crlf_multiline_native_intervention_preserves_exact_source_through_apply(project, tmp_path, apply):
    root, config = project
    original = b"def compute(value):\r\n    if value == 2:\r\n        return 3\r\n    return value + 1\r\n"
    (root / "src/sample.py").write_bytes(original)
    tests_before = {path.name: path.read_bytes() for path in (root / "tests").iterdir()}
    observed_context = []
    class NativeStub(workflow.V6LlamaCppProvider):
        async def ready(self):
            return {"model": self.model, "build_info": self.expected_build}

        async def apply_template(self, messages):
            self.context = json.loads(messages[1]["content"])["runtime_context"]
            observed_context.append(self.context)
            implementation = next(row for row in self.context["functions"] if row["path"] == "src/sample.py")
            assert implementation["source"].encode("utf-8") == original
            return "synthetic CRLF template"

        async def complete(self, payload):
            result = generation(self.context)["result"]
            result["replacements"] = [{"path": "src/sample.py", "old": "if value == 2:\r\n        return 3",
                                       "new": "if value == 2:\r\n        return 4"}]
            return {"model": self.model, "content": json.dumps(result), "tokens_predicted": 100,
                    "tokens_evaluated": 500, "truncated": False, "stopped_limit": False,
                    "stop": True, "stop_type": "eos", "timings": {"predicted_ms": 1}}

    async def run():
        async with NativeStub(model="stub-local", expected_build="b11889-d854d98ba", generation_profile="qwen3_coder_non_reasoning", expected_chat_template_sha256="a" * 64) as provider:
            return await workflow.run_triage_v6(root, ["tests/test_failure.py"], ["tests/test_regression.py"],
                tmp_path / "experiment", config, provider=provider, apply=apply)
    report = asyncio.run(run())
    assert report["state"] == workflow.SUPPORTED, report
    assert len(observed_context) == 1 and report["attempts"][0]["generation"]["status"] == "hypothesis"
    expected = original.replace(b"return 3", b"return 4")
    assert (root / "src/sample.py").read_bytes() == (expected if apply else original)
    assert (Path(report["verified_snapshot"]) / "src/sample.py").read_bytes() == expected
    assert report["apply"]["applied"] is apply
    assert {path.name: path.read_bytes() for path in (root / "tests").iterdir()} == tests_before
    assert report["attempts"][0]["candidate"]["state"] == report["attempts"][0]["regression"]["state"] == "passed"


@pytest.mark.parametrize("policy,schema", [("v5", 5), ("v6", 6)])
def test_context_policy_is_explicit_and_same_v6_generator_is_used(project, tmp_path, monkeypatch, policy, schema):
    calls = stub_generation(monkeypatch)
    report = execute(project, tmp_path, context_policy=policy)
    assert report["state"] == workflow.SUPPORTED, report
    assert report["schema_version"] == 6 and report["context_policy"] == policy
    assert report["context_schema_version"] == schema and calls[0]["context"]["schema_version"] == schema
    assert report["generation_module"] == "decilist.triage_hypothesis_v6"
    assert report["trace_schema_version"] == 5 and report["test_runner"] == "decilist.triage_verify_v5"
    persisted = json.loads(Path(report["report_path"]).read_text(encoding="utf-8"))
    assert persisted["context_policy"] == policy


def test_invalid_context_policy_abstains_before_project_or_provider_execution(project, tmp_path, monkeypatch):
    calls = stub_generation(monkeypatch)
    report = execute(project, tmp_path, context_policy="arbitrary")
    assert report["state"] == "unverified" and report["baseline"] is None and not calls
    assert not (tmp_path / "experiment").exists()


def test_work_directory_inside_source_is_rejected_without_recursive_hashing(project, monkeypatch):
    root, config = project
    calls = stub_generation(monkeypatch)
    report = asyncio.run(workflow.run_triage_v6(root, ["tests/test_failure.py"], ["tests/test_regression.py"],
        root / "output" / "next", config,
        provider=SimpleNamespace(**json.loads(config.read_text(encoding="utf-8")))))
    assert report["state"] == "unverified" and report["baseline"] is None and not calls
    assert not (root / "output").exists() and report["source_hashes"] is None


@pytest.mark.parametrize("field,value", [("generation_profile", "arbitrary"), ("expected_chat_template_sha256", "not-a-hash"),
    ("generation_profile", True)])
def test_profile_config_is_explicit_and_bounded(project, field, value):
    _, config = project
    data = json.loads(config.read_text(encoding="utf-8"))
    data[field] = value
    config.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        workflow.load_model_config(config)


@pytest.mark.parametrize("policy", ["v5", "v6"])
@pytest.mark.parametrize("profile,suffix", [
    ("qwen3_coder_non_reasoning", "<|im_start|>assistant\n"),
    ("qwen3_6_closed_thinking", "<|im_start|>assistant\n<think>\n\n</think>\n\n"),
])
def test_same_native_v6_generation_and_guards_under_both_policies(project, tmp_path, policy, profile, suffix):
    root, config = project
    template = "synthetic-native-template"
    digest = hashlib.sha256(template.encode()).hexdigest()
    settings = json.loads(config.read_text(encoding="utf-8"))
    settings.update(generation_profile=profile, expected_chat_template_sha256=digest)
    config.write_text(json.dumps(settings), encoding="utf-8")
    state = {"context": None, "completion": None}
    def handle(request):
        body = json.loads(request.content) if request.content else None
        if request.url.path == "/health":
            result = {"status": "ok"}
        elif request.url.path == "/props":
            result = {"model_alias": "stub-local", "build_info": "b11889-d854d98ba", "model_path": "models/stub.gguf",
                      "is_sleeping": False, "chat_template": template, "default_generation_settings": {"n_ctx": 16384}}
        elif request.url.path == "/v1/models":
            result = {"data": [{"id": "stub-local", "meta": {"n_vocab": 256, "n_ctx": 16384, "n_ctx_train": 32768}}]}
        elif request.url.path == "/apply-template":
            assert set(body) == {"messages"}
            state["context"] = json.loads(body["messages"][1]["content"])["runtime_context"]
            result = {"prompt": "".join(message["content"] for message in body["messages"]) + suffix}
        elif request.url.path == "/completion":
            state["completion"] = body
            result = {"model": "stub-local", "content": json.dumps(generation(state["context"])["result"]),
                "tokens_predicted": 100, "tokens_evaluated": 500, "truncated": False, "stopped_limit": False,
                "stop": True, "stop_type": "eos", "timings": {"predicted_ms": 1}}
        else:
            raise AssertionError("Unexpected native stub endpoint")
        return httpx.Response(200, json=result)
    async def run():
        async with workflow.V6LlamaCppProvider(**settings, transport=httpx.MockTransport(handle)) as provider:
            return await workflow.run_triage_v6(root, ["tests/test_failure.py"], ["tests/test_regression.py"],
                tmp_path / "experiment", config, provider=provider, context_policy=policy)
    report = asyncio.run(run())
    assert report["state"] == workflow.SUPPORTED, report
    generated = report["attempts"][0]["generation"]
    assert generated["generation_mode_proof"]["status"] == "verified"
    assert generated["generation_mode_proof"]["actual_prompt_suffix"] == suffix
    assert state["context"]["schema_version"] == (5 if policy == "v5" else 6)
    expected = {"n_predict": 1200, "temperature": 0, "seed": 42, "samplers": ["temperature"],
                "n_probs": 0, "top_k": 0, "top_p": 1, "min_p": 0}
    assert {key: state["completion"][key] for key in expected} == expected
