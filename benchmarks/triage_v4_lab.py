"""Private V4 repair-fixture factory and protected, isolated test runner.

The evaluator catalog and reference restoration never enter a participant tree.
All allowed production roots are intentionally broad. Fixture proof establishes
executable regressions, not human difficulty, model usefulness, or a speedup.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from time import perf_counter
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
LAB_ROOT = Path(os.environ.get("DECILIST_TRIAGE_V4_WORK_ROOT", REPO_ROOT.parents[1] / "work" / "triage-v4-lab"))
BASE_REVISION = "1c2e0ff46cca171c717b098b97c0cf768088beef"
CATALOG = Path(__file__).with_name("triage_v4_cases.json")
FREEZE = Path(__file__).with_name("triage_v4_freeze.json")
SOURCE_PREFIX = "packages/python/src/decilist/"
PRODUCTION_ROOTS = (SOURCE_PREFIX, "vendor/httpx/")
TEST_PATH = ".triage/test_case.py"
REGRESSION_TEST_PATH = ".triage/test_regression.py"
CONFIG_PATH = ".triage/pytest.ini"
MANIFEST_PATH = ".triage/manifest.json"
TEST_ARGS = ["-m", "pytest", "-q", "--tb=short", "-c", CONFIG_PATH,
             "--confcutdir=.triage", "-o", "asyncio_mode=auto", "-p", "no:cacheprovider",
             "-p", "pytest_asyncio.plugin", TEST_PATH, REGRESSION_TEST_PATH]

TEST_PREFIX = '''from pathlib import Path
import asyncio
import json
import pytest
import decilist
import httpx
CASE_ROOT = Path(__file__).resolve().parents[1]
assert Path(decilist.__file__).resolve().is_relative_to(CASE_ROOT / "packages/python/src"), "must import isolated project source"
assert Path(httpx.__file__).resolve().is_relative_to(CASE_ROOT / "vendor/httpx"), "must import isolated external source"
'''


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_catalog() -> dict:
    """Evaluator-only data. Do not pass this result to a model/participant."""
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    if data["base_revision"] != BASE_REVISION or len(data["cases"]) != 14:
        raise ValueError("unexpected frozen base or corpus size")
    ids = [case["id"] for case in data["cases"]]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate case IDs")
    expected = {"development": 2, "evaluation": 6, "reserved": 4, "controls": 2}
    if {role: sum(case["split"] == role for case in data["cases"]) for role in expected} != expected:
        raise ValueError("invalid V4 allocation")
    for case in data["cases"]:
        if not case["case_tests"] or not case["regression_tests"]:
            raise ValueError("both protected test groups are required")
        if not case["mutations"] or not case["investigation_surface"] or not case["repair_invariants"]:
            raise ValueError("incomplete private fixture specification")
        for mutation in case["mutations"]:
            if not _production_path(mutation["path"]) or mutation["kind"] != "replace":
                raise ValueError("mutations must be scoped production replacements")
    return data


def _case(case_id: str) -> dict:
    for case in load_catalog()["cases"]:
        if case["id"] == case_id:
            return case
    raise ValueError("unknown case ID")


def case_ids(split: str | None = None) -> tuple[str, ...]:
    """Neutral membership only; excludes all mechanism/repair metadata."""
    if split is not None and split not in {"development", "evaluation", "reserved", "controls"}:
        raise ValueError("unknown split")
    return tuple(row["id"] for row in load_catalog()["cases"] if split is None or row["split"] == split)


def development_case_ids() -> tuple[str, ...]:
    return case_ids("development")


def trial_case_ids() -> tuple[str, ...]:
    return case_ids("reserved")


def get_case_role(case_id: str) -> str:
    case = _case(case_id)
    return "trial_regression" if case["split"] == "reserved" else "control" if case["split"] == "controls" else "regression"


def _production_path(relative: str) -> bool:
    parts = Path(relative).parts
    return relative.startswith(PRODUCTION_ROOTS) and not Path(relative).is_absolute() and ".." not in parts


def _git_blob(relative: str) -> bytes:
    return subprocess.run(["git", "show", f"{BASE_REVISION}:{relative}"], cwd=REPO_ROOT,
                          capture_output=True, check=True).stdout


@lru_cache(maxsize=1)
def _base_files() -> dict[str, bytes]:
    archive = subprocess.run(["git", "archive", "--format=tar", BASE_REVISION, "--", SOURCE_PREFIX.rstrip("/")],
                             cwd=REPO_ROOT, capture_output=True, check=True).stdout
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as handle:
        for member in handle:
            if member.isdir():
                continue
            if not member.isfile() or not member.name.startswith(SOURCE_PREFIX) or ".." in Path(member.name).parts:
                raise ValueError("unsafe archived source path")
            files[member.name] = handle.extractfile(member).read()
    import httpx
    if httpx.__version__ != "0.28.1":
        raise ValueError("V4 requires already installed httpx==0.28.1; nothing is installed automatically")
    project = load_catalog()["projects"]["httpx"]
    root = Path(httpx.__file__).resolve().parent
    for relative, wanted in project["installed_source_sha256"].items():
        data = (root / relative.removeprefix("vendor/httpx/")).read_bytes()
        if digest(data) != wanted:
            raise ValueError("HTTPX source differs from the pinned snapshot: " + relative)
        files[relative] = data
    license_data = project["license_text"].encode("utf-8")
    if digest(license_data) != project["license_sha256"]:
        raise ValueError("HTTPX license hash differs")
    files["vendor/LICENSE.httpx.md"] = license_data
    files["LICENSE.decilist"] = _git_blob("LICENSE")
    return files


def _python() -> Path:
    path = Path(os.environ.get("DECILIST_TRIAGE_PYTHON", sys.executable)).resolve()
    if not path.is_file():
        raise ValueError("selected Python executable is unavailable")
    return path


def _work_root(work_root: str | Path | None) -> Path:
    root = Path(work_root if work_root is not None else LAB_ROOT).resolve()
    if root == REPO_ROOT.resolve() or root.is_relative_to(REPO_ROOT.resolve()):
        raise ValueError("work root must be outside the checkout")
    return root


def _checked_path(path: str | Path, *, work_root: str | Path | None = None, new: bool = False) -> Path:
    resolved, root = Path(path).resolve(), _work_root(work_root)
    if resolved == root or not resolved.is_relative_to(root) or resolved.is_relative_to(REPO_ROOT.resolve()):
        raise ValueError("case directory must be strictly inside the configured work root, outside the checkout")
    if new and resolved.exists():
        raise ValueError("destination already exists; use a fresh isolated directory")
    return resolved


def _registry_path(directory: Path, work_root: str | Path | None) -> Path:
    return _work_root(work_root) / ".registry" / (digest(str(directory).encode()) + ".json")


def _write_json(path: Path, value: dict, *, exclusive: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x" if exclusive else "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")


def _test_files(case: dict) -> dict[str, bytes]:
    return {TEST_PATH: (TEST_PREFIX + case["case_tests"]).encode(),
            REGRESSION_TEST_PATH: (TEST_PREFIX + case["regression_tests"]).encode(), CONFIG_PATH: b"[pytest]\n"}


def _apply_mutation(directory: Path, case: dict) -> None:
    for mutation in case["mutations"]:
        path = directory / mutation["path"]
        text = path.read_text(encoding="utf-8")
        if text.count(mutation["old"]) != 1:
            raise ValueError("candidate replacement must match once: " + case["id"])
        path.write_bytes(text.replace(mutation["old"], mutation["new"]).encode())


def prepare_case(case_id: str, dest: str | Path, *, work_root: str | Path | None = None) -> dict:
    """Create a participant tree with neutral test selectors and broad roots."""
    directory = _checked_path(dest, work_root=work_root, new=True)
    case, files = _case(case_id), _base_files()
    directory.mkdir(parents=True)
    for relative, data in {**files, **_test_files(case)}.items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    _apply_mutation(directory, case)
    manifest = {"schema_version": 1, "suite": "testtriage-repair-v4", "case_id": case_id,
                "allowed_production_roots": list(PRODUCTION_ROOTS), "failing_tests": [TEST_PATH],
                "regression_tests": [REGRESSION_TEST_PATH], "test_command": [Path(_python()).name, *TEST_ARGS]}
    _write_json(directory / MANIFEST_PATH, manifest)
    protected = {relative: digest(data) for relative, data in {**files, **_test_files(case)}.items()
                 if not _production_path(relative)}
    _write_json(_registry_path(directory, work_root), {"case_id": case_id,
                "manifest_sha256": digest((directory / MANIFEST_PATH).read_bytes()),
                "catalog_sha256": digest(CATALOG.read_bytes()), "protected_sha256": protected,
                "original_production_paths": sorted(relative for relative in files if _production_path(relative))})
    return {**manifest, "case_dir": str(directory)}


def _integrity(directory: Path, work_root: str | Path | None) -> tuple[bool, list[str], dict]:
    registry_path = _registry_path(directory, work_root)
    if not registry_path.is_file():
        return False, ["unregistered case directory"], {}
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    case, issues = _case(registry["case_id"]), []
    if digest(CATALOG.read_bytes()) != registry["catalog_sha256"]:
        issues.append("evaluator catalog changed after preparation")
    manifest = directory / MANIFEST_PATH
    if not manifest.is_file() or manifest.is_symlink() or digest(manifest.read_bytes()) != registry["manifest_sha256"]:
        issues.append("participant manifest changed")
    expected = {path: digest(data) for path, data in {**_base_files(), **_test_files(case)}.items() if not _production_path(path)}
    if expected != registry["protected_sha256"]:
        issues.append("trusted protected files differ from frozen source")
    for relative, wanted in expected.items():
        path = directory / relative
        if not path.is_file() or path.is_symlink() or digest(path.read_bytes()) != wanted:
            issues.append("protected file changed: " + relative)
    for relative in registry["original_production_paths"]:
        path = directory / relative
        if not path.is_file() or path.is_symlink():
            issues.append("production source missing or linked: " + relative)
    known = set(expected) | {MANIFEST_PATH}
    for path in directory.rglob("*"):
        relative = path.relative_to(directory).as_posix()
        if path.is_symlink():
            issues.append("symlinks are not permitted")
        if not path.is_file() or "/__pycache__/" in relative or relative.startswith(".triage/runs/"):
            continue
        if relative not in known and not _production_path(relative):
            issues.append("file outside allowed production roots: " + relative)
    return not issues, issues, case


def _source_hashes(directory: Path) -> dict:
    return {path.relative_to(directory).as_posix(): digest(path.read_bytes())
            for root in PRODUCTION_ROOTS for path in sorted((directory / root).rglob("*"))
            if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts}


def run_case(case_dir: str | Path, *, work_root: str | Path | None = None) -> dict:
    """Run both protected multi-input suites against the isolated source."""
    directory = _checked_path(case_dir, work_root=work_root)
    intact, issues, _ = _integrity(directory, work_root)
    empty = {"exit_code": None, "elapsed_ms": 0, "stdout": "", "log_path": None,
             "command": [Path(_python()).name, *TEST_ARGS], "protected_files_ok": intact,
             "integrity_issues": issues, "run_sha256": None, "timed_out": False,
             "source_stable": False, "editable_sha256_before": {}, "editable_sha256_after": {}}
    if not intact:
        return empty
    before = _source_hashes(directory)
    environment = dict(os.environ)
    environment.update(PYTHONPATH=os.pathsep.join([str(directory / "vendor"), str(directory / "packages/python/src")]),
                       PYTHONDONTWRITEBYTECODE="1", PYTHONOPTIMIZE="", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTEST_ADDOPTS="")
    tick = perf_counter()
    try:
        completed = subprocess.run([str(_python()), *TEST_ARGS], cwd=directory, env=environment,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
        code, output, timed_out = completed.returncode, completed.stdout + completed.stderr, False
    except subprocess.TimeoutExpired as error:
        pieces = [value.decode("utf-8", "replace") if isinstance(value, bytes) else value or "" for value in (error.stdout, error.stderr)]
        code, output, timed_out = None, "".join(pieces) + "\n45-second test observation bound exceeded.\n", True
    elapsed = (perf_counter() - tick) * 1000
    after = _source_hashes(directory)
    intact, issues, _ = _integrity(directory, work_root)
    from decilist.triage_evidence import prepare_evidence
    prepared = prepare_evidence(output, project=directory)
    cleaned = prepared["log"]
    relative = f".triage/runs/{uuid4().hex}.log"
    path = directory / relative
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(cleaned.encode())
    path.with_suffix(".raw.log").write_bytes(output.encode())
    return {**empty, "exit_code": code, "elapsed_ms": elapsed, "stdout": cleaned,
            "log_path": relative, "protected_files_ok": intact, "integrity_issues": issues,
            "run_sha256": digest(cleaned.encode()), "timed_out": timed_out, "source_stable": before == after,
            "editable_sha256_before": before, "editable_sha256_after": after,
            "raw_log_sha256": digest(output.encode()), "log_truncated": prepared["truncated"],
            "log_redaction_count": prepared["redaction_count"]}


def validate_repair(case_dir: str | Path, *, work_root: str | Path | None = None) -> dict:
    run = run_case(case_dir, work_root=work_root)
    return {"passed": bool(run["protected_files_ok"] and run["source_stable"] and run["exit_code"] == 0 and not run["timed_out"]),
            "integrity_ok": run["protected_files_ok"], "run": run}


def prepare_case_evidence(case_dir: str | Path, run: dict | None = None, *, work_root: str | Path | None = None) -> dict:
    """Observed logs/source only; no catalog labels, seeds, repairs, or ranking."""
    directory = _checked_path(case_dir, work_root=work_root)
    observed = run if run is not None else run_case(directory, work_root=work_root)
    intact, issues, _ = _integrity(directory, work_root)
    if not intact or not observed["protected_files_ok"] or observed["run_sha256"] != digest(observed["stdout"].encode()):
        raise ValueError("evidence requires an intact saved run: " + "; ".join(issues))
    stable = _source_hashes(directory) == observed["editable_sha256_after"]
    from decilist.triage_evidence import prepare_evidence
    evidence = prepare_evidence(observed["stdout"], project=directory if stable else None, command=observed["command"])
    evidence["exit_code"] = observed["exit_code"]
    if not stable:
        evidence["source_context_omitted"] = "Production source changed after the saved observation."
    return evidence


def prepare_machine_evidence(case_dir: str | Path, run: dict | None = None, *, work_root: str | Path | None = None) -> dict:
    directory = _checked_path(case_dir, work_root=work_root)
    intact, _, case = _integrity(directory, work_root)
    if not intact:
        raise ValueError("case integrity failed")
    if case["split"] == "reserved":
        raise ValueError("reserved blind repair cases cannot enter machine evaluation")
    observed = run if run is not None else run_case(directory, work_root=work_root)
    if case["evidence_mode"] == "full":
        return prepare_case_evidence(directory, observed, work_root=work_root)
    from decilist.triage_evidence import prepare_evidence
    evidence = prepare_evidence("Execution log was truncated before diagnostic details were retained.", command=observed["command"])
    evidence.update(exit_code=observed["exit_code"], truncated=True)
    return evidence


def _reference_repair(directory: Path, *, work_root: str | Path | None = None) -> None:
    """Evaluator-only restoration of pinned source. Never called by models."""
    intact, issues, _ = _integrity(directory, work_root)
    if not intact:
        raise ValueError("cannot restore a tampered fixture: " + "; ".join(issues))
    for relative, data in _base_files().items():
        if _production_path(relative):
            (directory / relative).write_bytes(data)


def verify_fixture(case_id: str, destination: str | Path, *, work_root: str | Path | None = None) -> dict:
    """Retain every candidate's actual baseline/seed/repair outcome, including failures."""
    manifest = prepare_case(case_id, destination, work_root=work_root)
    directory, case = Path(manifest["case_dir"]), _case(case_id)
    _reference_repair(directory, work_root=work_root)
    baseline = run_case(directory, work_root=work_root)
    _apply_mutation(directory, case)
    seeded = run_case(directory, work_root=work_root)
    _reference_repair(directory, work_root=work_root)
    repaired = validate_repair(directory, work_root=work_root)
    _apply_mutation(directory, case)
    passed = (baseline["exit_code"] == 0 and baseline["protected_files_ok"] and baseline["source_stable"]
              and seeded["exit_code"] in (1, 2) and seeded["protected_files_ok"] and seeded["source_stable"]
              and not seeded["timed_out"] and repaired["passed"])
    return {"case_id": case_id, "verified": bool(passed), "baseline": baseline, "mutation": seeded,
            "reference_repair": repaired, "seeded_source_sha256": _source_hashes(directory),
            "candidate_status": "executable_regression" if passed else "retained_candidate_failure"}


