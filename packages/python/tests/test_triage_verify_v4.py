import json
from pathlib import Path
import time

import pytest

from decilist import triage_verify_v4 as runner


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "original"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "sample.py").write_text(
        "def compute(value):\n    if value == 2:\n        return 3\n    return value + 1\n", encoding="utf-8")
    (root / "tests" / "test_sample.py").write_text(
        "from pathlib import Path\nimport sample\n\n"
        "def test_failing():\n    assert Path(sample.__file__).is_relative_to(Path.cwd())\n    assert sample.compute(2) == 4\n", encoding="utf-8")
    (root / "tests" / "test_regression.py").write_text(
        "from sample import compute\n\ndef test_regression():\n    assert compute(0) == 1\n", encoding="utf-8")
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    return root


def patch():
    return [{"path": "src/sample.py", "old": "return 3", "new": "return 4"}]


def verify(project, tmp_path, **kwargs):
    return runner.verify_candidate(project, tmp_path / "copy", ["tests/test_sample.py", "-q"], ["tests/test_regression.py", "-q"], patch(), **kwargs)


def test_real_candidate_runs_identical_failure_and_existing_regressions_without_original_edits(project, tmp_path):
    before = {path.relative_to(project).as_posix(): path.read_bytes() for path in project.rglob("*") if path.is_file()}
    result = verify(project, tmp_path, capture_failure_text=True)
    assert result["state"] == "hypothesis_supported_by_tests", result
    assert result["baseline"]["state"] == "failed"
    assert result["candidate"]["state"] == result["regression"]["state"] == "passed"
    assert result["baseline"]["pytest_args"] == result["candidate"]["pytest_args"]
    assert result["baseline"]["collected"] == result["candidate"]["collected"]
    assert result["test_selection_unchanged"] and result["original_unchanged"]
    assert "not complete proof" in result["explanation"]
    assert "FAILED" in result["baseline"]["stdout"]
    for name in ("baseline", "candidate", "regression"):
        run = result[name]
        assert run["snapshot_unchanged"] and run["original_unchanged"]
        assert run["elapsed_seconds"] > 0 and run["collected_count"] == run["executed_count"] == 1
        assert Path(run["stdout_path"]).is_file() and Path(run["audit_path"]).is_file()
        assert "-I" in run["command"] and "-B" in run["command"]
    trace = json.loads(Path(result["baseline"]["trace_path"]).read_text(encoding="utf-8"))
    assert trace["failures"] and trace["failures"][0]["failure_text_captured"]
    assert {path.relative_to(project).as_posix(): path.read_bytes() for path in project.rglob("*") if path.is_file()} == before


def test_baseline_pass_is_unverified_and_does_not_apply_candidate(project, tmp_path):
    (project / "tests/test_sample.py").write_text("def test_pass():\n    assert True\n", encoding="utf-8")
    result = verify(project, tmp_path)
    assert result["state"] == "unverified" and result["baseline"]["state"] == "passed"
    assert result["patch"] is result["candidate"] is result["regression"] is None
    assert (Path(result["snapshot_root"]) / "src/sample.py").read_bytes() == (project / "src/sample.py").read_bytes()


def test_empty_collection_and_only_skipped_tests_cannot_support_candidate(project, tmp_path):
    (project / "tests/test_sample.py").write_text("VALUE = 1\n", encoding="utf-8")
    result = verify(project, tmp_path)
    assert result["state"] == "unverified" and result["baseline"]["state"] == "empty"
    assert result["baseline"]["collected_count"] == 0
    (project / "tests/test_sample.py").write_text("import pytest\n@pytest.mark.skip(reason='skip')\ndef test_skipped():\n    assert False\n", encoding="utf-8")
    result = runner.verify_candidate(project, tmp_path / "copy_skipped", ["tests/test_sample.py"], ["tests/test_regression.py"], patch())
    assert result["state"] == "unverified" and result["baseline"]["state"] == "empty"


