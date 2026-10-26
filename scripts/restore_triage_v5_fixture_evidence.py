"""Restore the exact historical V5 fixture evidence required by frozen tests.

Fresh checkout, after installing the normal development dependencies::

    python scripts/restore_triage_v5_fixture_evidence.py
    python -m pytest -q

The default destination matches benchmarks.triage_v5_lab, including its optional
DECILIST_TRIAGE_V5_WORK_ROOT environment override. This standard-library script
does not import a model, execute archived Python, regenerate proof, or run tests.
It verifies every archived byte against the unchanged corpus freeze. Existing
artifacts are accepted only when identical and are never overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ASSET_DIRECTORY = "reports/fixture-evidence/triage-v5-attempt-02"
FILENAMES = frozenset({"proof.json", "development-proof.json", "catalog_snapshot.json",
                       "lab_snapshot.py", "freeze_snapshot.json"})
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024
MAX_MEMBER_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024


class EvidenceError(ValueError):
    """The artifact identity or destination could not be verified."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("duplicate JSON field: " + key)
        result[key] = value
    return result


def _read_json(path: Path):
    def reject(value):
        raise EvidenceError("non-finite JSON value: " + value)
    return json.loads(_checked_path(path).read_bytes(), object_pairs_hook=_unique_object, parse_constant=reject)


def _checked_path(value) -> Path:
    """Check before resolving so links and Windows reparse points stay visible."""
    path = Path(os.path.abspath(value))
    for item in (path, *path.parents):
        try:
            metadata = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise EvidenceError("linked or reparse-point evidence paths are not supported")
    return path


def _freeze_inputs(repo_root: Path) -> tuple[dict, dict[str, str]]:
    freeze = _read_json(repo_root / "benchmarks/triage_v5_freeze.json")
    if freeze.get("suite") != "testtriage-repair-v5" or freeze.get("schema_version") != 5:
        raise EvidenceError("unsupported V5 corpus freeze")
    for relative, expected in freeze["files_sha256"].items():
        path = repo_root / relative
        if not path.resolve().is_relative_to(repo_root.resolve()) or _digest(path.read_bytes()) != expected:
            raise EvidenceError("frozen corpus source changed: " + relative)
    record = freeze["fixture_proof"]
    if record.get("relative_directory") != "attempt-02-all":
        raise EvidenceError("the retained evidence belongs to attempt-02-all only")
    expected = {
        "proof.json": record["proof_sha256"],
        "development-proof.json": record["development_proof_sha256"],
        "freeze_snapshot.json": record["freeze_snapshot_sha256"],
        "catalog_snapshot.json": freeze["files_sha256"]["benchmarks/triage_v5_cases.json"],
        "lab_snapshot.py": freeze["files_sha256"]["benchmarks/triage_v5_lab.py"],
    }
    if any(not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
           for value in expected.values()):
        raise EvidenceError("the frozen evidence SHA-256 values are invalid")
    return record, expected


def _bundle(asset_dir: Path, expected: dict[str, str]) -> dict[str, bytes]:
    manifest = _read_json(asset_dir / "manifest.json")
    if (manifest.get("schema_version") != 1 or manifest.get("suite") != "testtriage-repair-v5"
            or manifest.get("relative_directory") != "attempt-02-all"
            or manifest.get("archive") != "original-proof.zip" or set(manifest.get("files", {})) != FILENAMES):
        raise EvidenceError("unsupported or incomplete retained evidence manifest")
    archive = asset_dir / "original-proof.zip"
    _checked_path(archive)
    if archive.stat().st_size > MAX_ARCHIVE_BYTES or _digest(archive.read_bytes()) != manifest.get("archive_sha256"):
        raise EvidenceError("retained archive SHA-256 mismatch or size limit exceeded")
    contents = {}
    with zipfile.ZipFile(archive) as zipped:
        members = zipped.infolist()
        if len(members) != len(FILENAMES) or {member.filename for member in members} != FILENAMES:
            raise EvidenceError("archive must contain exactly the five declared evidence files")
        if sum(member.file_size for member in members) > MAX_TOTAL_BYTES:
            raise EvidenceError("retained evidence exceeds the total size limit")
        for member in members:
            declared = manifest["files"][member.filename]
            if (member.is_dir() or PurePosixPath(member.filename).name != member.filename or member.flag_bits & 1
                    or stat.S_ISLNK(member.external_attr >> 16)
                    or member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    or member.file_size > MAX_MEMBER_BYTES
                    or type(declared.get("bytes")) is not int or declared["bytes"] != member.file_size
                    or declared.get("sha256") != expected[member.filename]):
                raise EvidenceError("unsafe, changed or oversized archive member: " + member.filename)
            data = zipped.read(member)
            if len(data) != declared["bytes"] or _digest(data) != expected[member.filename]:
                raise EvidenceError("original evidence SHA-256 mismatch: " + member.filename)
            contents[member.filename] = data
    return contents


def restore_evidence(*, repo_root: Path = ROOT, lab_root: Path | None = None,
                     asset_dir: Path | None = None) -> dict:
    """Validate all inputs/conflicts first, then exclusively create missing files."""
    repo_root = _checked_path(repo_root)
    record, expected = _freeze_inputs(repo_root)
    contents = _bundle(_checked_path(asset_dir or repo_root / ASSET_DIRECTORY), expected)
    configured = lab_root if lab_root is not None else os.environ.get(
        "DECILIST_TRIAGE_V5_WORK_ROOT", repo_root.parents[1] / "work" / "triage-v5-lab")
    root = _checked_path(configured)
    if root == repo_root or root.is_relative_to(repo_root):
        raise EvidenceError("the V5 lab root must remain outside the checkout")
    destination = _checked_path(root / record["relative_directory"])
    unchanged, missing = [], []
    for filename, data in contents.items():
        target = _checked_path(destination / filename)
        if target.exists():
            if not target.is_file() or target.read_bytes() != data:
                raise EvidenceError("existing artifact differs; no files were overwritten: " + filename)
            unchanged.append(filename)
        else:
            missing.append(filename)
    destination.mkdir(parents=True, exist_ok=True)
    _checked_path(destination)
    restored = []
    for filename in missing:
        target = _checked_path(destination / filename)
        try:
            with target.open("xb") as handle:
                handle.write(contents[filename])
                handle.flush()
                os.fsync(handle.fileno())
            restored.append(filename)
        except FileExistsError:
            if not target.is_file() or target.read_bytes() != contents[filename]:
                raise EvidenceError("concurrent artifact differs; no files were overwritten: " + filename)
            unchanged.append(filename)
        if _digest(target.read_bytes()) != expected[filename]:
            raise EvidenceError("restored evidence did not retain its frozen bytes: " + filename)
    return {"suite": "testtriage-repair-v5", "relative_directory": record["relative_directory"],
            "restored": sorted(restored), "already_identical": sorted(unchanged),
            "files_sha256": expected, "historical_evidence_only": True,
            "fixture_tests_executed": False, "model_calls": 0}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab-root", type=Path,
        help="Optional destination override; set DECILIST_TRIAGE_V5_WORK_ROOT identically for subsequent pytest")
    args = parser.parse_args(argv)
    try:
        result = restore_evidence(lab_root=args.lab_root)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        print("V5 evidence restore failed: " + str(exc))
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
