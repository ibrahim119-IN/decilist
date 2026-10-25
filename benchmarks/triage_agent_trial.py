"""Local agent-run repair trials. No human timing or automatic repair execution."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import platform
import time
from uuid import uuid4

from .triage_study import _digest, _editable_paths, validate_loopback_url

SETTINGS = {"model": "gpt-6.1-sol", "reasoning_effort": "xhigh"}
PAIRS = {"A": ("a01", "a02"), "B": ("b01", "b02")}
CONDITIONS = {"normal_tools", "decilist_hint"}
ENDED = {"solved", "unsolved", "capped", "invalid", "withdrawn"}


class TrialError(ValueError):
    pass


def _lab_api():
    from benchmarks import triage_v2_lab
    return triage_v2_lab


def assignment(pair: str, sequence: int = 0) -> list[dict]:
    """Balance distinct frozen cases, condition order, and case-to-condition mapping."""
    if pair not in PAIRS or type(sequence) is not int or sequence not in range(4):
        raise TrialError("Choose frozen pair A/B and sequence 0–3.")
    first, second = PAIRS[pair] if sequence < 2 else PAIRS[pair][::-1]
    conditions = ("normal_tools", "decilist_hint") if sequence % 2 == 0 else ("decilist_hint", "normal_tools")
    return [{"case_id": case, "condition": condition, "pair": pair, "sequence": sequence, "position": number}
            for number, (case, condition) in enumerate(zip((first, second), conditions), start=1)]


def _clock() -> dict:
    monotonic = time.monotonic_ns()
    wall = time.time_ns()
    host = hashlib.sha256(platform.node().encode()).hexdigest()
    boot_file = Path("/proc/sys/kernel/random/boot_id")
    if boot_file.is_file():
        boot = {"kind": "linux_boot_id", "value": hashlib.sha256(boot_file.read_bytes()).hexdigest()}
    elif os.name == "nt":
        import ctypes
        uptime = ctypes.windll.kernel32.GetTickCount64
        uptime.restype = ctypes.c_ulonglong
        boot = {"kind": "windows_uptime_origin", "epoch_ns": wall - int(uptime()) * 1_000_000}
    else:
        boot = {"kind": "estimated_uptime_origin", "epoch_ns": wall - monotonic}
    return {"utc": datetime.fromtimestamp(wall / 1e9, timezone.utc).isoformat(),
            "wall_ns": wall, "monotonic_ns": monotonic, "host_sha256": host, "boot": boot}


def _elapsed(start: dict, end: dict) -> tuple[float | None, str | None]:
    if start["host_sha256"] != end["host_sha256"]:
        return None, "host_changed"
    before, after = start["boot"], end["boot"]
    if before["kind"] != after["kind"]:
        return None, "boot_clock_changed"
    if before["kind"] == "linux_boot_id":
        if before["value"] != after["value"]:
            return None, "host_rebooted"
    elif abs(before["epoch_ns"] - after["epoch_ns"]) > 5_000_000_000:
        return None, "reboot_or_wall_clock_changed"
    monotonic = end["monotonic_ns"] - start["monotonic_ns"]
    wall = end["wall_ns"] - start["wall_ns"]
    if monotonic < 0 or wall < 0:
        return None, "negative_clock_interval"
    if abs(monotonic - wall) > 5_000_000_000:
        return None, "wall_and_monotonic_disagree"
    return monotonic / 1e9, None


def _directory(value, lab) -> Path:
    directory, root = Path(value).resolve(), Path(lab.LAB_ROOT).resolve()
    if directory == root or not directory.is_relative_to(root):
        raise TrialError("Trial records must be new children of the fresh lab root.")
    return directory


def _write(directory: Path, state: dict) -> None:
    target = directory / f".trial-{uuid4().hex}.tmp"
    with target.open("x", encoding="utf-8") as output:
        json.dump(state, output, indent=2, ensure_ascii=False, allow_nan=False)
        output.write("\n")
    os.replace(target, directory / "trial.json")


@contextmanager
def _lock(directory):
    path = directory / ".trial.lock"
    try:
        with path.open("x", encoding="utf-8") as output:
            output.write(str(os.getpid()))
    except FileExistsError as exc:
        raise TrialError("Another trial command is running; do not overlap record writes.") from exc
    try:
        yield
    finally:
        path.unlink()


def _source_hashes(case_dir: Path, paths: list[str]) -> dict:
    return {path: _digest(case_dir / path) if (case_dir / path).is_file() else None for path in paths}


def _read(directory: Path) -> dict:
    state = json.loads((directory / "trial.json").read_text(encoding="utf-8"))
    case_dir = (directory / "case").resolve()
    if state.get("run_type") != "agent-run" or state.get("agent_settings") != SETTINGS:
        raise TrialError("This is not a declared gpt-6.1-sol/xhigh agent trial.")
    if state.get("case_dir") != str(case_dir) or not case_dir.is_relative_to(directory):
        raise TrialError("The trial workspace path changed.")
    manifest = case_dir / ".triage/manifest.json"
    if _digest(manifest) != state["manifest_sha256"]:
        raise TrialError("The protected case manifest changed.")
    if _digest(directory / "original-failure.log") != state["baseline_run"]["run_sha256"]:
        raise TrialError("The original failing log changed.")
    return state


def prepare_trial(case_id: str, condition: str, directory, *, agent_id: str,
                  planned_assignment: dict, cap_seconds: int = 900, lab=None) -> dict:
    """Prepare only. Agent clocks stay null until an explicit start command."""
    lab = lab or _lab_api()
    if condition not in CONDITIONS or not agent_id.strip():
        raise TrialError("Declare an agent identity and a normal_tools/decilist_hint condition.")
    if planned_assignment not in assignment(planned_assignment.get("pair"), planned_assignment.get("sequence")):
        raise TrialError("The trial must use a frozen counterbalanced assignment.")
    if (case_id, condition) != (planned_assignment["case_id"], planned_assignment["condition"]):
        raise TrialError("Case and condition differ from the declared assignment.")
    if type(cap_seconds) is not int or not 1 <= cap_seconds <= 86400:
        raise TrialError("The agent cap must be 1–86400 seconds.")
    directory = _directory(directory, lab)
    if directory.exists():
        raise TrialError("The trial directory exists; nothing was overwritten.")
    directory.mkdir(parents=True)
    case_dir = directory / "case"
    manifest = lab.prepare_case(case_id, case_dir)
    editable = _editable_paths(manifest)
    if (case_dir / ".git").exists():
        raise TrialError("Participant workspaces must not contain Git history.")
    prepared = _clock()
    run = lab.run_case(case_dir)
    if run["exit_code"] != 1 or not run["protected_files_ok"] or not run["source_stable"]:
        raise TrialError("The fresh case did not reproduce a stable protected test failure.")
    (directory / "original-failure.log").write_bytes(run["stdout"].encode("utf-8"))
    metadata = {key: value for key, value in run.items() if key != "stdout"}
    state = {"schema_version": 1, "run_type": "agent-run", "agent_id": agent_id,
             "agent_settings": dict(SETTINGS), "settings_source": "orchestrator declaration",
             "case_id": case_id, "condition": condition, "assignment": planned_assignment,
             "case_dir": str(case_dir), "project": manifest.get("project"),
             "editable_paths": editable, "manifest_sha256": _digest(case_dir / ".triage/manifest.json"),
             "original_buggy_source_sha256": _source_hashes(case_dir, editable),
             "baseline_run": metadata, "prepared_at": prepared["utc"], "status": "ready",
             "cap_seconds": cap_seconds, "start_clock": None, "end_clock": None,
             "agent_elapsed_seconds": None, "timing_issue": None,
             "attempts": [], "hints": [], "verifications": [], "verified_repair": False,
             "unmeasured": {"human_elapsed_seconds": None, "human_minutes_saved": None,
                            "token_usage": None, "cost_usd": None}}
    _write(directory, state)
    return state


def start_trial(directory, *, lab=None) -> dict:
    lab = lab or _lab_api()
    directory = _directory(directory, lab)
    with _lock(directory):
        state = _read(directory)
        if state["status"] != "ready":
            raise TrialError("A trial clock cannot be restarted.")
        state["start_clock"], state["status"] = _clock(), "active"
        _write(directory, state)
        return state


def _active(state):
    if state["status"] != "active":
        raise TrialError("Start this prepared agent trial before recording work.")


def begin_attempt(directory, command: str | list[str], *, tool: str = "shell", lab=None) -> str:
    """Bracket real tool work; supplied command text is recorded, never executed."""
    lab = lab or _lab_api()
    directory = _directory(directory, lab)
    if not command or not isinstance(command, (str, list)):
        raise TrialError("Record the actual observed tool action.")
    with _lock(directory):
        state = _read(directory)
        _active(state)
        identifier = uuid4().hex
        state["attempts"].append({"id": identifier, "tool": tool, "command": command,
                                  "origin": "agent-or-orchestrator-observed", "executed_by_runner": False,
                                  "start_clock": _clock(), "end_clock": None,
                                  "elapsed_seconds": None, "exit_code": None, "observation": None})
        _write(directory, state)
        return identifier


def end_attempt(directory, attempt_id: str, *, exit_code: int | None = None,
                observation: str | None = None, lab=None) -> dict:
    lab = lab or _lab_api()
    directory = _directory(directory, lab)
    with _lock(directory):
        state = _read(directory)
        _active(state)
        attempt = next((item for item in state["attempts"] if item["id"] == attempt_id), None)
        if attempt is None or attempt["end_clock"] is not None:
            raise TrialError("No unfinished tool attempt with that identifier exists.")
        attempt["end_clock"] = _clock()
        attempt["elapsed_seconds"], attempt["timing_issue"] = _elapsed(attempt["start_clock"], attempt["end_clock"])
        attempt["exit_code"], attempt["observation"] = exit_code, observation
        attempt["editable_source_sha256_after"] = _source_hashes(Path(state["case_dir"]), state["editable_paths"])
        _write(directory, state)
        return attempt


def _finish(state: dict, status: str, clock: dict):
    state["end_clock"] = clock
    state["agent_elapsed_seconds"], state["timing_issue"] = _elapsed(state["start_clock"], clock)
    state["status"] = "invalid" if state["timing_issue"] else status
    state["final_source_sha256"] = _source_hashes(Path(state["case_dir"]), state["editable_paths"])


def check_trial(directory, *, verify: bool = False, lab=None) -> dict:
    """Execute only the lab's fixed trusted checks; never a recorded task command."""
    lab = lab or _lab_api()
    directory = _directory(directory, lab)
    with _lock(directory):
        state = _read(directory)
        _active(state)
        before = _clock()
        if verify:
            validation = lab.validate_repair(Path(state["case_dir"]))
            run = validation.get("run")
        else:
            run = lab.run_case(Path(state["case_dir"]))
            validation = {"passed": False, "integrity_ok": run["protected_files_ok"], "run": run}
        after = _clock()
        verified = bool(
            verify and validation.get("passed") and validation.get("integrity_ok")
            and isinstance(run, dict) and run.get("exit_code") == 0
            and run.get("protected_files_ok") and run.get("source_stable")
            and not run.get("timed_out")
        )
        if verified and run.get("editable_sha256_after") is not None:
            verified = run["editable_sha256_after"] == _source_hashes(Path(state["case_dir"]), state["editable_paths"])
        record = {"kind": "verify" if verify else "check", "start_clock": before, "end_clock": after,
                  "elapsed_seconds": _elapsed(before, after)[0], "validation": validation,
                  "verified_endpoint": verified}
        state["verifications"].append(record)
        if verified:
            state["verified_repair"] = True
        elapsed, issue = _elapsed(state["start_clock"], after)
        if not validation["integrity_ok"] or issue:
            _finish(state, "invalid", after)
        elif verified:
            _finish(state, "solved" if elapsed <= state["cap_seconds"] else "capped", after)
        elif elapsed >= state["cap_seconds"]:
            _finish(state, "capped", after)
        record["trial_status"] = state["status"]
        record["agent_elapsed_seconds"] = state["agent_elapsed_seconds"] if state["status"] in ENDED else elapsed
        _write(directory, state)
        return record