def test_collection_error_is_not_a_reproduced_test_failure(project, tmp_path):
    (project / "tests/test_sample.py").write_text("raise RuntimeError('during collection')\n", encoding="utf-8")
    result = verify(project, tmp_path)
    assert result["state"] == "unverified" and result["baseline"]["state"] != "failed"


def test_failed_regression_rejects_candidate(project, tmp_path):
    (project / "tests/test_regression.py").write_text("from sample import compute\ndef test_regression():\n    assert compute(2) == 3\n", encoding="utf-8")
    result = verify(project, tmp_path)
    assert result["state"] == "failed"
    assert result["candidate"]["state"] == "passed" and result["regression"]["state"] == "failed"


def test_candidate_cannot_replace_original_failure_with_a_skip(project, tmp_path):
    (project / "src/sample.py").write_text(
        "import pytest\ndef compute(value):\n    if value == -999:\n        pytest.skip('unavailable')\n    if value == 2:\n        return 3\n    return value + 1\n", encoding="utf-8")
    with (project / "tests/test_sample.py").open("a", encoding="utf-8") as handle:
        handle.write("\ndef test_other_still_passes():\n    assert True\n")
    result = runner.verify_candidate(project, tmp_path / "copy", ["tests/test_sample.py"], ["tests/test_regression.py"], [{"path": "src/sample.py", "old": "value == -999", "new": "value == 2"}])
    assert result["candidate"]["state"] == "passed"
    assert result["state"] == "failed" and not result["originally_failing_tests_now_pass"]


def test_snapshot_exclusions_limits_hidden_tests_and_owned_cleanup(project, tmp_path):
    for relative in (".git/config", ".venv/secret.py", "node_modules/lib/a.js", "build/out.py", ".pytest_cache/state", ".env", ".env.example", "private.key", "id_rsa", "credentials.json"):
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("DO NOT COPY", encoding="utf-8")
    (project / ".triage").mkdir()
    (project / ".triage/test_case.py").write_text("def test_case(): pass", encoding="utf-8")
    (project / "large.gguf").write_bytes(b"x" * 200)
    snapshot = runner.snapshot_project(project, tmp_path / "copy", limits=runner.SnapshotLimits(max_file_bytes=1000, max_binary_bytes=100))
    assert ".triage/test_case.py" in snapshot.source_hashes
    assert len(snapshot.source_hashes) == 5
    assert "large.gguf" not in snapshot.source_hashes
    assert all("DO NOT COPY" not in path.read_text(encoding="utf-8") for path in snapshot.root.rglob("*") if path.is_file())
    assert runner.validate_pytest_args(snapshot, [".triage/test_case.py"]) == [".triage/test_case.py"]
    runner.cleanup_snapshot(snapshot)
    assert not snapshot.directory.exists() and project.exists()
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "one.py").write_text("ONE = 1", encoding="utf-8")
    (clean / "two.py").write_text("TWO = 2", encoding="utf-8")
    with pytest.raises(ValueError, match="file or byte"):
        runner.snapshot_project(clean, tmp_path / "too_many", limits=runner.SnapshotLimits(max_files=1))
    assert not (tmp_path / "too_many").exists()
    with pytest.raises(ValueError, match="file or byte"):
        runner.snapshot_project(clean, tmp_path / "too_large", limits=runner.SnapshotLimits(max_total_bytes=1))


def test_snapshot_rejects_existing_nested_and_escape_paths(project, tmp_path):
    for destination in (project, project / "nested", tmp_path):
        with pytest.raises(ValueError):
            runner.snapshot_project(project, destination)
    link = project / "escape"
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("This Windows account cannot create symlinks")
    with pytest.raises(ValueError, match="symbolic links|reparse"):
        runner.snapshot_project(project, tmp_path / "copy")
    assert not (tmp_path / "copy").exists()