def verify_all(destination: str | Path, *, ids: tuple[str, ...] | None = None, work_root: str | Path | None = None) -> dict:
    root = _checked_path(destination, work_root=work_root, new=True)
    root.mkdir(parents=True)
    # These private snapshots live beside, never inside, participant trees.
    (root / "catalog_snapshot.json").write_bytes(CATALOG.read_bytes())
    (root / "lab_snapshot.py").write_bytes(Path(__file__).read_bytes())
    results = []
    for case_id in ids if ids is not None else case_ids():
        try:
            result = verify_fixture(case_id, root / case_id, work_root=work_root)
        except Exception as error:
            result = {"case_id": case_id, "verified": False, "candidate_status": "retained_candidate_failure",
                      "error_type": type(error).__name__, "error": str(error)}
        results.append(result)
        _write_json(root / "proof.json", {"suite": "testtriage-repair-v4", "cases": results}, exclusive=False)
    proof = {"suite": "testtriage-repair-v4", "created_utc": datetime.now(timezone.utc).isoformat(),
             "cases": results, "verified_count": sum(row["verified"] for row in results),
             "candidate_count": len(results), "all_verified": all(row["verified"] for row in results)}
    _write_json(root / "proof.json", proof, exclusive=False)
    _write_json(root / "development-proof.json", {"suite": proof["suite"],
                "cases": [row for row in results if row["case_id"] in development_case_ids()]})
    return proof


