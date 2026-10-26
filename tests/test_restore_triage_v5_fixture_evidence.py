"""Portable restoration never replaces the actual frozen fixture tests."""
import hashlib
from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from scripts import restore_triage_v5_fixture_evidence as restore


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_bundle(folder, contents, *, extra=None, linked=None):
    archive = folder / "original-proof.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for name, data in contents.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (0o120777 if name == linked else 0o100644) << 16
            handle.writestr(info, data)
        if extra is not None:
            handle.writestr(extra, b"must not escape")
    manifest = {"schema_version": 1, "suite": "testtriage-repair-v5", "relative_directory": "attempt-02-all",
        "archive": "original-proof.zip", "archive_sha256": digest(archive.read_bytes()),
        "files": {name: {"sha256": digest(data), "bytes": len(data)} for name, data in contents.items()}}
    (folder / "manifest.json").write_text(json.dumps(manifest))


@pytest.fixture
def fixture(tmp_path):
    repo, assets, lab = tmp_path / "checkout", tmp_path / "assets", tmp_path / "lab"
    (repo / "benchmarks").mkdir(parents=True)
    (repo / "tests").mkdir()
    assets.mkdir()
    lab_source = b"# frozen source, never executed by the restorer\n"
    catalog = b'{"fixture":"synthetic-public-data"}\n'
    test_source = b"# fixed assertions, never changed by the restorer\n"
    (repo / "benchmarks/triage_v5_lab.py").write_bytes(lab_source)
    (repo / "benchmarks/triage_v5_cases.json").write_bytes(catalog)
    (repo / "tests/test_triage_v5_lab.py").write_bytes(test_source)
    contents = {"proof.json": b'{"observed":"original byte content"}\r\n',
        "development-proof.json": b'{"observed":"original development content"}\n',
        "catalog_snapshot.json": catalog, "lab_snapshot.py": lab_source,
        "freeze_snapshot.json": b'{"historical":"freeze snapshot"}\n'}
    freeze = {"suite": "testtriage-repair-v5", "schema_version": 5,
        "files_sha256": {"benchmarks/triage_v5_lab.py": digest(lab_source),
            "benchmarks/triage_v5_cases.json": digest(catalog), "tests/test_triage_v5_lab.py": digest(test_source)},
        "fixture_proof": {"relative_directory": "attempt-02-all", "proof_sha256": digest(contents["proof.json"]),
            "development_proof_sha256": digest(contents["development-proof.json"]),
            "freeze_snapshot_sha256": digest(contents["freeze_snapshot.json"])}}
    (repo / "benchmarks/triage_v5_freeze.json").write_text(json.dumps(freeze))
    write_bundle(assets, contents)
    return repo, assets, lab, contents


def run(fixture, **kwargs):
    repo, assets, lab, _ = fixture
    return restore.restore_evidence(repo_root=repo, asset_dir=assets, lab_root=lab, **kwargs)


def test_fresh_restore_preserves_exact_bytes_and_repeat_is_read_only(fixture):
    _, _, lab, contents = fixture
    first = run(fixture)
    destination = lab / "attempt-02-all"
    assert first["restored"] == sorted(contents) and first["already_identical"] == []
    assert first["model_calls"] == 0 and not first["fixture_tests_executed"]
    before = {name: ((destination / name).read_bytes(), (destination / name).stat().st_mtime_ns) for name in contents}
    second = run(fixture)
    assert second["restored"] == [] and second["already_identical"] == sorted(contents)
    assert before == {name: ((destination / name).read_bytes(), (destination / name).stat().st_mtime_ns) for name in contents}
    assert (destination / "proof.json").read_bytes().endswith(b"\r\n")


def test_one_conflicting_existing_artifact_blocks_every_write(fixture):
    _, _, lab, _ = fixture
    destination = lab / "attempt-02-all"
    destination.mkdir(parents=True)
    target = destination / "proof.json"
    target.write_bytes(b"pre-existing user evidence\n")
    with pytest.raises(restore.EvidenceError, match="no files were overwritten"):
        run(fixture)
    assert target.read_bytes() == b"pre-existing user evidence\n"
    assert list(destination.iterdir()) == [target]


def test_identical_partial_restore_can_finish_without_overwriting(fixture):
    _, _, lab, contents = fixture
    destination = lab / "attempt-02-all"
    destination.mkdir(parents=True)
    (destination / "proof.json").write_bytes(contents["proof.json"])
    result = run(fixture)
    assert result["already_identical"] == ["proof.json"] and len(result["restored"]) == 4