def test_cleanup_refuses_foreign_marker(project, tmp_path):
    snapshot = runner.snapshot_project(project, tmp_path / "copy")
    marker = snapshot.directory / ".decilist-v4-owner.json"
    marker.write_text('{"identity":"foreign"}', encoding="utf-8")
    with pytest.raises(ValueError, match="ownership"):
        runner.cleanup_snapshot(snapshot)
    assert snapshot.root.is_dir()


@pytest.mark.parametrize("arguments", [
    ["tests/test_sample.py", "-p", "evil_plugin"],
    ["tests/test_sample.py", "-p", "no:_decilist_v4_owned_trace"],
    ["tests/test_sample.py", "-o", "pythonpath=/elsewhere"],
    ["tests/test_sample.py", "-c", "other.ini"],
    ["tests/test_sample.py", "--rootdir=/elsewhere"],
    ["tests/test_sample.py", "--basetemp=/elsewhere"],
    ["tests/test_sample.py", "--junitxml=stolen.xml"],
    ["tests/test_sample.py", "--collect-only"],
    ["tests/test_sample.py", "--showlocals"],
    ["../outside.py"], ["C:/outside.py"], ["tests/test_sample.py\nwhoami"], ["-q"],
])
def test_command_controls_are_rejected_before_execution(project, tmp_path, arguments):
    snapshot = runner.snapshot_project(project, tmp_path / "copy")
    with pytest.raises(ValueError):
        runner.run_pytest(snapshot, arguments)
    assert not list(snapshot.evidence.iterdir())


def test_known_async_controls_are_supported(project, tmp_path):
    (project / "tests/test_sample.py").write_text("async def test_async():\n    assert True\n", encoding="utf-8")
    snapshot = runner.snapshot_project(project, tmp_path / "copy")
    result = runner.run_pytest(snapshot, ["tests/test_sample.py", "-p", "pytest_asyncio.plugin", "-o", "asyncio_mode=auto", "-q"])
    assert result["state"] == "passed", result


@pytest.mark.parametrize("relative", ["tests/test_sample.py", "src/test_case.py", "src/config.py", "src/settings.py", "src/package_config.py", "src/manifest.py", "src/config/options.py", ".triage/test_case.py", "pytest.ini", "../outside.py"])
def test_patch_rejects_tests_config_manifests_and_traversal(project, tmp_path, relative):
    target = project / relative
    if ".." not in relative:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("VALUE = 1\n", encoding="utf-8")
    snapshot = runner.snapshot_project(project, tmp_path / "copy")
    with pytest.raises(ValueError):
        runner.apply_replacements(snapshot, [{"path": relative, "old": "VALUE = 1", "new": "VALUE = 2"}])
    assert runner._manifest(snapshot.root, snapshot.limits) == snapshot.source_hashes


def test_patch_requires_exact_single_match_and_compilation_before_any_writes(project, tmp_path):
    snapshot = runner.snapshot_project(project, tmp_path / "copy")
    for row in ({"path": "src/sample.py", "old": "return", "new": "yield"}, {"path": "src/sample.py", "old": "missing", "new": "other"}, {"path": "src/sample.py", "old": "return 3", "new": "return ("}, {"path": "src/sample.py", "old": "return 3", "new": "return 4", "extra": "no"}):
        with pytest.raises((ValueError, SyntaxError)):
            runner.apply_replacements(snapshot, [row])
        assert runner._manifest(snapshot.root, snapshot.limits) == snapshot.source_hashes


def test_same_file_disjoint_replacements_are_applied_against_original_atomically(project, tmp_path):
    snapshot = runner.snapshot_project(project, tmp_path / "copy")
    replacement = [*patch(), {"path": "src/sample.py", "old": "return value + 1", "new": "return value + 2"}]
    result = runner.apply_replacements(snapshot, replacement)
    text = (snapshot.root / "src/sample.py").read_text(encoding="utf-8")
    assert "return 4" in text and "return value + 2" in text
    assert len(result["replacements"]) == 2
    assert result["replacements"][0]["sha256_after"] == result["replacements"][1]["sha256_after"]
    other = runner.snapshot_project(project, tmp_path / "overlap")
    with pytest.raises(ValueError, match="overlap"):
        runner.apply_replacements(other, [*patch(), {"path": "src/sample.py", "old": "        return 3", "new": "        return 5"}])
    assert runner._manifest(other.root, other.limits) == other.source_hashes


