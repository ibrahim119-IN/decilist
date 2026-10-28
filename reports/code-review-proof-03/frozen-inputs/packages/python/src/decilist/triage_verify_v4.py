"""Bounded, disposable-copy verification for explicitly trusted project tests.

This runs Python code with the caller's operating-system permissions. It is not
a security sandbox. A passing candidate is evidence for a hypothesis, never a
complete proof of causality or correctness.
"""
from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import signal
import stat
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence
import uuid


EXPLANATION = "Tests passing supports the candidate hypothesis; it is not complete proof of causality or correctness."
_EXCLUDED_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "build", "dist", "target", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox", ".cache", ".idea", ".vscode", ".aws", ".azure", ".ssh", ".gnupg", ".kube", ".terraform"}
_PRIVATE_NAMES = {"credentials", "credentials.json", "secrets.json", "secrets.yaml", "secrets.yml", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".npmrc", ".pypirc", ".netrc", ".git-credentials"}
_PRIVATE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore", ".env", ".tfstate"}
_BINARY_SUFFIXES = {".gguf", ".safetensors", ".pt", ".pth", ".onnx", ".bin", ".exe", ".dll", ".so", ".dylib", ".zip", ".tar", ".gz", ".7z", ".mp4", ".mp3", ".png", ".jpg", ".jpeg", ".pdf"}
_PATCH_BLOCKED_NAMES = {"conftest.py", "setup.py", "noxfile.py", "manage.py", "sitecustomize.py", "usercustomize.py", "test.py", "tests.py", "config.py", "configuration.py", "settings.py", "manifest.py", "conf.py", "pytest.py"}
_DEFAULT_PRODUCTION_ROOTS = ("src", "packages/python/src", "vendor")
_PURE_NEW_CALLS = {"abs", "all", "any", "bool", "dict", "enumerate", "float", "frozenset", "int", "isinstance", "issubclass", "len", "list", "max", "min", "range", "repr", "reversed", "round", "set", "sorted", "str", "sum", "tuple", "zip"}
_DANGEROUS_CALLS = {"exec", "eval", "compile", "__import__", "open", "breakpoint", "globals", "locals", "getattr", "setattr", "delattr"}


@dataclass(frozen=True)
class SnapshotLimits:
    max_files: int = 4000
    max_total_bytes: int = 32 * 1024 * 1024
    max_file_bytes: int = 1024 * 1024
    max_binary_bytes: int = 128 * 1024

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in self.__dict__.values()):
            raise ValueError("Snapshot limits must be positive integers")