def verify_freeze() -> dict:
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    if freeze["base_revision"] != BASE_REVISION:
        raise ValueError("frozen baseline mismatch")
    for relative, wanted in freeze["files_sha256"].items():
        if digest((REPO_ROOT / relative).read_bytes()) != wanted:
            raise ValueError("frozen V4 corpus changed: " + relative)
    if {path: digest(data) for path, data in _base_files().items()} != freeze["base_source_sha256"]:
        raise ValueError("frozen production source changed")
    return freeze


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "validate", "verify", "verify-all"))
    parser.add_argument("case_or_directory")
    parser.add_argument("--destination")
    parser.add_argument("--work-root")
    args = parser.parse_args()
    options = {"work_root": args.work_root}
    if args.action == "prepare":
        if args.destination is None:
            parser.error("prepare requires --destination")
        result = prepare_case(args.case_or_directory, args.destination, **options)
    elif args.action == "verify":
        if args.destination is None:
            parser.error("verify requires --destination")
        result = verify_fixture(args.case_or_directory, args.destination, **options)
    elif args.action == "verify-all":
        result = verify_all(args.case_or_directory, **options)
    elif args.action == "run":
        result = run_case(args.case_or_directory, **options)
    else:
        result = validate_repair(args.case_or_directory, **options)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