def test_flat_module_root_requires_explicit_permission_and_keeps_tests_protected(project, tmp_path):
    (project / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    snapshot = runner.snapshot_project(project, tmp_path / "copy")
    replacement = [{"path": "app.py", "old": "return 1", "new": "return 2"}]
    with pytest.raises(ValueError, match="permitted production"):
        runner.apply_replacements(snapshot, replacement)
    with pytest.raises(ValueError, match="permitted production"):
        runner.apply_replacements(snapshot, [{"path": "tests/test_sample.py", "old": "== 4", "new": "== 3"}], allowed_production_roots=["."])
    approved = runner.apply_replacements(snapshot, replacement, allowed_production_roots=["."])
    assert runner.check_snapshot_integrity(snapshot, expected_hashes=approved["hashes_after"])["snapshot_unchanged"]


def test_approved_patch_tampering_is_detected_before_candidate_execution(project, tmp_path, monkeypatch):
    original_apply = runner.apply_replacements

    def tamper(snapshot, replacements, **kwargs):
        result = original_apply(snapshot, replacements, **kwargs)
        (snapshot.root / "src/sample.py").write_text("def compute(value):\n    return 4\n", encoding="utf-8")
        return result

    monkeypatch.setattr(runner, "apply_replacements", tamper)
    result = verify(project, tmp_path)
    assert result["state"] == "failed" and result["reason"] == "candidate_integrity_failure"
    assert result["candidate"] is None and result["original_unchanged"]


def test_test_file_alteration_during_baseline_is_detected(project, tmp_path):
    (project / "tests/test_sample.py").write_text(
        "from pathlib import Path\ndef test_tamper():\n    Path(__file__).write_text('def test_pass(): pass\\n')\n    assert False\n", encoding="utf-8")
    original = (project / "tests/test_sample.py").read_bytes()
    result = verify(project, tmp_path)
    assert result["state"] == "failed" and result["reason"] == "baseline_integrity_failure"
    assert result["baseline"]["snapshot_unchanged"] is False
    assert (project / "tests/test_sample.py").read_bytes() == original


def test_candidate_cannot_pass_by_changing_existing_regression_tests(project, tmp_path):
    # The effect already exists in the trusted source: AST screening cannot prove
    # its safety. Post-run hashes still detect activation by a changed branch.
    (project / "src/sample.py").write_text(
        "from pathlib import Path\n"
        "def compute(value):\n"
        "    if value == -999:\n"
        "        Path('tests/test_regression.py').write_text('def test_regression(): pass\\n')\n"
        "    if value == 2:\n        return 3\n    return value + 1\n", encoding="utf-8")
    malicious = [*patch(), {"path": "src/sample.py", "old": "value == -999", "new": "value == 2"}]
    result = runner.verify_candidate(project, tmp_path / "copy", ["tests/test_sample.py"], ["tests/test_regression.py"], malicious)
    assert result["state"] == "failed" and result["reason"] == "candidate_integrity_failure"
    assert result["regression"] is None and result["original_unchanged"]


@pytest.mark.parametrize("new", [
    "import os\n        return 4", "return eval('4')", "return __import__('os')", "return open('changed', 'w')", "return value.__class__", "return unknown_helper(value)", "return getattr(value, 'name')",
])
def test_conservative_ast_screen_rejects_new_imports_dunders_and_effect_surfaces(project, tmp_path, new):
    snapshot = runner.snapshot_project(project, tmp_path / "copy")
    with pytest.raises(ValueError, match="Conservative patch screening"):
        runner.apply_replacements(snapshot, [{"path": "src/sample.py", "old": "return 3", "new": new}])
    assert runner.check_snapshot_integrity(snapshot)["snapshot_unchanged"]


def test_conservative_ast_screen_permits_new_pure_builtin_calls(project, tmp_path):
    snapshot = runner.snapshot_project(project, tmp_path / "copy")
    approved = runner.apply_replacements(snapshot, [{"path": "src/sample.py", "old": "return 3", "new": "return len([1, 2, 3, 4])"}])
    assert approved["screening"] == "conservative_ast_change_screen_v1"
    assert runner.check_snapshot_integrity(snapshot, expected_hashes=approved["hashes_after"])["snapshot_unchanged"]


def test_timeout_terminates_owned_process_tree_and_retains_output(project, tmp_path):
    (project / "tests/test_sample.py").write_text("import time\ndef test_slow():\n    print('started', flush=True)\n    time.sleep(30)\n", encoding="utf-8")
    snapshot = runner.snapshot_project(project, tmp_path / "copy")
    tick = time.perf_counter()
    result = runner.run_pytest(snapshot, ["tests/test_sample.py", "-s"], timeout_seconds=1)
    assert result["state"] == "timeout" and result["timed_out"]
    assert result["process_cleanup"] == "owned_process_tree_terminated"
    assert time.perf_counter() - tick < 12
    assert result["original_unchanged"] and result["snapshot_unchanged"]


def test_timeout_also_terminates_descendants(project, tmp_path):
    (project / "tests/test_sample.py").write_text(
        "import os\nfrom pathlib import Path\nimport subprocess\nimport sys\nimport time\n"
        "def test_spawn():\n"
        "    marker = Path(os.environ['TMP']) / 'descendant_survived.txt'\n"
        "    program = 'import pathlib, time; pathlib.Path(' + repr(str(marker.with_suffix('.ready'))) + ').write_text(\\\"started\\\"); time.sleep(2.5); pathlib.Path(' + repr(str(marker)) + ').write_text(\\\"survived\\\")'\n"
        "    compile(program, '<child>', 'exec')\n"
        "    subprocess.Popen([sys.executable, '-c', program])\n"
        "    print('descendant started', flush=True)\n"
        "    time.sleep(30)\n", encoding="utf-8")
    snapshot = runner.snapshot_project(project, tmp_path / "copy")
    result = runner.run_pytest(snapshot, ["tests/test_sample.py", "-s"], timeout_seconds=1.5)
    assert result["state"] == "timeout" and "descendant started" in result["stdout"]
    assert list(snapshot.evidence.rglob("descendant_survived.ready"))
    time.sleep(2.8)
    assert not list(snapshot.evidence.rglob("descendant_survived.txt"))


def test_sanitized_environment_and_default_trace_do_not_capture_failure_text(project, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "never-forward-this")
    monkeypatch.setenv("GITHUB_TOKEN", "never-forward-that")
    monkeypatch.setenv("PYTHONPATH", "do-not-inherit")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--showlocals")
    env = runner.sanitized_environment(temporary_directory=tmp_path)
    assert not {"OPENAI_API_KEY", "GITHUB_TOKEN", "PYTHONPATH", "PYTEST_ADDOPTS", "HOME", "USERPROFILE"} & env.keys()
    assert env["TEMP"] == env["TMP"] == env["TMPDIR"] == str(tmp_path)
    assert "PATH" in {key.upper() for key in env}
    snapshot = runner.snapshot_project(project, tmp_path / "copy")
    result = runner.run_pytest(snapshot, ["tests/test_sample.py", "-q"])
    trace = json.loads(Path(result["trace_path"]).read_text(encoding="utf-8"))
    assert trace["failures"] and not trace["failures"][0]["failure_text_captured"]
    assert "never-forward" not in json.dumps(trace)


def test_original_source_changes_are_rejected_before_execution(project, tmp_path):
    snapshot = runner.snapshot_project(project, tmp_path / "copy")
    (project / "src/sample.py").write_text("CHANGED = True\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Original project changed"):
        runner.run_pytest(snapshot, ["tests/test_sample.py"])