def verify_trial(directory, *, lab=None) -> dict:
    return check_trial(directory, verify=True, lab=lab)


async def hint_trial(directory, *, callback, hint_version: str, url: str = "http://127.0.0.1:8765", fresh_run: bool = False, lab=None) -> dict:
    """An explicit versioned hook is required; no silent fallback to the old assistant."""
    url = validate_loopback_url(url)
    if not callable(callback) or not hint_version.strip():
        raise TrialError("Declare the actual hint API and version; no assistant is selected implicitly.")
    lab = lab or _lab_api()
    directory = _directory(directory, lab)
    with _lock(directory):
        state = _read(directory)
        _active(state)
        if state["condition"] != "decilist_hint":
            raise TrialError("This normal-tools trial does not permit a Decilist hint.")
        before = _clock()
        elapsed, issue = _elapsed(state["start_clock"], before)
        if issue or elapsed >= state["cap_seconds"]:
            _finish(state, "invalid" if issue else "capped", before)
            _write(directory, state)
            raise TrialError("Trial clock/cap does not permit another hint.")
        target_callback = getattr(callback, "__trial_target__", callback)
        source = inspect.getsourcefile(target_callback)
        source_before = _digest(Path(source)) if source and Path(source).is_file() else None
        entry = {"version": hint_version, "callback": f"{target_callback.__module__}:{target_callback.__qualname__}",
                 "source_sha256_before": source_before, "source_sha256_after": None,
                 "source_stable": None, "url": url, "start_clock": before, "end_clock": None,
                 "elapsed_seconds": None, "status": "pending", "route": None,
                 "runtime": None, "report_path": None, "fresh_run_requested": fresh_run,
                 "evidence_run": None, "assistant_source": None, "rule": None,
                 "assistant_version": None, "specification_sha256": None, "model_policy": None,
                 "case_source_sha256_before": None, "case_source_sha256_after": None,
                 "case_source_stable": None}
        state["hints"].append(entry)
        _write(directory, state)
        try:
            case_dir = Path(state["case_dir"])
            changed = _source_hashes(case_dir, state["editable_paths"]) != state["original_buggy_source_sha256"]
            if changed and not fresh_run:
                raise TrialError("Production changed after the original failure. Request an explicit fresh protected run before another hint.")
            if fresh_run:
                observed = lab.run_case(case_dir)
                entry["evidence_run"] = observed
                if not observed["protected_files_ok"] or not observed["source_stable"]:
                    raise TrialError("Fresh hint evidence failed integrity/source-stability checks.")
                if observed["exit_code"] == 0:
                    raise TrialError("Fresh protected tests pass; verify the repair instead of inventing a failure hint.")
            else:
                observed = {**state["baseline_run"], "stdout": (directory / "original-failure.log").read_text(encoding="utf-8")}
            evidence = lab.prepare_case_evidence(Path(state["case_dir"]), observed)
            entry["case_source_sha256_before"] = _source_hashes(case_dir, state["editable_paths"])
            if evidence.get("source_context_omitted") or entry["case_source_sha256_before"] != observed.get("editable_sha256_after"):
                raise TrialError("The failure and current source no longer match; request a fresh protected run.")
            result = callback(evidence, url=url, project=case_dir)
            report = await result if inspect.isawaitable(result) else result
            if not isinstance(report, dict):
                raise TrialError("The explicit hint API did not return a structured report.")
            folder = directory / "hints"
            folder.mkdir(exist_ok=True)
            target = folder / f"hint-{len(state['hints'])}.json"
            with target.open("x", encoding="utf-8") as output:
                json.dump(report, output, indent=2, ensure_ascii=False, allow_nan=False)
            entry.update(status=report.get("status"), route=report.get("route"), runtime=report.get("runtime"), report_path=str(target),
                         assistant_source=report.get("source"), rule=report.get("rule"),
                         assistant_version=report.get("assistant_version"), specification_sha256=report.get("specification_sha256"),
                         model_policy=report.get("model_policy"))
            return report
        except BaseException as exc:
            entry["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "error"
            entry["error_type"] = type(exc).__name__
            raise
        finally:
            after = _clock()
            entry["end_clock"] = after
            entry["elapsed_seconds"], entry["timing_issue"] = _elapsed(before, after)
            entry["source_sha256_after"] = _digest(Path(source)) if source and Path(source).is_file() else None
            entry["source_stable"] = entry["source_sha256_before"] == entry["source_sha256_after"]
            entry["case_source_sha256_after"] = _source_hashes(Path(state["case_dir"]), state["editable_paths"])
            entry["case_source_stable"] = entry["case_source_sha256_before"] == entry["case_source_sha256_after"] if entry["case_source_sha256_before"] is not None else None
            _write(directory, state)


def stop_trial(directory, *, reason: str = "unsolved", lab=None) -> dict:
    if reason not in {"unsolved", "withdrawn", "cap"}:
        raise TrialError("Stop reason must be unsolved, withdrawn, or cap.")
    lab = lab or _lab_api()
    directory = _directory(directory, lab)
    with _lock(directory):
        state = _read(directory)
        _active(state)
        now = _clock()
        elapsed, _ = _elapsed(state["start_clock"], now)
        if reason == "cap" and elapsed is not None and elapsed < state["cap_seconds"]:
            raise TrialError("The configured cap has not elapsed.")
        status = "capped" if reason == "cap" or elapsed is not None and elapsed >= state["cap_seconds"] else reason
        _finish(state, status, now)
        _write(directory, state)
        return state


def status(directory, *, lab=None) -> dict:
    lab = lab or _lab_api()
    state = _read(_directory(directory, lab))
    durations = [item["elapsed_seconds"] for item in state["hints"]]
    hint_runs = [item["evidence_run"] for item in state["hints"] if item.get("evidence_run")]
    test_durations = [(item["validation"].get("run") or {}).get("elapsed_ms") for item in state["verifications"]] + [item.get("elapsed_ms") for item in hint_runs]
    return {"run_type": "agent-run", "case_id": state["case_id"], "condition": state["condition"],
            "agent_settings": state["agent_settings"], "status": state["status"],
            "verified_repair": state["verified_repair"], "agent_elapsed_seconds": state["agent_elapsed_seconds"],
            "timing_issue": state["timing_issue"], "tool_attempts": len(state["attempts"]),
            "failed_tool_attempts": sum(type(item["exit_code"]) is int and item["exit_code"] != 0 for item in state["attempts"]),
            "trusted_test_calls": len(state["verifications"]) + len(hint_runs),
            "trusted_test_elapsed_ms": sum(test_durations) if test_durations and all(value is not None for value in test_durations) else None,
            "failed_verifications": sum(item["kind"] == "verify" and not item["verified_endpoint"] for item in state["verifications"]),
            "hint_requests": len(state["hints"]), "hint_seconds": sum(durations) if durations and all(value is not None for value in durations) else None,
            "hint_errors": sum(item["status"] in {"error", "interrupted", "unavailable"} for item in state["hints"]),
            "incomplete_attempts": sum(item["end_clock"] is None for item in state["attempts"]),
            "hint_treatment_requested": bool(state["hints"]), "unmeasured": state["unmeasured"]}


async def _cli_hint(args):
    validate_loopback_url(args.url)
    module, separator, name = args.hint_api.partition(":")
    if not separator or not module.startswith("decilist."):
        raise TrialError("Choose an explicitly versioned trusted decilist.module:callable API.")
    target = getattr(importlib.import_module(module), name)
    from decilist import Client
    async def call(evidence, *, url, project):
        async with Client(base_url=url) as client:
            result = target(evidence, client=client, project=project, mode=args.mode)
            return await result if inspect.isawaitable(result) else result
    call.__trial_target__ = target
    return await hint_trial(args.trial, callback=call, hint_version=args.hint_version, url=args.url, fresh_run=args.fresh_run)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("start", "attempt-start", "attempt-end", "check", "verify", "hint", "stop", "status"):
        sub = subparsers.add_parser(action)
        sub.add_argument("--trial", required=True)
        if action == "attempt-start":
            sub.add_argument("--command", required=True)
            sub.add_argument("--tool", default="shell")
        elif action == "attempt-end":
            sub.add_argument("--id", required=True)
            sub.add_argument("--exit-code", type=int)
            sub.add_argument("--observation")
        elif action == "hint":
            sub.add_argument("--hint-api", required=True)
            sub.add_argument("--hint-version", required=True)
            sub.add_argument("--url", default="http://127.0.0.1:8765")
            sub.add_argument("--mode", choices=("auto", "rules", "model"), default="auto")
            sub.add_argument("--fresh-run", action="store_true")
        elif action == "stop":
            sub.add_argument("--reason", choices=("unsolved", "withdrawn", "cap"), default="unsolved")
    args = parser.parse_args(argv)
    try:
        if args.action == "attempt-start":
            result = {"attempt_id": begin_attempt(args.trial, args.command, tool=args.tool)}
        elif args.action == "attempt-end":
            result = end_attempt(args.trial, args.id, exit_code=args.exit_code, observation=args.observation)
        elif args.action == "check":
            result = check_trial(args.trial)
        elif args.action == "verify":
            result = verify_trial(args.trial)
        elif args.action == "hint":
            result = asyncio.run(_cli_hint(args))
        elif args.action == "stop":
            result = stop_trial(args.trial, reason=args.reason)
        else:
            result = start_trial(args.trial) if args.action == "start" else status(args.trial)
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        return 0
    except (OSError, ValueError, AttributeError) as exc:
        print(f"Agent trial: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
