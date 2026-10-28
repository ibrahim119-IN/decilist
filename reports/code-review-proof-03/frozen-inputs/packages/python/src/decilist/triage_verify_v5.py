"""V5 trusted-test execution with the unchanged V4 snapshot and patch guards.

This module doubles as a standalone isolated worker when invoked by file path
with --worker. It loads only the V5 trace plugin; shared V4 state is not changed.
Tests run with the caller's OS permissions, not inside a security sandbox.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence
import uuid

class _Audit:
    def __init__(self, output: Path):
        self.output = output
        self.data = {"collected": [], "reports": [], "collection_errors": []}

    def pytest_collection_finish(self, session):
        self.data["collected"] = [item.nodeid for item in session.items]

    def pytest_collectreport(self, report):
        if report.failed:
            self.data["collection_errors"].append(report.nodeid)

    def pytest_runtest_logreport(self, report):
        self.data["reports"].append({"nodeid": report.nodeid, "when": report.when, "outcome": report.outcome})

    def pytest_sessionfinish(self, session, exitstatus):
        self.data["exitstatus"] = int(exitstatus)
        self.output.write_text(json.dumps(self.data, sort_keys=True), encoding="utf-8")

def _worker_main(request_path: str) -> int:
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    root = Path(request["root"]).resolve()
    # -I excludes ambient PYTHONPATH and the worker's containing directory.
    # Put copied implementation roots first, including the pinned vendor tree.
    copied_paths = [root / "vendor", root / "packages" / "python" / "src", root / "src", root]
    sys.path[:0] = [str(path) for path in copied_paths if path.is_dir()]
    os.environ["DECILIST_TRACE_OUTPUT"] = request["trace_output"]
    os.environ["DECILIST_TRACE_ROOT"] = str(root)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    if request.get("capture_failure_text") is True:
        os.environ["DECILIST_TRACE_FAILURE_TEXT"] = "1"
    else:
        os.environ.pop("DECILIST_TRACE_FAILURE_TEXT", None)
    sys.dont_write_bytecode = True
    plugin_spec = importlib.util.spec_from_file_location("_decilist_v5_owned_trace", request["trace_plugin"])
    if plugin_spec is None or plugin_spec.loader is None:
        raise RuntimeError("V5 trace plugin is unavailable")
    trace_plugin = importlib.util.module_from_spec(plugin_spec)
    sys.modules[plugin_spec.name] = trace_plugin
    plugin_spec.loader.exec_module(trace_plugin)
    import pytest
    import pytest_asyncio.plugin
    audit = _Audit(Path(request["audit_output"]))
    controls = ["--rootdir=" + str(root), "--confcutdir=" + str(root), "--basetemp=" + request["basetemp"], "-p", "no:cacheprovider", "-o", "addopts="]
    return int(pytest.main([*request["pytest_args"], *controls], plugins=[trace_plugin, audit, pytest_asyncio.plugin]))

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        raise SystemExit(_worker_main(sys.argv[2]))
    raise SystemExit("Use python -m decilist.triage_v5 for the public workflow")


from . import triage_verify_v4 as _guards

EXPLANATION = _guards.EXPLANATION
Snapshot = _guards.Snapshot
SnapshotLimits = _guards.SnapshotLimits
validate_pytest_args = _guards.validate_pytest_args
apply_replacements = _guards.apply_replacements
cleanup_snapshot = _guards.cleanup_snapshot
check_snapshot_integrity = _guards.check_snapshot_integrity
sanitized_environment = _guards.sanitized_environment
_owned = _guards._owned
_manifest = _guards._manifest
_source_unchanged = _guards._source_unchanged
_checked_directory = _guards._checked_directory
_is_link = _guards._is_link
_relative = _guards._relative
_PATCH_BLOCKED_NAMES = _guards._PATCH_BLOCKED_NAMES
_stop_owned_process = _guards._stop_owned_process
_read_output = _guards._read_output


def _check_path(path: Path) -> Path:
    """Check all existing components, including dangling links and junctions."""
    absolute = Path(os.path.abspath(path))
    for item in (absolute, *absolute.parents):
        try:
            metadata = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_file_attributes", 0) & 0x400):
            raise ValueError("Symbolic links and reparse-point directory paths are not supported")
    return absolute


def snapshot_project(source_root: Path | str, destination: Path | str, *,
                     limits: SnapshotLimits | None = None) -> Snapshot:
    """Create missing caller-selected parents, then use the stable copy guards.

    Parent components are created exclusively and checked again; a collision is
    rejected rather than adopted. This does not attest directory ACL ownership.
    Failure cleanup removes only empty components created by this invocation.
    """
    source = _check_path(Path(source_root))
    destination = _check_path(Path(destination))
    if not source.is_dir():
        raise ValueError("The source project must be an existing directory")
    if destination == source or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("Snapshot destination must be outside and separate from the source project")
    if os.path.lexists(destination):
        raise ValueError("Snapshot destination must be new")
    missing, cursor = [], destination.parent
    while not os.path.lexists(cursor):
        missing.append(cursor)
        if len(missing) > 32:
            raise ValueError("Snapshot parent path exceeds 32 missing components")
        cursor = cursor.parent
    _check_path(cursor)
    if not cursor.is_dir():
        raise ValueError("An existing snapshot ancestor is not a directory")
    created: list[tuple[Path, int, int]] = []
    try:
        for parent in reversed(missing):
            _check_path(parent)
            parent.mkdir()  # exclusive: never accept a raced existing component
            metadata = parent.lstat()
            created.append((parent, metadata.st_dev, metadata.st_ino))
            _check_path(parent)
        _check_path(destination)
        if os.path.lexists(destination):
            raise ValueError("Snapshot destination appeared during parent creation")
        return _guards.snapshot_project(source, destination, limits=limits)
    except BaseException:
        for parent, device, inode in reversed(created):
            try:
                _check_path(parent)
                metadata = parent.lstat()
                if (metadata.st_dev, metadata.st_ino) != (device, inode):
                    continue
                parent.rmdir()  # no recursion and no removal of nonempty paths
            except (OSError, ValueError):
                pass
        raise


def run_pytest(snapshot: Snapshot, pytest_args: Sequence[str], *, timeout_seconds: float = 30, label: str = "run", capture_failure_text: bool = False, expected_hashes: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Run explicit pytest arguments in the copy using the standalone V5 worker."""
    _owned(snapshot)
    args = validate_pytest_args(snapshot, pytest_args)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 3600:
        raise ValueError("Test timeout must be between zero and 3600 seconds")
    if not isinstance(label, str) or not label.isidentifier() or len(label) > 80:
        raise ValueError("Run label must be a short identifier")
    if type(capture_failure_text) is not bool:
        raise ValueError("capture_failure_text must be an explicit boolean")
    before = _manifest(snapshot.root, snapshot.limits)
    if before != (dict(expected_hashes) if expected_hashes is not None else snapshot.source_hashes):
        raise ValueError("Snapshot differs from the approved source or candidate hashes")
    run_directory = snapshot.evidence / (label + "_" + uuid.uuid4().hex[:8])
    run_directory.mkdir()
    temporary = snapshot.evidence / "tmp" / run_directory.name
    temporary.mkdir(parents=True)
    original_before = _source_unchanged(snapshot)
    if not original_before:
        raise ValueError("Original project changed since the snapshot was created")
    trace, audit = run_directory / "trace.json", run_directory / "audit.json"
    request = {"root": str(snapshot.root), "pytest_args": args, "trace_output": str(trace), "trace_plugin": str(Path(__file__).with_name("triage_trace_v5.py")), "audit_output": str(audit), "basetemp": str(temporary / "pytest"), "capture_failure_text": capture_failure_text}
    request_path = run_directory / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    command = [sys.executable, "-I", "-B", str(Path(__file__)), "--worker", str(request_path)]
    env = sanitized_environment(temporary_directory=temporary)
    env.update(DECILIST_TRACE_OUTPUT=str(trace), DECILIST_TRACE_ROOT=str(snapshot.root))
    if capture_failure_text:
        env["DECILIST_TRACE_FAILURE_TEXT"] = "1"
    stdout_path, stderr_path = run_directory / "stdout.txt", run_directory / "stderr.txt"
    tick, timed_out, cleanup, launch_error = time.perf_counter(), False, None, None
    process = None
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
            process = subprocess.Popen(command, cwd=snapshot.root, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, shell=False, **options)
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                cleanup = _stop_owned_process(process)
        except OSError as exc:
            launch_error = type(exc).__name__
        finally:
            if process is not None and process.poll() is None:
                cleanup = _stop_owned_process(process)
    elapsed = time.perf_counter() - tick
    stdout, stdout_truncated = _read_output(stdout_path)
    stderr, stderr_truncated = _read_output(stderr_path)
    try:
        observed = json.loads(audit.read_text(encoding="utf-8"))
        if not isinstance(observed, dict):
            observed = {}
    except (OSError, ValueError):
        observed = {}
    try:
        after = _manifest(snapshot.root, snapshot.limits)
        snapshot_unchanged = before == after
    except (OSError, ValueError):
        after, snapshot_unchanged = {}, False
    returncode = process.returncode if process is not None else None
    collected = observed.get("collected", [])
    reports = observed.get("reports", [])
    audit_valid = isinstance(collected, list) and all(isinstance(item, str) for item in collected) and isinstance(reports, list) and all(isinstance(item, dict) for item in reports)
    ran = [row for row in reports if row.get("when") == "call"] if audit_valid else []
    actual_failures = [row for row in reports if row.get("outcome") == "failed"] if audit_valid else []
    state = "timeout" if timed_out else "error" if launch_error or not audit_valid or not audit.exists() else "empty" if not collected or not ran else "passed" if returncode == 0 and not actual_failures and any(row.get("outcome") == "passed" for row in ran) else "failed" if returncode == 1 and actual_failures else "error"
    original_after = _source_unchanged(snapshot)
    return {"state": state, "pytest_args": args, "command": command, "returncode": returncode, "timed_out": timed_out, "elapsed_seconds": elapsed, "stdout": stdout, "stderr": stderr, "stdout_path": str(stdout_path), "stderr_path": str(stderr_path), "stdout_truncated": stdout_truncated, "stderr_truncated": stderr_truncated, "trace_path": str(trace), "audit_path": str(audit), "collected": collected if audit_valid else [], "reports": reports if audit_valid else [], "collected_count": len(collected) if audit_valid else 0, "executed_count": len(ran), "hashes_before": before, "hashes_after": after, "snapshot_unchanged": snapshot_unchanged, "original_unchanged": original_before and original_after, "process_cleanup": cleanup, "launch_error": launch_error}
