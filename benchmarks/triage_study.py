"""Deferred, local two-case triage pilot. Never generates or applies repairs."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import marshal
import os
from pathlib import Path, PurePosixPath
import platform
from time import perf_counter
from urllib.parse import urlsplit
from uuid import uuid4


SCHEMA_VERSION = 1
CASE_PAIR = ("t05", "t06")
# Order and case-to-condition assignment are balanced over four sessions.
SEQUENCES = (
    (("normal", "t05"), ("decilist", "t06")),
    (("decilist", "t05"), ("normal", "t06")),
    (("normal", "t06"), ("decilist", "t05")),
    (("decilist", "t06"), ("normal", "t05")),
)
TERMINAL = {"solved", "unsolved", "capped", "withdrawn", "invalid"}


class StudyError(ValueError):
    """An actionable preparation, protocol, or integrity problem."""


def _lab_api():
    from benchmarks import triage_lab
    return triage_lab


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp() -> str:
    return _now().isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise StudyError("Study timestamps must include a time zone.")
    return parsed


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _optional_digest(path) -> str | None:
    return _digest(Path(path)) if path is not None and Path(path).is_file() else None


def _hint_provenance() -> dict:
    """Identify the declared frozen contract and implementation actually loaded."""
    from decilist import client, triage, triage_evidence
    from .run_triage import canonical_hash, diagnosis_contract
    contract = diagnosis_contract()
    modules = {
        "benchmarks/triage_study.py": Path(__file__),
        "decilist/triage.py": Path(triage.__file__),
        "decilist/triage_evidence.py": Path(triage_evidence.__file__),
        "decilist/client.py": Path(client.__file__),
    }
    return {
        "python_version": platform.python_version(),
        "diagnosis_contract_sha256": canonical_hash(contract),
        "instructions_sha256": canonical_hash(contract["instructions"]),
        "routes_sha256": canonical_hash(contract["routes"]),
        "frozen_lab_policy": contract["policy"],
        "source_sha256": {name: _optional_digest(path) for name, path in modules.items()},
        "loaded_code_sha256": {
            "diagnose": hashlib.sha256(marshal.dumps(triage.diagnose.__code__)).hexdigest(),
            "prepare_evidence": hashlib.sha256(marshal.dumps(triage_evidence.prepare_evidence.__code__)).hexdigest(),
        },
    }


def _inside(path: Path, root: Path) -> bool:
    return path.is_relative_to(root) and path != root


def _session_directory(directory: str | Path | None, lab) -> Path:
    root = Path(lab.LAB_ROOT).resolve()
    selected = Path(directory) if directory is not None else root / "developer-study"
    selected = selected.expanduser().resolve()
    if not _inside(selected, root):
        raise StudyError(f"Study directories must be new children of {root}.")
    return selected


def _write(directory: Path, state: dict) -> None:
    target = directory / "session.json"
    temporary = directory / f".session-{uuid4().hex}.tmp"
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(state, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    os.replace(temporary, target)


@contextmanager
def _locked(directory: Path):
    lock = directory / ".study.lock"
    token = uuid4().hex
    try:
        with lock.open("x", encoding="utf-8") as stream:
            json.dump({"token": token, "pid": os.getpid(), "created_at": _stamp()}, stream)
    except FileExistsError as exc:
        raise StudyError("A study command is already running. Let it finish before continuing.") from exc
    try:
        yield
    finally:
        # Never remove a lock that another process or the participant replaced.
        if lock.exists() and json.loads(lock.read_text(encoding="utf-8")).get("token") == token:
            lock.unlink()


def _manifest(case_dir: Path) -> dict:
    return json.loads((case_dir / ".triage" / "manifest.json").read_text(encoding="utf-8"))


def _editable_paths(manifest: dict) -> list[str]:
    paths = manifest.get("editable_paths")
    if not isinstance(paths, list) or not paths:
        raise StudyError("The lab did not provide a production-file edit allowlist.")
    for value in paths:
        if not isinstance(value, str):
            raise StudyError("Invalid editable path in the frozen lab manifest.")
        path = PurePosixPath(value)
        if (
            path.is_absolute() or ".." in path.parts or "\\" in value
            or any(part in {"tests", "test", ".triage"} or part.startswith("test_") for part in path.parts)
        ):
            raise StudyError("Study cases may allow edits to production files only.")
    return paths


def _run_record(run: dict, manifest: dict) -> dict:
    return {
        "command": run.get("command", manifest.get("test_command")),
        "command_origin": "trusted_lab",
        "exit_code": run.get("exit_code"),
        "elapsed_ms": run.get("elapsed_ms"),
        "log_path": str(run.get("log_path", "")),
        "run_sha256": run.get("run_sha256"),
        "protected_files_ok": run.get("protected_files_ok"),
        "timed_out": run.get("timed_out"),
        "source_stable": run.get("source_stable"),
        "editable_sha256_before": run.get("editable_sha256_before", {}),
        "editable_sha256_after": run.get("editable_sha256_after", {}),
    }


def _event(trial: dict, kind: str, at: str, **details) -> None:
    trial["events"].append({"kind": kind, "at": at, **details})


def create_session(
    directory: str | Path | None = None, *, sequence: int = 0,
    cap_seconds: int = 1800, participant: str = "local-pilot", lab=None,
) -> dict:
    """Prepare distinct matched cases and baseline logs without starting a human clock."""
    lab = lab or _lab_api()
    if sequence not in range(len(SEQUENCES)):
        raise StudyError("Sequence must be 0, 1, 2, or 3.")
    if type(cap_seconds) is not int or not 1 <= cap_seconds <= 86400:
        raise StudyError("The per-case cap must be 1–86400 seconds.")
    if not participant.strip() or len(participant) > 120:
        raise StudyError("Use a short participant alias without personal details.")
    directory = _session_directory(directory, lab)
    if directory.exists():
        raise StudyError("The study directory already exists; nothing was overwritten.")
    directory.mkdir(parents=True)
    (directory / "baseline-logs").mkdir()
    state = {
        "schema_version": SCHEMA_VERSION, "kind": "paired-triage-pilot",
        "session_id": uuid4().hex, "participant": participant,
        "created_at": _stamp(), "status": "preparing", "sequence": sequence,
        "case_pair": list(CASE_PAIR), "cap_seconds": cap_seconds,
        "tools_policy": "Use the same existing tools in both cases; only the Decilist case adds a hint. Ordinary tool use is participant-reported, not monitored.",
        "preparation_provenance": {
            "study_source_sha256": _digest(Path(__file__)),
            "lab_catalog_sha256": _optional_digest(getattr(lab, "CATALOG", None)),
            "lab_freeze_sha256": _optional_digest(getattr(lab, "FREEZE", None)),
            "base_revision": getattr(lab, "BASE_REVISION", None),
        },
        "interpretation": "Two matched cases are a feasibility pilot, not evidence of general speedup.",
        "trials": [],
    }
    _write(directory, state)
    try:
        for index, (condition, case_id) in enumerate(SEQUENCES[sequence], start=1):
            case_dir = directory / f"trial-{index}"
            manifest = lab.prepare_case(case_id, case_dir)
            editable = _editable_paths(manifest)
            before = _stamp()
            run = lab.run_case(case_dir)
            after = _stamp()
            if (
                run.get("exit_code") != 1
                or run.get("protected_files_ok") is not True
                or run.get("source_stable") is not True
            ):
                raise StudyError("A prepared case did not reproduce its protected failing tests.")
            log = run.get("stdout")
            if not isinstance(log, str) or not log.strip():
                raise StudyError("The lab did not return the original failing pytest log.")
            baseline = directory / "baseline-logs" / f"trial-{index}.log"
            baseline.write_text(log, encoding="utf-8")
            trial = {
                "number": index, "condition": condition, "case_id": case_id,
                "case_dir": str(case_dir), "editable_paths": editable,
                "manifest_sha256": _digest(case_dir / ".triage" / "manifest.json"),
                "baseline_log": str(baseline), "baseline_sha256": _digest(baseline),
                "baseline_run": _run_record(run, manifest),
                "status": "ready", "started_at": None, "ended_at": None,
                "active_intervals": [], "interruptions": [], "events": [],
                "commands": [{"started_at": before, "finished_at": after, **_run_record(run, manifest), "phase": "preparation"}],
                "hint_requests": [], "verifications": [], "tool_notes": [],
                "verified_repair": False, "human_elapsed_seconds": None,
                "wall_elapsed_seconds": None, "timing_valid": None,
            }
            state["trials"].append(trial)
            _write(directory, state)
        state["status"] = "ready"
    except Exception as exc:
        state["status"] = "preparation_failed"
        state["preparation_error"] = str(exc)
        _write(directory, state)
        raise
    _write(directory, state)
    return state


def load_session(directory: str | Path | None = None, *, lab=None) -> tuple[Path, dict]:
    lab = lab or _lab_api()
    directory = _session_directory(directory, lab)
    target = directory / "session.json"
    if not target.is_file() or target.stat().st_size > 8_000_000:
        raise StudyError("No readable prepared study session was found.")
    state = json.loads(target.read_text(encoding="utf-8"))
    if state.get("schema_version") != SCHEMA_VERSION or state.get("case_pair") != list(CASE_PAIR):
        raise StudyError("The study session does not match this frozen protocol.")
    sequence = state.get("sequence")
    if type(sequence) is not int or sequence not in range(len(SEQUENCES)):
        raise StudyError("Invalid study assignment.")
    if type(state.get("cap_seconds")) is not int or not 1 <= state["cap_seconds"] <= 86400:
        raise StudyError("Invalid study time cap.")
    trials = state.get("trials")
    if not isinstance(trials, list) or len(trials) != 2:
        raise StudyError("The two study cases were not prepared successfully.")
    for number, (trial, assignment) in enumerate(zip(trials, SEQUENCES[sequence]), start=1):
        case_dir = (directory / f"trial-{number}").resolve()
        if not _inside(case_dir, directory) or trial.get("case_dir") != str(case_dir):
            raise StudyError("A participant case path changed or escaped its study directory.")
        if (trial.get("condition"), trial.get("case_id")) != assignment:
            raise StudyError("The counterbalanced assignment changed.")
        manifest_path = case_dir / ".triage" / "manifest.json"
        baseline = directory / "baseline-logs" / f"trial-{number}.log"
        if (
            trial.get("baseline_log") != str(baseline)
            or not manifest_path.is_file() or _digest(manifest_path) != trial.get("manifest_sha256")
            or not baseline.is_file() or _digest(baseline) != trial.get("baseline_sha256")
        ):
            raise StudyError("The frozen case manifest or original failing log changed.")
        manifest = _manifest(case_dir)
        if manifest.get("case_id") != trial["case_id"] or _editable_paths(manifest) != trial["editable_paths"]:
            raise StudyError("The frozen production-file edit allowlist changed.")
    return directory, state


def _active_trial(state: dict) -> dict:
    active = [trial for trial in state["trials"] if trial["status"] in {"active", "paused"}]
    if len(active) != 1:
        raise StudyError("Start the next case before recording work or checking a repair.")
    return active[0]


def _elapsed(trial: dict, at: str) -> tuple[float | None, float | None]:
    if trial["started_at"] is None:
        return None, None
    stop = trial["ended_at"] or at
    wall = (_parse(stop) - _parse(trial["started_at"])).total_seconds()
    active = 0.0
    previous_end = _parse(trial["started_at"])
    for interval in trial["active_intervals"]:
        begin, end = _parse(interval["started_at"]), _parse(interval["ended_at"] or stop)
        if begin < previous_end or end < begin:
            return None, None
        active += (end - begin).total_seconds()
        previous_end = end
    if wall < 0 or active < 0 or active > wall:
        return None, None
    return active, wall


def _finish(state: dict, trial: dict, status: str, at: str) -> None:
    if trial["status"] == "active":
        trial["active_intervals"][-1]["ended_at"] = at
    elif trial["status"] == "paused":
        trial["interruptions"][-1]["ended_at"] = at
    trial["ended_at"] = at
    elapsed, wall = _elapsed(trial, at)
    trial["human_elapsed_seconds"], trial["wall_elapsed_seconds"] = elapsed, wall
    trial["timing_valid"] = elapsed is not None
    trial["status"] = status
    _event(trial, status, at)
    state["status"] = "completed" if all(item["status"] in TERMINAL for item in state["trials"]) else "between_cases"


def start_trial(directory: str | Path | None = None, *, lab=None) -> dict:
    lab = lab or _lab_api()
    directory = _session_directory(directory, lab)
    with _locked(directory):
        _, state = load_session(directory, lab=lab)
        if any(trial["status"] in {"active", "paused"} for trial in state["trials"]):
            raise StudyError("A case is already in progress; its clock was not restarted.")
        trial = next((item for item in state["trials"] if item["status"] == "ready"), None)
        if trial is None:
            raise StudyError("Both study cases have ended.")
        at = _stamp()
        trial["status"], trial["started_at"] = "active", at
        trial["active_intervals"].append({"started_at": at, "ended_at": None})
        _event(trial, "started", at)
        state["status"] = "in_progress"
        _write(directory, state)
        return trial


def interrupt_trial(directory=None, *, reason: str, lab=None) -> dict:
    if not reason.strip() or len(reason) > 1000:
        raise StudyError("Record a short interruption reason.")
    lab = lab or _lab_api()
    directory = _session_directory(directory, lab)
    with _locked(directory):
        _, state = load_session(directory, lab=lab)
        trial = _active_trial(state)
        if trial["status"] != "active":
            raise StudyError("This case is already paused.")
        at = _stamp()
        trial["active_intervals"][-1]["ended_at"] = at
        trial["interruptions"].append({"started_at": at, "ended_at": None, "reason": reason})
        trial["status"] = "paused"
        _event(trial, "interrupted", at, reason=reason)
        _write(directory, state)
        return trial


def resume_trial(directory=None, *, lab=None) -> dict:
    lab = lab or _lab_api()
    directory = _session_directory(directory, lab)
    with _locked(directory):
        _, state = load_session(directory, lab=lab)
        trial = _active_trial(state)
        if trial["status"] != "paused":
            raise StudyError("This case is not paused.")
        at = _stamp()
        trial["interruptions"][-1]["ended_at"] = at
        trial["active_intervals"].append({"started_at": at, "ended_at": None})
        trial["status"] = "active"
        _event(trial, "resumed", at)
        _write(directory, state)
        return trial


def record_command(directory=None, *, command: str, lab=None) -> dict:
    """Record a participant annotation; never execute supplied command text."""
    if not command.strip() or len(command) > 4000:
        raise StudyError("Record a nonempty command of at most 4000 characters.")
    lab = lab or _lab_api()
    directory = _session_directory(directory, lab)
    with _locked(directory):
        _, state = load_session(directory, lab=lab)
        trial = _active_trial(state)
        trial["commands"].append({"at": _stamp(), "command": command, "command_origin": "participant_reported", "executed_by_study": False})
        _write(directory, state)
        return trial


def record_tool(directory=None, *, tool: str, lab=None) -> dict:
    """Record an editor/other-tool annotation without launching or monitoring it."""
    if not tool.strip() or len(tool) > 1000:
        raise StudyError("Record a short tool name or usage note.")
    lab = lab or _lab_api()
    directory = _session_directory(directory, lab)
    with _locked(directory):
        _, state = load_session(directory, lab=lab)
        trial = _active_trial(state)
        trial.setdefault("tool_notes", []).append({"at": _stamp(), "tool": tool, "origin": "participant_reported"})
        _write(directory, state)
        return trial


def verify_trial(directory=None, *, lab=None) -> dict:
    lab = lab or _lab_api()
    directory = _session_directory(directory, lab)
    with _locked(directory):
        _, state = load_session(directory, lab=lab)
        trial = _active_trial(state)
        if trial["status"] != "active":
            raise StudyError("Resume the case before verifying it.")
        before = _stamp()
        validation = lab.validate_repair(Path(trial["case_dir"]))
        at = _stamp()
        trial["verifications"].append({"started_at": before, "finished_at": at, "passed": validation.get("passed") is True, "integrity_ok": validation.get("integrity_ok") is True, "run": _run_record(validation.get("run") or {}, _manifest(Path(trial["case_dir"])))})
        if validation.get("run"):
            trial["commands"].append({"started_at": before, "finished_at": at, "phase": "verification", **_run_record(validation["run"], _manifest(Path(trial["case_dir"])))})
        elapsed, _ = _elapsed(trial, at)
        if validation.get("integrity_ok") is not True:
            _finish(state, trial, "invalid", at)
        elif validation.get("passed") is True:
            trial["verified_repair"] = True
            result = "invalid" if elapsed is None else "solved" if elapsed <= state["cap_seconds"] else "capped"
            _finish(state, trial, result, at)
        elif elapsed is not None and elapsed >= state["cap_seconds"]:
            _finish(state, trial, "capped", at)
        else:
            _event(trial, "verification_failed", at)
        _write(directory, state)
        return trial


def stop_trial(directory=None, *, reason: str = "unsolved", lab=None) -> dict:
    if reason not in {"unsolved", "cap", "withdrawn"}:
        raise StudyError("Stop reason must be unsolved, cap, or withdrawn.")
    lab = lab or _lab_api()
    directory = _session_directory(directory, lab)
    with _locked(directory):
        _, state = load_session(directory, lab=lab)
        trial = _active_trial(state)
        at = _stamp()
        elapsed, _ = _elapsed(trial, at)
        if reason == "cap" and (elapsed is None or elapsed < state["cap_seconds"]):
            raise StudyError("The configured cap has not elapsed; use unsolved or withdrawn.")
        ended = "capped" if reason == "cap" or (reason == "unsolved" and elapsed is not None and elapsed >= state["cap_seconds"]) else reason
        _finish(state, trial, ended, at)
        _write(directory, state)
        return trial


def validate_loopback_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise StudyError("Hint endpoint must be a loopback HTTP(S) origin without credentials or a path.")
    try:
        local = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname or "").is_loopback
        parsed.port
    except ValueError:
        local = False
    if not local:
        raise StudyError("Study hints require a loopback endpoint; no log or source was sent.")
    return url.rstrip("/")


async def _get_hint(directory: Path, trial: dict, url: str, lab) -> dict:
    from decilist import Client
    from decilist.triage import diagnose
    case_dir = Path(trial["case_dir"])
    observed = {
        **trial["baseline_run"],
        "stdout": Path(trial["baseline_log"]).read_text(encoding="utf-8"),
    }
    # The machine benchmark uses this exact helper too. Legitimate referenced
    # tests are evidence; hidden oracle labels and repair metadata are excluded.
    evidence = lab.prepare_case_evidence(case_dir, observed)
    async with Client(base_url=url) as client:
        return await diagnose(evidence, client=client)


async def request_hint(directory=None, *, url: str = "http://127.0.0.1:8765", lab=None) -> dict:
    # Reject remote destinations before reading or preparing any participant evidence.
    url = validate_loopback_url(url)
    lab = lab or _lab_api()
    directory = _session_directory(directory, lab)
    with _locked(directory):
        _, state = load_session(directory, lab=lab)
        trial = _active_trial(state)
        if trial["condition"] != "decilist":
            raise StudyError("The normal-tools case does not allow a Decilist hint.")
        if trial["status"] != "active":
            raise StudyError("Resume the case before requesting a hint.")
        before = _stamp()
        elapsed, _ = _elapsed(trial, before)
        if elapsed is not None and elapsed >= state["cap_seconds"]:
            _finish(state, trial, "capped", before)
            _write(directory, state)
            raise StudyError("The case cap elapsed; no hint was requested.")
        provenance = _hint_provenance()
        entry = {"started_at": before, "finished_at": None, "elapsed_seconds": None, "endpoint": url, "status": "pending", "report_path": None, "provenance_before": provenance, "provenance_after": None, "provenance_stable": None, "runtime": None}
        trial["hint_requests"].append(entry)
        _write(directory, state)
        tick = perf_counter()
        try:
            report = await _get_hint(directory, trial, url, lab)
            entry["status"] = report.get("status", "unavailable")
            entry["runtime"] = report.get("runtime")
            folder = directory / "hints"
            folder.mkdir(exist_ok=True)
            target = folder / f"trial-{trial['number']}-hint-{len(trial['hint_requests'])}.json"
            with target.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, indent=2, ensure_ascii=False, allow_nan=False)
            entry["report_path"] = str(target)
            return report
        except BaseException as exc:
            entry["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "error"
            entry["error_type"] = type(exc).__name__
            raise
        finally:
            entry["finished_at"] = _stamp()
            entry["elapsed_seconds"] = perf_counter() - tick
            try:
                entry["provenance_after"] = _hint_provenance()
                entry["provenance_stable"] = entry["provenance_before"] == entry["provenance_after"]
            except (OSError, ValueError) as exc:
                entry["provenance_stable"] = False
                entry["provenance_error"] = type(exc).__name__
            _write(directory, state)


def status(directory=None, *, lab=None) -> dict:
    directory, state = load_session(directory, lab=lab)
    at = _stamp()
    return {
        "session": str(directory), "status": state["status"],
        "interpretation": state["interpretation"],
        "trials": [{
            "number": trial["number"], "condition": trial["condition"],
            "case_id": trial["case_id"], "status": trial["status"],
            "verified_repair": trial["verified_repair"],
            "human_elapsed_seconds": _elapsed(trial, at)[0],
            "wall_elapsed_seconds": _elapsed(trial, at)[1],
            "interruptions": len(trial["interruptions"]),
            "hint_requests": len(trial["hint_requests"]),
            "hint_seconds": sum(item["elapsed_seconds"] for item in trial["hint_requests"]) if trial["hint_requests"] and all(item["elapsed_seconds"] is not None for item in trial["hint_requests"]) else None,
        } for trial in state["trials"]],
    }


def _print_trial(trial: dict, directory: Path) -> None:
    print(f"Case {trial['number']} · {trial['condition']} · {trial['status']}")
    print(f"Workspace: {trial['case_dir']}")
    print(f"Original failing log: {trial['baseline_log']}")
    print("Editable production files: " + ", ".join(trial["editable_paths"]))
    if trial["status"] == "active":
        print("Use your normal editor, source, logs, and existing tools. Keep every test unchanged.")
        _, state = load_session(directory)
        print(f"Per-case cap: {state['cap_seconds']} active seconds. Recorded hint/test waiting time counts; recorded interruptions do not.")
        if trial["condition"] == "decilist":
            print(f'Optional hint: python -m benchmarks.triage_study hint --session "{directory}"')
        print(f'Check the repair: python -m benchmarks.triage_study verify --session "{directory}"')
    elif trial["status"] in TERMINAL:
        print(f"Verified repair: {trial['verified_repair']}; recorded active seconds: {trial['human_elapsed_seconds']}")
        print("Run start again for the next prepared case, or status to inspect the record.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("create", "start", "hint", "verify", "interrupt", "resume", "stop", "note", "status"):
        sub = commands.add_parser(name)
        sub.add_argument("--session", "--directory", dest="session")
        if name == "create":
            sub.add_argument("--sequence", type=int, choices=range(4), default=0)
            sub.add_argument("--cap-seconds", type=int, default=1800)
            sub.add_argument("--participant", default="local-pilot")
        elif name == "hint":
            sub.add_argument("--url", default="http://127.0.0.1:8765")
        elif name == "interrupt":
            sub.add_argument("--reason", required=True)
        elif name == "stop":
            sub.add_argument("--reason", choices=("unsolved", "cap", "withdrawn"), default="unsolved")
        elif name == "note":
            annotations = sub.add_mutually_exclusive_group(required=True)
            annotations.add_argument("--command")
            annotations.add_argument("--tool")
    args = parser.parse_args(argv)
    try:
        if args.action == "create":
            create_session(args.session, sequence=args.sequence, cap_seconds=args.cap_seconds, participant=args.participant)
            print("Study prepared. No human timer has started.")
            print("When ready: python -m benchmarks.triage_study start" + (f' --session "{args.session}"' if args.session else ""))
        elif args.action == "status":
            print(json.dumps(status(args.session), indent=2, ensure_ascii=False))
        elif args.action == "hint":
            report = asyncio.run(request_hint(args.session, url=args.url))
            from decilist.triage import format_text
            print(format_text(report), end="")
        else:
            actions = {"start": start_trial, "verify": verify_trial, "resume": resume_trial}
            if args.action in actions:
                trial = actions[args.action](args.session)
            elif args.action == "interrupt":
                trial = interrupt_trial(args.session, reason=args.reason)
            elif args.action == "stop":
                trial = stop_trial(args.session, reason=args.reason)
            else:
                trial = record_command(args.session, command=args.command) if args.command else record_tool(args.session, tool=args.tool)
            directory = _session_directory(args.session, _lab_api())
            _print_trial(trial, directory)
        return 0
    except (StudyError, OSError, ValueError) as exc:
        print(f"Study: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