@pytest.mark.parametrize("change", ["archive", "manifest_member", "frozen_source", "relative_directory"])
def test_drift_is_rejected_before_destination_creation(fixture, change):
    repo, assets, lab, _ = fixture
    if change == "archive":
        with (assets / "original-proof.zip").open("ab") as handle:
            handle.write(b"changed archive")
    elif change == "manifest_member":
        path = assets / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["files"]["proof.json"]["sha256"] = "f" * 64
        path.write_text(json.dumps(manifest))
    elif change == "frozen_source":
        (repo / "tests/test_triage_v5_lab.py").write_text("# changed assertions\n")
    else:
        path = repo / "benchmarks/triage_v5_freeze.json"
        freeze = json.loads(path.read_text())
        freeze["fixture_proof"]["relative_directory"] = "../escape"
        path.write_text(json.dumps(freeze))
    with pytest.raises(restore.EvidenceError):
        run(fixture)
    assert not lab.exists()


@pytest.mark.parametrize("extra", ["../escape", "/absolute", "proof.json", "nested/proof.json"])
def test_archive_paths_duplicates_and_unexpected_members_never_extract(fixture, extra):
    _, assets, lab, contents = fixture
    with pytest.warns(UserWarning) if extra == "proof.json" else nullcontext():
        write_bundle(assets, contents, extra=extra)
    with pytest.raises(restore.EvidenceError, match="exactly the five"):
        run(fixture)
    assert not lab.exists() and not (lab.parent / "escape").exists()


def test_zip_symlink_member_rejected_without_platform_symlink_permissions(fixture):
    _, assets, lab, contents = fixture
    write_bundle(assets, contents, linked="proof.json")
    with pytest.raises(restore.EvidenceError, match="unsafe"):
        run(fixture)
    assert not lab.exists()


def test_refuses_lab_destination_inside_checkout(fixture):
    repo, assets, _, _ = fixture
    with pytest.raises(restore.EvidenceError, match="outside the checkout"):
        restore.restore_evidence(repo_root=repo, asset_dir=assets, lab_root=repo / "work")
    assert not (repo / "work").exists()


def test_environment_default_matches_frozen_lab_contract(fixture, monkeypatch):
    repo, assets, lab, _ = fixture
    monkeypatch.setenv("DECILIST_TRIAGE_V5_WORK_ROOT", str(lab))
    result = restore.restore_evidence(repo_root=repo, asset_dir=assets)
    assert len(result["restored"]) == 5 and (lab / "attempt-02-all/proof.json").is_file()


def test_packaged_originals_satisfy_the_real_unchanged_freeze_in_a_fresh_lab(tmp_path, monkeypatch):
    from benchmarks import triage_v5_lab as lab
    fresh = tmp_path / "real-fresh-lab"
    monkeypatch.setattr(lab, "LAB_ROOT", fresh)
    with pytest.raises(FileNotFoundError):
        lab.verify_freeze()
    result = restore.restore_evidence(lab_root=fresh)
    frozen = lab.verify_freeze()
    assert result["files_sha256"]["proof.json"] == frozen["fixture_proof"]["proof_sha256"]
    assert frozen["fixture_proof"]["verified_count"] == 14
    assert (fresh / "attempt-02-all/catalog_snapshot.json").read_bytes() == lab.CATALOG.read_bytes()
    assert (fresh / "attempt-02-all/lab_snapshot.py").read_bytes() == Path(lab.__file__).read_bytes()


def test_documented_cli_uses_only_stdlib_and_the_environment_destination(tmp_path):
    destination = tmp_path / "cli-fresh-lab"
    environment = dict(os.environ, DECILIST_TRIAGE_V5_WORK_ROOT=str(destination))
    completed = subprocess.run([sys.executable, "-I", str(restore.ROOT / "scripts/restore_triage_v5_fixture_evidence.py")],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    result = json.loads(completed.stdout)
    assert len(result["restored"]) == 5 and result["historical_evidence_only"]


def test_ci_restores_before_running_the_unmodified_full_pytest_command():
    workflow = (restore.ROOT / ".github/workflows/ci.yml").read_text()
    assert workflow.index("python scripts/restore_triage_v5_fixture_evidence.py") < workflow.index("python -m pytest -q")
    assert "DECILIST_TRIAGE_V5_WORK_ROOT: ${{ runner.temp }}" in workflow
