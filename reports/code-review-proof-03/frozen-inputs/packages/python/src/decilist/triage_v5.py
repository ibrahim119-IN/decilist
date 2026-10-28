"""One explicit trusted-project triage experiment with retained test evidence.

Usage: python -m decilist.triage_v5 --project PROJECT --test tests/test_bug.py
    --regression tests/test_existing.py --work-dir NEW_DIRECTORY --model-config MODEL.json

Project tests run with the caller's permissions in disposable copies. Local
model output is a hypothesis, and passing unchanged tests supports that
hypothesis without establishing a complete causal proof.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import difflib
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import stat
from time import perf_counter
from typing import Any, Mapping, Sequence
import uuid

from . import triage_verify_v5 as runner
from .triage_context_v5 import build_context
from .triage_hypothesis_v5 import V5LlamaCppProvider, generate_hypothesis, validate_generated


VERSION = "triage-v5-workflow.1"
DEFAULT_MAX_CONTEXT_CHARS = 24000
SUPPORTED = "hypothesis_supported_by_tests"
_PRODUCTION_ROOTS = ("src", "packages/python/src", "vendor")
_TIMED_STAGES = ("snapshot", "baseline", "context", "model", "patch", "verification", "application", "final_integrity")


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                               allow_nan=False) + "\n", encoding="utf-8")


@contextmanager
def _stage(report: dict, name: str, *, attempt: dict | None = None, label: str | None = None):
    """Retain wall time even when an operation raises or its guard returns."""
    started = perf_counter()
    try:
        yield
    finally:
        elapsed = (perf_counter() - started) * 1000
        report["timings_ms"][name] = report["timings_ms"].get(name, 0.0) + elapsed
        event = {"stage": name, "elapsed_ms": elapsed}
        if label is not None:
            event["label"] = label
        if attempt is not None:
            event["attempt"] = attempt["attempt"]
            attempt["timings_ms"][name] = attempt["timings_ms"].get(name, 0.0) + elapsed
        report["stage_timings"].append(event)


def load_model_config(path: Path | str) -> dict[str, str]:
    """Read identity settings only; no model file is opened or loaded."""
    with Path(path).open("rb") as handle:
        data = handle.read(16385)
    if len(data) > 16384:
        raise ValueError("Model identity configuration exceeds 16 KiB")
    from .llama_cpp import _nonfinite, _unique_object
    config = json.loads(data, object_pairs_hook=_unique_object, parse_constant=_nonfinite)
    if (not isinstance(config, dict) or set(config) - {"model", "expected_build", "expected_model_path"}
            or not {"model", "expected_build"} <= config.keys()
            or any(not isinstance(value, str) or not value.strip() for value in config.values())):
        raise ValueError("Model configuration needs model and expected_build, optionally expected_model_path")
    return config


def _intact(run: dict, expected: Mapping[str, str]) -> bool:
    return (run.get("snapshot_unchanged") is True and run.get("original_unchanged") is True
            and run.get("hashes_before") == expected and run.get("hashes_after") == expected)


def _actual_failure(run: dict) -> bool:
    return (run.get("state") == "failed" and run.get("returncode") == 1
            and any(row.get("outcome") == "failed" for row in run.get("reports", [])))


def _all_passed(run: dict) -> bool:
    """Do not support candidates which skip or stop executing collected tests."""
    collected = run.get("collected", [])
    calls = [row for row in run.get("reports", []) if row.get("when") == "call"]
    return (run.get("state") == "passed" and run.get("returncode") == 0 and bool(collected)
            and len(set(collected)) == len(collected) == len(calls)
            and {row.get("nodeid") for row in calls} == set(collected)
            and all(row.get("outcome") == "passed" for row in calls)
            and not any(row.get("outcome") == "failed" for row in run.get("reports", [])))


def _production_path(path: str, roots: Sequence[str]) -> str:
    relative = runner._relative(path)
    pure = PurePosixPath(relative)
    name = pure.name.casefold()
    blocked = any(part.casefold() in {"test", "tests", "testing", "config", "configs", "configuration", "manifest", "manifests"}
                  or part.startswith(".") for part in pure.parts)
    def allowed(root: str) -> bool:
        if root in {".", "./"}:
            return True
        return relative.startswith(runner._relative(root.rstrip("/\\")) + "/")
    if (pure.suffix != ".py" or blocked or name.startswith("test_")
            or name.endswith(("_test.py", "_config.py", "_settings.py", "_manifest.py"))
            or name in runner._PATCH_BLOCKED_NAMES
            or not any(allowed(root) for root in roots)):
        raise ValueError("Applying is restricted to permitted production Python files")
    return relative


def _atomic_bytes(path: Path, data: bytes) -> None:
    """Replace one existing file atomically; staged files are always ours."""
    temporary = path.with_name(".decilist-v5-" + uuid.uuid4().hex + ".tmp")
    mode = stat.S_IMODE(path.stat().st_mode)
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def apply_verified_candidate(snapshot: runner.Snapshot, patch: dict, *,
                             allowed_production_roots: Sequence[str] = _PRODUCTION_ROOTS) -> dict:
    """Copy verified bytes to the original after complete hash and link checks.

    Multi-file replacement is best effort: individual replacements are atomic,
    and an interruption or concurrent editor can prevent complete rollback.
    Caller must first establish the unchanged-test verification result.
    """
    report: dict[str, Any] = {"requested": True, "applied": False, "files": [],
                              "rollback": None, "reason": None}
    committed: list[str] = []
    originals: dict[str, bytes] = {}
    candidates: dict[str, bytes] = {}
    expected = dict(snapshot.source_hashes)
    try:
        runner._owned(snapshot)
        runner._checked_directory(snapshot.source_root)
        approved = patch["hashes_after"]
        if not runner._source_unchanged(snapshot):
            raise ValueError("Original project changed since verification began")
        if runner._manifest(snapshot.root, snapshot.limits) != approved:
            raise ValueError("Verified candidate snapshot changed after testing")
        for row in patch["replacements"]:
            relative = _production_path(row["path"], allowed_production_roots)
            if relative in originals:
                continue
            original, candidate = snapshot.source_root / relative, snapshot.root / relative
            runner._checked_directory(original.parent)
            runner._checked_directory(candidate.parent)
            if runner._is_link(original) or runner._is_link(candidate):
                raise ValueError("A verified file became a link or reparse point")
            originals[relative], candidates[relative] = original.read_bytes(), candidate.read_bytes()
            if (_hash(originals[relative]) != snapshot.source_hashes[relative]
                    or _hash(candidates[relative]) != approved[relative]
                    or _hash(candidates[relative]) == snapshot.source_hashes[relative]):
                raise ValueError("A source or candidate file hash does not match verification")
        if not originals or set(approved) != set(expected):
            raise ValueError("Expected a verified patch of existing source files")
        if any(value != expected[path] for path, value in approved.items() if path not in originals):
            raise ValueError("Verified snapshot contains unrelated changes")
        for relative in originals:
            # Recheck the complete source tree before every mutation, including
            # previous intentional writes, so unrelated edits are never ignored.
            if runner._manifest(snapshot.source_root, snapshot.limits) != expected:
                raise ValueError("Original project changed immediately before applying")
            target = snapshot.source_root / relative
            runner._checked_directory(target.parent)
            if runner._is_link(target) or _hash(target.read_bytes()) != expected[relative]:
                raise ValueError("An original target changed immediately before applying")
            _atomic_bytes(target, candidates[relative])
            committed.append(relative)
            expected[relative] = approved[relative]
        if runner._manifest(snapshot.source_root, snapshot.limits) != expected:
            raise ValueError("Original project changed while applying the verified candidate")
        report.update(applied=True, reason="verified_candidate_applied", files=[
            {"path": path, "sha256_before": snapshot.source_hashes[path], "sha256_after": approved[path]}
            for path in originals], source_hashes_after=expected)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        restored, failed = [], []
        for relative in reversed(committed):
            try:
                target = snapshot.source_root / relative
                runner._checked_directory(target.parent)
                if runner._is_link(target) or _hash(target.read_bytes()) != _hash(candidates[relative]):
                    raise ValueError("Concurrent source edit prevents safe rollback")
                _atomic_bytes(target, originals[relative])
                restored.append(relative)
            except (OSError, ValueError):
                failed.append(relative)
        report.update(reason="apply_rejected", error=str(exc),
                      rollback={"restored": restored, "failed": failed} if committed else None)
    return report


def _candidate_diff(snapshot: runner.Snapshot, replacements: list[dict]) -> str:
    lines: list[str] = []
    for relative in sorted({row["path"] for row in replacements}):
        # read_bytes preserves original CRLF and exact replacement matching.
        original = (snapshot.source_root / relative).read_bytes().decode("utf-8")
        candidate = (snapshot.root / relative).read_bytes().decode("utf-8")
        for line in difflib.unified_diff(original.splitlines(keepends=True), candidate.splitlines(keepends=True),
                                         fromfile="a/" + relative, tofile="b/" + relative):
            lines.append(line)
            if not line.endswith("\n"):
                lines.append("\n\\ No newline at end of file\n")
    return "".join(lines)


def _feedback(previous: dict, runs: Sequence[dict], *, capture_failure_text: bool) -> dict | None:
    rejected = next((run for run in runs if _actual_failure(run)), None)
    if rejected is None:
        return None
    # The exception text remains opt-in; default feedback contains only the
    # runner's actual node IDs, phases and outcomes, without captured values.
    output = ((rejected.get("stdout", "") + "\n" + rejected.get("stderr", "")).strip()
              if capture_failure_text else json.dumps({"collected": rejected["collected"],
                                                       "reports": rejected["reports"]}, ensure_ascii=False))
    return {"attempt": 1, "previous_result": previous,
            "rejection": {"kind": "test_rejection", "exit_code": rejected["returncode"],
                          "output": output[:12000] or "An audited test failed.", "timed_out": False}}


async def run_triage_v5(project: Path | str, tests: Sequence[str], regressions: Sequence[str],
                        work_dir: Path | str, model_config: Path | str, *,
                        capture_failure_text: bool = False, apply: bool = False,
                        provider=None, endpoint: str = "http://127.0.0.1:8792",
                        timeout_seconds: float = 30, max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
                        allowed_production_roots: Sequence[str] = _PRODUCTION_ROOTS) -> dict:
    """Reproduce once, generate at most twice, and verify unchanged selections.

    A supplied provider is caller-owned and is useful for explicit test
    transports. The default provider is closed here; its server is never stopped.
    No original project file is changed unless apply=True and verification passes.
    """
    started = perf_counter()
    result: dict[str, Any] = {"schema_version": 5, "workflow_version": VERSION, "state": "unverified",
        "reason": None, "explanation": runner.EXPLANATION, "project": str(Path(project).absolute()),
        "snapshot_directory": None, "source_hashes": None, "excluded": [], "baseline": None,
        "baseline_regression": None, "context_path": None, "attempts": [], "candidate_diff": "",
        "diff_path": None, "report_path": None, "apply": {"requested": apply, "applied": False},
        "capture_failure_text": capture_failure_text, "generation_limit": 2, "feedback_limit": 1,
        "timings_ms": {name: 0.0 for name in _TIMED_STAGES}, "stage_timings": [], "errors": []}
    original = None
    owned_provider = None
    active_attempt = None
    attempt_started = None
    try:
        if type(capture_failure_text) is not bool or type(apply) is not bool:
            raise ValueError("Failure-text capture and apply must be explicit booleans")
        if type(max_context_chars) is not int or not 4000 <= max_context_chars <= 96000:
            raise ValueError("Context limit must be between 4000 and 96000 characters")
        if (type(timeout_seconds) not in {int, float} or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= 3600):
            raise ValueError("Test timeout must be between zero and 3600 seconds")
        config = load_model_config(model_config)
        result["model_identity_config"] = config
        if provider is not None and any(getattr(provider, key, None) != value for key, value in config.items()):
            raise ValueError("Supplied provider identity differs from the explicit model configuration")
        with _stage(result, "snapshot", label="initial"):
            original = runner.snapshot_project(project, work_dir)
        result.update(snapshot_directory=str(original.directory), source_hashes=original.source_hashes,
                      excluded=list(original.excluded), report_path=str(original.evidence / "workflow.json"))
        failing = runner.validate_pytest_args(original, tests)
        regression_args = runner.validate_pytest_args(original, regressions)
        result["test_args"], result["regression_args"] = failing, regression_args
        with _stage(result, "baseline", label="failing_selection"):
            baseline = result["baseline"] = runner.run_pytest(original, failing, timeout_seconds=timeout_seconds,
                label="baseline", capture_failure_text=capture_failure_text)
            if not _intact(baseline, original.source_hashes):
                result["reason"] = "baseline_integrity_failure"
                return result
            if not _actual_failure(baseline):
                result["reason"] = "baseline_did_not_reproduce_a_real_test_failure"
                return result
        with _stage(result, "baseline", label="regression_selection"):
            baseline_regression = result["baseline_regression"] = runner.run_pytest(original, regression_args,
                timeout_seconds=timeout_seconds, label="baseline_regression", capture_failure_text=capture_failure_text)
            if (not _intact(baseline_regression, original.source_hashes)
                    or baseline_regression["state"] not in {"passed", "failed"}):
                result["reason"] = "baseline_regression_unavailable_or_changed"
                return result
        with _stage(result, "context"):
            trace_path = Path(baseline["trace_path"])
            with trace_path.open("rb") as handle:
                trace_data = handle.read(1048577)
            if len(trace_data) > 1048576:
                raise ValueError("Runtime trace exceeds the bounded workflow input")
            trace = json.loads(trace_data)
            context = build_context(original.root, trace, max_chars=max_context_chars)
            context_path = original.evidence / "context.json"
            _write_json(context_path, context)
            result["context_path"] = str(context_path)
            result["context_truncation"] = context.get("truncation")
        if provider is None:
            owned_provider = provider = V5LlamaCppProvider(base_url=endpoint, **config)
        feedback = None
        for index in range(2):
            if active_attempt is not None:
                active_attempt["timings_ms"]["total"] = (perf_counter() - attempt_started) * 1000
            attempt_started = perf_counter()
            attempt: dict[str, Any] = {"attempt": index + 1, "snapshot_directory": None,
                                       "generation": None, "patch": None, "candidate": None, "regression": None,
                                       "timings_ms": {name: 0.0 for name in ("snapshot", "model", "patch", "verification", "application")}}
            active_attempt = attempt
            result["attempts"].append(attempt)
            if index == 0:
                snapshot = original
            else:
                with _stage(result, "snapshot", attempt=attempt, label="feedback_fresh_copy"):
                    snapshot = runner.snapshot_project(project, original.directory / "attempt_2")
            attempt["snapshot_directory"] = str(snapshot.directory)
            if snapshot.source_hashes != original.source_hashes:
                result["reason"] = "source_changed_before_feedback_attempt"
                return result
            with _stage(result, "model", attempt=attempt):
                generation = attempt["generation"] = await generate_hypothesis(context, provider=provider, feedback=feedback)
                _write_json(original.evidence / f"generation_{index + 1}.json", generation)
                if isinstance(generation.get("result"), dict):
                    proposed = validate_generated(generation["result"], context).model_dump()
                    attempt["hypothesis"] = proposed
                if generation.get("status") != "hypothesis" or not isinstance(generation.get("result"), dict):
                    result["reason"] = generation.get("reason") or "model_did_not_supply_a_valid_hypothesis"
                    return result
                if not proposed["replacements"]:
                    result["reason"] = "hypothesis_has_no_safe_intervention"
                    return result
            with _stage(result, "patch", attempt=attempt):
                try:
                    patch = attempt["patch"] = runner.apply_replacements(snapshot, proposed["replacements"],
                        allowed_production_roots=allowed_production_roots)
                except (ValueError, OSError, SyntaxError, UnicodeError) as exc:
                    attempt["patch_error"] = str(exc)
                    result["reason"] = "candidate_patch_rejected"
                    return result
                approved = patch["hashes_after"]
                diff = _candidate_diff(snapshot, proposed["replacements"])
                diff_path = original.evidence / f"candidate_{index + 1}.diff"
                diff_path.write_text(diff, encoding="utf-8", newline="")
                attempt["diff_path"] = str(diff_path)
                result.update(candidate_diff=diff, diff_path=str(diff_path))
            with _stage(result, "verification", attempt=attempt):
                candidate = attempt["candidate"] = runner.run_pytest(snapshot, failing, timeout_seconds=timeout_seconds,
                    label="candidate", capture_failure_text=capture_failure_text, expected_hashes=approved)
                if not _intact(candidate, approved):
                    result["reason"] = "candidate_integrity_failure"
                    return result
                checked = attempt["regression"] = runner.run_pytest(snapshot, regression_args,
                    timeout_seconds=timeout_seconds, label="regression", capture_failure_text=capture_failure_text,
                    expected_hashes=approved)
                same_tests = baseline["collected"] == candidate["collected"]
                same_regressions = baseline_regression["collected"] == checked["collected"]
                attempt.update(test_selection_unchanged=same_tests, regression_selection_unchanged=same_regressions)
                if not _intact(checked, approved) or not same_tests or not same_regressions:
                    result["reason"] = "verification_integrity_failure"
                    return result
                verified = _all_passed(candidate) and _all_passed(checked) and runner._source_unchanged(snapshot)
            if verified:
                result.update(state=SUPPORTED, reason="failing_and_existing_regression_tests_passed",
                              hypothesis=proposed, verified_snapshot=str(snapshot.root),
                              original_unchanged_during_verification=True,
                              verification={"baseline_real_test_failure": True, "test_selection_unchanged": True,
                                  "regression_selection_unchanged": True, "protected_file_hashes_unchanged": True,
                                  "candidate_all_collected_tests_passed": True, "regression_all_collected_tests_passed": True})
                if apply:
                    with _stage(result, "application", attempt=attempt):
                        result["apply"] = apply_verified_candidate(snapshot, patch,
                            allowed_production_roots=allowed_production_roots)
                return result
            result["reason"] = "candidate_did_not_pass_unchanged_tests"
            if any(run["state"] not in {"passed", "failed"} for run in (candidate, checked)):
                result["reason"] = "candidate_test_runtime_unavailable"
                return result
            if any(run["state"] == "passed" and not _all_passed(run) for run in (candidate, checked)):
                result["reason"] = "candidate_did_not_execute_all_collected_tests"
                return result
            feedback = _feedback(proposed, [candidate, checked], capture_failure_text=capture_failure_text)
            if index == 1 or feedback is None:
                return result
            _write_json(original.evidence / "feedback.json", feedback)
    except Exception as exc:
        result["reason"] = result["reason"] or "workflow_guard_or_runtime_failure"
        result["errors"].append({"type": type(exc).__name__, "message": str(exc)[:1000]})
    finally:
        if active_attempt is not None:
            active_attempt["timings_ms"]["total"] = (perf_counter() - attempt_started) * 1000
        if owned_provider is not None:
            try:
                await owned_provider.aclose()
            except Exception as exc:
                result["errors"].append({"type": type(exc).__name__, "message": "Provider close failed"})
        if original is not None:
            with _stage(result, "final_integrity"):
                result["original_unchanged_at_end"] = runner._source_unchanged(original)
        result["timings_ms"]["total"] = (perf_counter() - started) * 1000
        if original is not None:
            _write_json(original.evidence / "workflow.json", result)
    return result


def _terminal(text: Any) -> str:
    return "".join(char for char in str(text) if char in "\n\t" or ord(char) >= 32 and not 127 <= ord(char) <= 159)


def format_text(report: dict | None) -> str:
    """Render full or guarded reports without requiring success-only fields."""
    report = report if isinstance(report, dict) else {}
    lines = ["Decilist V5: " + _terminal(report.get("state") or "unverified"),
             _terminal(report.get("reason") or "No verification result")]
    errors = report.get("errors")
    if isinstance(errors, list) and errors:
        error = errors[0]
        lines.append(_terminal(error.get("message", "Workflow unavailable") if isinstance(error, dict) else error))
    attempts = report.get("attempts")
    attempts = [item for item in attempts if isinstance(item, dict)] if isinstance(attempts, list) else []
    hypothesis = report.get("hypothesis") or (attempts[-1].get("hypothesis") if attempts else None)
    prose = hypothesis.get("hypothesis") if isinstance(hypothesis, dict) else hypothesis
    if isinstance(prose, str) and prose:
        lines.append("Hypothesis: " + _terminal(prose))
    runs = [("baseline", report.get("baseline")), ("baseline regression", report.get("baseline_regression"))]
    if attempts:
        final = attempts[-1]
        if final.get("patch_error"):
            lines.append(_terminal(final["patch_error"]))
        runs.extend((("candidate", final.get("candidate")), ("regression", final.get("regression"))))
    for label, run in runs:
        if isinstance(run, dict) and run:
            elapsed = run.get("elapsed_seconds")
            try:
                duration = f"{elapsed:.2f}s" if type(elapsed) in {int, float} and math.isfinite(elapsed) and elapsed >= 0 else "time unavailable"
            except (OverflowError, ValueError):
                duration = "time unavailable"
            lines.append(_terminal(f"{label}: {run.get('state', 'unavailable')} ({run.get('executed_count', '?')}/{run.get('collected_count', '?')} tests; {duration})"))
    if report.get("candidate_diff"):
        lines.extend(("", _terminal(report["candidate_diff"]).rstrip()))
    applied = report.get("apply") if isinstance(report.get("apply"), dict) else {}
    if applied.get("requested") is True:
        lines.append("Original project: " + ("verified candidate applied" if applied.get("applied") is True else "apply rejected or verification unavailable"))
    if report.get("report_path"):
        lines.append("Evidence: " + _terminal(report["report_path"]))
    lines.append(_terminal(report.get("explanation") or runner.EXPLANATION))
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--test", action="append", required=True, help="Explicit failing Python test selector; repeatable")
    parser.add_argument("--regression", action="append", required=True, help="Existing regression selector; repeatable")
    parser.add_argument("--work-dir", type=Path, required=True,
        help="New disposable-copy directory outside the project; missing non-reparse parents are created")
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8792")
    parser.add_argument("--capture-failure-text", action="store_true",
        help="Include dynamic failure text in model context; raw pytest output is always retained locally")
    parser.add_argument("--apply", action="store_true", help="Apply production edits only after unchanged-test verification")
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument("--max-context-chars", type=int, default=DEFAULT_MAX_CONTEXT_CHARS)
    parser.add_argument("--production-root", action="append",
        help="Permitted implementation directory; repeatable. Use . explicitly for flat-module projects")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    report = asyncio.run(run_triage_v5(args.project, args.test, args.regression, args.work_dir, args.model_config,
        capture_failure_text=args.capture_failure_text, apply=args.apply, endpoint=args.url,
        timeout_seconds=args.timeout_seconds, max_context_chars=args.max_context_chars,
        allowed_production_roots=args.production_root or _PRODUCTION_ROOTS))
    print(json.dumps(report, indent=2, ensure_ascii=False) if args.format == "json" else format_text(report), end="\n")
    return 0 if (isinstance(report, dict) and report.get("state") == SUPPORTED
        and (not args.apply or isinstance(report.get("apply"), dict) and report["apply"].get("applied") is True)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