@dataclass(frozen=True)
class Snapshot:
    source_root: Path
    directory: Path
    root: Path
    evidence: Path
    source_hashes: dict[str, str]
    excluded: tuple[dict[str, str], ...]
    limits: SnapshotLimits
    identity: str


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_link(path: Path) -> bool:
    metadata = path.lstat()
    return stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def _checked_directory(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    for item in (absolute, *absolute.parents):
        if item.exists() and _is_link(item):
            raise ValueError("Symbolic links and reparse-point directory paths are not supported")
    return absolute


def _relative(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(char) < 32 for char in value):
        raise ValueError("Expected a relative project path")
    value = value.replace("\\", "/")
    parts = PurePosixPath(value)
    if parts.is_absolute() or PureWindowsPath(value).drive or any(part in {"..", "."} for part in value.split("/")) or ":" in value:
        raise ValueError("Paths must stay within the project")
    if not parts.parts or any(not part for part in value.split("/")):
        raise ValueError("Expected a normalized project path")
    return parts.as_posix()


def _exclusion(path: str, *, directory: bool = False, size: int = 0, limits: SnapshotLimits) -> str | None:
    name = PurePosixPath(path).name.casefold()
    if name in _EXCLUDED_DIRS:
        return "dependency_build_or_cache"
    if name.startswith(".env") or name in _PRIVATE_NAMES or PurePosixPath(name).suffix in _PRIVATE_SUFFIXES:
        return "environment_or_key_material"
    if not directory and size > limits.max_file_bytes:
        return "file_size_limit"
    if not directory and PurePosixPath(name).suffix in _BINARY_SUFFIXES and size > limits.max_binary_bytes:
        return "large_binary"
    return None


def _inventory(root: Path, limits: SnapshotLimits) -> tuple[dict[str, str], list[dict[str, str]]]:
    hashes, excluded, total, entries = {}, [], 0, 0
    pending = [root]
    while pending:
        current = pending.pop()
        for child in sorted(current.iterdir(), key=lambda item: item.name):
            entries += 1
            if entries > limits.max_files * 8:
                raise ValueError("Project exceeds the configured snapshot entry limit")
            relative = child.relative_to(root).as_posix()
            if _is_link(child):
                raise ValueError(f"Snapshot refuses symbolic links or reparse points: {relative}")
            metadata = child.stat()
            is_directory = stat.S_ISDIR(metadata.st_mode)
            reason = _exclusion(relative, directory=is_directory, size=metadata.st_size, limits=limits)
            if reason:
                excluded.append({"path": relative, "reason": reason})
                continue
            if is_directory:
                pending.append(child)
            elif stat.S_ISREG(metadata.st_mode):
                # The bounded read also detects growth between stat and read.
                with child.open("rb") as handle:
                    data = handle.read(limits.max_file_bytes + 1)
                if len(data) > limits.max_file_bytes:
                    raise ValueError(f"File grew beyond the snapshot limit: {relative}")
                if len(hashes) >= limits.max_files or total + len(data) > limits.max_total_bytes:
                    raise ValueError("Project exceeds the configured snapshot file or byte limit")
                hashes[relative] = _digest(data)
                total += len(data)
            else:
                raise ValueError(f"Snapshot refuses non-regular project files: {relative}")
    return hashes, excluded


def snapshot_project(source_root: Path | str, destination: Path | str, *, limits: SnapshotLimits | None = None) -> Snapshot:
    """Copy permitted project files into a new caller-owned destination/project.

    No existing destination is replaced. Symlinks and Windows junctions are
    rejected before reading. Excluded files are never read or hashed.
    """
    limits = limits or SnapshotLimits()
    source, destination = _checked_directory(Path(source_root)), _checked_directory(Path(destination))
    if not source.is_dir():
        raise ValueError("The source project must be an existing directory")
    if destination == source or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("Snapshot destination must be outside and separate from the source project")
    if destination.exists() or not destination.parent.is_dir():
        raise ValueError("Snapshot destination must be new and its parent must exist")
    hashes, excluded = _inventory(source, limits)
    identity = uuid.uuid4().hex
    destination.mkdir()
    snapshot = Snapshot(source, destination, destination / "project", destination / "evidence", hashes, tuple(excluded), limits, identity)
    try:
        (destination / ".decilist-v4-owner.json").write_text(json.dumps({"identity": identity}), encoding="utf-8")
        snapshot.root.mkdir()
        snapshot.evidence.mkdir()
        for relative, expected in hashes.items():
            origin = source / relative
            if _is_link(origin):
                raise ValueError("Source changed while creating the snapshot")
            with origin.open("rb") as handle:
                data = handle.read(limits.max_file_bytes + 1)
            if _digest(data) != expected:
                raise ValueError("Source changed while creating the snapshot")
            copied = snapshot.root / relative
            copied.parent.mkdir(parents=True, exist_ok=True)
            copied.write_bytes(data)
        if _inventory(source, limits)[0] != hashes:
            raise ValueError("Source changed while creating the snapshot")
        return snapshot
    except BaseException:
        cleanup_snapshot(snapshot)
        raise


def _owned(snapshot: Snapshot) -> None:
    if not isinstance(snapshot, Snapshot):
        raise ValueError("Expected an owned Snapshot")
    _checked_directory(snapshot.directory)
    marker = snapshot.directory / ".decilist-v4-owner.json"
    if _is_link(marker) or json.loads(marker.read_text(encoding="utf-8")) != {"identity": snapshot.identity}:
        raise ValueError("Snapshot ownership marker does not match")
    if snapshot.root != snapshot.directory / "project" or snapshot.evidence != snapshot.directory / "evidence":
        raise ValueError("Snapshot paths do not match the owned directory")


def cleanup_snapshot(snapshot: Snapshot) -> None:
    """Remove only this runner's new snapshot directory, after checking ownership."""
    _owned(snapshot)
    shutil.rmtree(snapshot.directory)


def _manifest(root: Path, limits: SnapshotLimits) -> dict[str, str]:
    return _inventory(root, limits)[0]


def _source_unchanged(snapshot: Snapshot) -> bool:
    try:
        return _manifest(snapshot.source_root, snapshot.limits) == snapshot.source_hashes
    except (OSError, ValueError):
        return False


def check_snapshot_integrity(snapshot: Snapshot, *, expected_hashes: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Check owned copy and original hashes without running project code."""
    _owned(snapshot)
    hashes = _manifest(snapshot.root, snapshot.limits)
    return {"snapshot_unchanged": hashes == (dict(expected_hashes) if expected_hashes is not None else snapshot.source_hashes), "original_unchanged": _source_unchanged(snapshot), "hashes": hashes}


def _screen_ast_changes(original: str, candidate: str, filename: str) -> None:
    """Conservative automatic-patch filter, explicitly not a security sandbox.

    Existing callable names can already have side effects and pure builtin
    names can be shadowed. This filter limits new effect surfaces; the caller
    still authorizes trusted test execution and reviews the concrete patch.
    """
    try:
        before, after = ast.parse(original, filename), ast.parse(candidate, filename)
    except (RecursionError, MemoryError) as exc:
        raise ValueError("Candidate AST exceeds conservative screening limits") from exc

    def imports(tree):
        return Counter(ast.dump(node, include_attributes=False) for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)))

    if imports(after) - imports(before):
        raise ValueError("Conservative patch screening rejects newly introduced imports")

    def dunders(tree):
        result = set()
        for node in ast.walk(tree):
            value = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else None
            if value and value.startswith("__") and value.endswith("__"):
                result.add(value)
        return result

    if dunders(after) - dunders(before):
        raise ValueError("Conservative patch screening rejects new dunder accesses or definitions")
    old_calls = Counter(ast.dump(node.func, include_attributes=False) for node in ast.walk(before) if isinstance(node, ast.Call))
    new_calls = [node for node in ast.walk(after) if isinstance(node, ast.Call)]
    old_dangerous = Counter(node.func.id for node in ast.walk(before) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _DANGEROUS_CALLS)
    new_dangerous = Counter(node.func.id for node in new_calls if isinstance(node.func, ast.Name) and node.func.id in _DANGEROUS_CALLS)
    if new_dangerous - old_dangerous:
        raise ValueError("Conservative patch screening rejects newly introduced dynamic or effectful builtin calls")
    for node in new_calls:
        if ast.dump(node.func, include_attributes=False) not in old_calls and not (isinstance(node.func, ast.Name) and node.func.id in _PURE_NEW_CALLS):
            raise ValueError("Conservative patch screening rejects a newly introduced callable shape")


def validate_pytest_args(snapshot: Snapshot, pytest_args: Sequence[str]) -> list[str]:
    """Accept explicit existing test selectors and a small set of pytest flags.

    Configuration, plugin, import-path, output, and collection-only controls are
    runner-owned. Arguments are never parsed by a command shell.
    """
    if isinstance(pytest_args, (str, bytes)) or not isinstance(pytest_args, (list, tuple)) or not pytest_args or len(pytest_args) > 100:
        raise ValueError("Supply a nonempty list of pytest arguments")
    result, selectors, index = [], 0, 0
    simple = {"-q", "-qq", "-v", "-vv", "-x", "-s", "--disable-warnings", "--strict-markers", "--strict-config"}
    choices = {"--tb": {"auto", "long", "short", "line", "native", "no"}, "--capture": {"fd", "sys", "no", "tee-sys"}}
    while index < len(pytest_args):
        item = pytest_args[index]
        if not isinstance(item, str) or not item or len(item) > 4096 or any(ord(char) < 32 for char in item):
            raise ValueError("Pytest arguments must be bounded strings without control characters")
        if item in simple:
            result.append(item)
        elif item in {"-p", "-o"}:
            index += 1
            allowed = "pytest_asyncio.plugin" if item == "-p" else "asyncio_mode=auto"
            if index >= len(pytest_args) or pytest_args[index] != allowed:
                raise ValueError("Only the known asyncio plugin and asyncio_mode=auto controls are permitted")
            result.extend([item, allowed])
        elif item in {"-k", "-m"}:
            index += 1
            if index >= len(pytest_args) or not isinstance(pytest_args[index], str) or not pytest_args[index] or len(pytest_args[index]) > 1000 or any(ord(char) < 32 for char in pytest_args[index]):
                raise ValueError("A pytest selection expression is required")
            result.extend([item, pytest_args[index]])
        elif any(item.startswith(name + "=") and item.split("=", 1)[1] in values for name, values in choices.items()):
            result.append(item)
        elif item.startswith("--maxfail=") and item.split("=", 1)[1].isdigit() and 1 <= int(item.split("=", 1)[1]) <= 100:
            result.append(item)
        elif item.startswith("-"):
            raise ValueError(f"Unsupported pytest command control: {item.split('=', 1)[0]}")
        else:
            path, *node = item.split("::")
            relative = _relative(path)
            target = snapshot.root / relative
            if not target.exists() or _is_link(target) or (not target.is_dir() and target.suffix != ".py"):
                raise ValueError("Each pytest selector must name an existing copied Python test or directory")
            if any(not part or any(ord(char) < 32 for char in part) for part in node):
                raise ValueError("Invalid pytest node selector")
            result.append(relative + ("::" + "::".join(node) if node else ""))
            selectors += 1
        index += 1
    if not selectors:
        raise ValueError("At least one explicit copied test selector is required")
    return result


def sanitized_environment(*, temporary_directory: Path) -> dict[str, str]:
    """Use an allowlist: credential-bearing ambient variables are not forwarded."""
    keep = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "LANG", "LC_ALL", "TZ", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"}
    env = {name: value for name, value in os.environ.items() if name.upper() in keep}
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", PYTHONHASHSEED="0", TEMP=str(temporary_directory), TMP=str(temporary_directory), TMPDIR=str(temporary_directory))
    return env


def _stop_owned_process(process: subprocess.Popen) -> str:
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        subprocess.run([str(Path(system_root) / "System32" / "taskkill.exe"), "/PID", str(process.pid), "/T", "/F"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, shell=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)
    return "owned_process_tree_terminated"


def _read_output(path: Path, maximum: int = 2 * 1024 * 1024) -> tuple[str, bool]:
    with path.open("rb") as handle:
        data = handle.read(maximum + 1)
    return data[:maximum].decode("utf-8", errors="replace"), len(data) > maximum


def run_pytest(snapshot: Snapshot, pytest_args: Sequence[str], *, timeout_seconds: float = 30, label: str = "run", capture_failure_text: bool = False, expected_hashes: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Run explicit pytest arguments in the copy using the standalone V4 worker."""
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
    request = {"root": str(snapshot.root), "pytest_args": args, "trace_output": str(trace), "trace_plugin": str(Path(__file__).with_name("triage_trace_v4.py")), "audit_output": str(audit), "basetemp": str(temporary / "pytest"), "capture_failure_text": capture_failure_text}
    request_path = run_directory / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    command = [sys.executable, "-I", "-B", str(Path(__file__).with_name("triage_test_worker_v4.py")), str(request_path)]
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


def apply_replacements(snapshot: Snapshot, replacements: Sequence[Mapping[str, str]], *, allowed_production_roots: Sequence[str] = _DEFAULT_PRODUCTION_ROOTS) -> dict[str, Any]:
    """Apply exact single-match, compiling production-Python edits only in the copy."""
    _owned(snapshot)
    if not isinstance(replacements, (list, tuple)) or not 1 <= len(replacements) <= 8:
        raise ValueError("A candidate needs one to eight structured replacements")
    if not isinstance(allowed_production_roots, (list, tuple)) or not allowed_production_roots:
        raise ValueError("Explicit permitted production roots are required")
    if any(not isinstance(root, str) for root in allowed_production_roots):
        raise ValueError("Permitted production roots must be relative project paths")
    # Flat-module projects can explicitly permit the project root. It is never
    # part of the default policy, and protected tests/configuration stay denied.
    roots = ["" if root in {".", "./"} else _relative(root.rstrip("/\\")) for root in allowed_production_roots]
    if _manifest(snapshot.root, snapshot.limits) != snapshot.source_hashes or not _source_unchanged(snapshot):
        raise ValueError("Project or copy changed before candidate application")
    plans, prepared, changed = {}, {}, []
    for row in replacements:
        if not isinstance(row, Mapping) or set(row) != {"path", "old", "new"}:
            raise ValueError("Replacements contain only path, old, and new")
        relative = _relative(row["path"])
        pure = PurePosixPath(relative)
        lowered = pure.name.casefold()
        blocked = any(part.casefold() in {"test", "tests", "testing", "config", "configs", "configuration", "manifest", "manifests"} or part.startswith(".") for part in pure.parts)
        if pure.suffix != ".py" or lowered.startswith("test_") or lowered.endswith(("_test.py", "_config.py", "_settings.py", "_manifest.py")) or lowered in _PATCH_BLOCKED_NAMES or blocked or not any(not root or relative.startswith(root + "/") for root in roots):
            raise ValueError("Candidate patches may change only permitted production Python files")
        if relative not in snapshot.source_hashes:
            raise ValueError("Candidate files must be copied existing files")
        old, new = row["old"], row["new"]
        if not isinstance(old, str) or not old or not isinstance(new, str) or old == new or len(old.encode("utf-8")) + len(new.encode("utf-8")) > 128 * 1024:
            raise ValueError("Candidate replacement strings must be nonempty, different, and bounded")
        path = snapshot.root / relative
        if _is_link(path):
            raise ValueError("Candidate file became a symbolic link or reparse point")
        data = path.read_bytes()
        if _digest(data) != snapshot.source_hashes[relative]:
            raise ValueError("Candidate source was modified before application")
        text = data.decode("utf-8")
        if text.count(old) != 1:
            raise ValueError("Each candidate old string must match exactly once")
        start = text.index(old)
        end = start + len(old)
        plan = plans.setdefault(relative, {"text": text, "data": data, "edits": []})
        if any(start < previous_end and previous_start < end for previous_start, previous_end, _ in plan["edits"]):
            raise ValueError("Candidate replacements in one file must not overlap")
        plan["edits"].append((start, end, new))
        changed.append({"path": relative, "sha256_before": _digest(data), "old_sha256": _digest(old.encode()), "new_sha256": _digest(new.encode())})
    for relative, plan in plans.items():
        candidate = plan["text"]
        for start, end, replacement in sorted(plan["edits"], reverse=True):
            candidate = candidate[:start] + replacement + candidate[end:]
        compile(candidate, relative, "exec", dont_inherit=True)
        _screen_ast_changes(plan["text"], candidate, relative)
        encoded = candidate.encode("utf-8")
        if len(encoded) > snapshot.limits.max_file_bytes:
            raise ValueError("Patched file exceeds the snapshot file limit")
        prepared[relative] = encoded
    for row in changed:
        row["sha256_after"] = _digest(prepared[row["path"]])
    for relative, data in prepared.items():
        (snapshot.root / relative).write_bytes(data)
    return {"replacements": changed, "hashes_after": _manifest(snapshot.root, snapshot.limits), "original_unchanged": _source_unchanged(snapshot), "screening": "conservative_ast_change_screen_v1", "screening_limitation": "Existing callable shapes can still have effects; this is not a security sandbox."}


def verify_candidate(source_root: Path | str, destination: Path | str, failing_test_args: Sequence[str], regression_test_args: Sequence[str], replacements: Sequence[Mapping[str, str]], *, timeout_seconds: float = 30, limits: SnapshotLimits | None = None, allowed_production_roots: Sequence[str] = _DEFAULT_PRODUCTION_ROOTS, capture_failure_text: bool = False) -> dict[str, Any]:
    """Reproduce a failure, apply a bounded patch, and rerun unchanged tests.

    Existing tests, configuration, manifests and the source project are never
    patched by this API. A failed baseline reproduction remains unverified.
    """
    snapshot = snapshot_project(source_root, destination, limits=limits)
    # Both selections must be valid before any project code is executed.
    failing = validate_pytest_args(snapshot, failing_test_args)
    regression = validate_pytest_args(snapshot, regression_test_args)
    result: dict[str, Any] = {"schema_version": 1, "state": "unverified", "explanation": EXPLANATION, "snapshot_directory": str(snapshot.directory), "snapshot_root": str(snapshot.root), "source_hashes": snapshot.source_hashes, "excluded": list(snapshot.excluded), "baseline": None, "candidate": None, "regression": None, "patch": None, "original_unchanged": True}
    try:
        baseline = result["baseline"] = run_pytest(snapshot, failing, timeout_seconds=timeout_seconds, label="baseline", capture_failure_text=capture_failure_text)
    except (ValueError, OSError) as exc:
        result.update(state="failed", reason="baseline_integrity_failure", error=str(exc), original_unchanged=_source_unchanged(snapshot))
        return result
    if not baseline["snapshot_unchanged"] or not baseline["original_unchanged"]:
        result.update(state="failed", reason="baseline_integrity_failure", original_unchanged=baseline["original_unchanged"])
        return result
    if baseline["state"] != "failed":
        result["reason"] = "baseline_did_not_reproduce_a_real_test_failure"
        return result
    try:
        result["patch"] = apply_replacements(snapshot, replacements, allowed_production_roots=allowed_production_roots)
    except (ValueError, SyntaxError, UnicodeError, OSError) as exc:
        result.update(state="failed", reason="candidate_patch_rejected", patch_error=str(exc), original_unchanged=_source_unchanged(snapshot))
        return result
    approved = result["patch"]["hashes_after"]
    try:
        candidate = result["candidate"] = run_pytest(snapshot, failing, timeout_seconds=timeout_seconds, label="candidate", capture_failure_text=capture_failure_text, expected_hashes=approved)
    except (ValueError, OSError) as exc:
        result.update(state="failed", reason="candidate_integrity_failure", error=str(exc), original_unchanged=_source_unchanged(snapshot))
        return result
    if not candidate["snapshot_unchanged"] or not candidate["original_unchanged"]:
        result.update(state="failed", reason="candidate_integrity_failure", original_unchanged=candidate["original_unchanged"])
        return result
    # Run the requested existing regressions even when the candidate still fails.
    try:
        checked = result["regression"] = run_pytest(snapshot, regression, timeout_seconds=timeout_seconds, label="regression", capture_failure_text=capture_failure_text, expected_hashes=approved)
    except (ValueError, OSError) as exc:
        result.update(state="failed", reason="regression_integrity_failure", error=str(exc), original_unchanged=_source_unchanged(snapshot))
        return result
    unchanged_tests = baseline["collected"] == candidate["collected"]
    failing_nodeids = {row["nodeid"] for row in baseline["reports"] if row.get("outcome") == "failed"}
    passing_nodeids = {row["nodeid"] for row in candidate["reports"] if row.get("when") == "call" and row.get("outcome") == "passed"}
    failures_now_pass = bool(failing_nodeids) and failing_nodeids <= passing_nodeids
    integrity = checked["snapshot_unchanged"] and checked["original_unchanged"] and unchanged_tests and failures_now_pass
    result.update(original_unchanged=_source_unchanged(snapshot), test_selection_unchanged=unchanged_tests, originally_failing_tests_now_pass=failures_now_pass)
    if candidate["state"] == checked["state"] == "passed" and integrity and result["original_unchanged"]:
        result.update(state="hypothesis_supported_by_tests", reason="failing_and_existing_regression_tests_passed")
    else:
        result.update(state="failed", reason="verification_failed" if integrity else "verification_integrity_failure")
    return result
