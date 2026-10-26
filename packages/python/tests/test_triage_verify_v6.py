import hashlib
import json
from pathlib import Path

import pytest

from decilist import triage_verify_v4 as legacy
from decilist import triage_verify_v5 as frozen_worker
from decilist import triage_verify_v6 as runner


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "original"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src/sample.py").write_text("def compute():\n    return 3\n", encoding="utf-8")
    (root / "tests/test_sample.py").write_text("from sample import compute\ndef test_value():\n    assert compute() == 4\n", encoding="utf-8")
    return root


def test_v6_uses_frozen_worker_without_modifying_v4_primitives(project, tmp_path):
    source = Path(legacy.__file__)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    original_run = legacy.run_pytest
    snapshot = runner.snapshot_project(project, tmp_path / "missing" / "parents" / "copy")
    assert runner.run_pytest is frozen_worker.run_pytest
    run = runner.run_pytest(snapshot, ["tests/test_sample.py"], capture_failure_text=True)
    assert run["state"] == "failed" and run["snapshot_unchanged"] and run["original_unchanged"]
    assert Path(run["command"][3]).name == "triage_verify_v5.py" and run["command"][4] == "--worker"
    trace = json.loads(Path(run["trace_path"]).read_text(encoding="utf-8"))
    assert trace["schema_version"] == 5 and trace["failures"]
    assert legacy.run_pytest is original_run and hashlib.sha256(source.read_bytes()).hexdigest() == digest
    approved = runner.apply_replacements(snapshot, [{"path": "src/sample.py", "old": "return 3", "new": "return 4"}])
    passed = runner.run_pytest(snapshot, ["tests/test_sample.py"], expected_hashes=approved["hashes_after"])
    assert passed["state"] == "passed" and passed["collected"] == run["collected"]
    with pytest.raises(ValueError, match="approved"):
        runner.run_pytest(snapshot, ["tests/test_sample.py"])


def test_existing_destination_and_nested_paths_are_rejected_before_parent_creation(project, tmp_path):
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="new"):
        runner.snapshot_project(project, existing)
    nested = project / "absent" / "copy"
    with pytest.raises(ValueError, match="outside"):
        runner.snapshot_project(project, nested)
    assert not nested.parent.exists() and (existing / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_missing_parent_failure_cleans_only_empty_created_components(project, tmp_path):
    target = tmp_path / "created" / "nested" / "copy"
    with pytest.raises(ValueError, match="file or byte"):
        runner.snapshot_project(project, target, limits=runner.SnapshotLimits(max_files=1))
    assert not (tmp_path / "created").exists() and project.exists()


def test_parent_creation_race_refuses_existing_component_without_deleting_it(project, tmp_path, monkeypatch):
    raced = tmp_path / "parent" / "raced"
    original_mkdir = Path.mkdir
    def mkdir(path, *args, **kwargs):
        if path == raced and not path.exists():
            original_mkdir(path)
            (path / "someone_else.txt").write_text("preserve", encoding="utf-8")
        return original_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    with pytest.raises(FileExistsError):
        runner.snapshot_project(project, raced / "copy")
    assert (raced / "someone_else.txt").read_text(encoding="utf-8") == "preserve"
    assert not (raced / "copy").exists()


def test_symlink_parent_is_rejected_without_creating_target(project, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("This account cannot create symbolic links")
    with pytest.raises(ValueError, match="links|reparse"):
        runner.snapshot_project(project, link / "missing" / "copy")
    assert not (elsewhere / "missing").exists()


def test_parent_file_and_excessive_missing_depth_are_rejected(project, tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("keep", encoding="utf-8")
    with pytest.raises((ValueError, OSError)):
        runner.snapshot_project(project, blocked / "nested" / "copy")
    deep = tmp_path.joinpath(*["d" + str(index) for index in range(34)], "copy")
    with pytest.raises(ValueError, match="32"):
        runner.snapshot_project(project, deep)
    assert not (tmp_path / "d0").exists()


def test_test_and_configuration_patch_guards_remain_inherited(project, tmp_path):
    snapshot = runner.snapshot_project(project, tmp_path / "copy")
    for path in ("tests/test_sample.py", "conftest.py", "config.py"):
        with pytest.raises(ValueError, match="production"):
            runner.apply_replacements(snapshot, [{"path": path, "old": "return 3", "new": "return 4"}], allowed_production_roots=["."])
    assert runner.check_snapshot_integrity(snapshot)["snapshot_unchanged"]
