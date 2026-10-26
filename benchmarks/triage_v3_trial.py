"""Eight predeclared agent-run repair trials; never a human productivity study.

Fix hint presentation before live sessions: planned workers use --format text.
Presentation changes only stdout; complete JSON hint evidence remains preserved.
Creation hashes detect report drift against a mutable ledger, not an adversarial
security seal; case access remains an instruction-based filesystem protocol.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import inspect
import json
from pathlib import Path

from . import triage_agent_trial as v2
from .triage_study import _digest, _editable_paths, validate_loopback_url

TrialError = v2.TrialError
CASE_IDS = ("r01", "r02")
TREATMENTS = ("normal_tools", "qwen_route", "qwen_action", "qwen_diagnostic")
CAP_SECONDS = 900


def _lab_api():
    from benchmarks import triage_v3_lab
    return triage_v3_lab


def plan_trials() -> list[dict]:
    """Fixed before answers: each treatment gets both cases, in mirrored order."""
    order = [("r01", "normal_tools"), ("r02", "qwen_route"),
             ("r01", "qwen_action"), ("r02", "qwen_diagnostic"),
             ("r01", "qwen_diagnostic"), ("r02", "qwen_action"),
             ("r01", "qwen_route"), ("r02", "normal_tools")]
    return [{"slot": index, "case_id": case, "treatment": treatment}
            for index, (case, treatment) in enumerate(order, start=1)]


def _plan_hash() -> str:
    return hashlib.sha256(json.dumps(plan_trials(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _trial_case(case_id: str, lab) -> None:
    # These neutral accessors deliberately expose neither gold labels nor repairs.
    if case_id not in CASE_IDS or case_id not in lab.trial_case_ids() or lab.get_case_role(case_id) != "trial_regression":
        raise TrialError("Only the reserved r01/r02 trial_regression cases may be prepared.")


def _directory(value, lab) -> Path:
    directory = Path(value).resolve()
    root = (Path(lab.LAB_ROOT) / "agent-trials").resolve()
    if directory == root or not directory.is_relative_to(root):
        raise TrialError("V3 records must be children of work/triage-v3-lab/agent-trials.")
    return directory


def _read(directory: Path, lab) -> dict:
    state = v2._read(directory)
    _trial_case(state["case_id"], lab)
    if state.get("experiment") != "triage-v3-repair" or state.get("plan_sha256") != _plan_hash():
        raise TrialError("The recorded V3 experiment/assignment plan changed.")
    if state.get("assignment") not in plan_trials() or state.get("treatment") != state["assignment"]["treatment"]:
        raise TrialError("The predeclared treatment changed.")
    if state["case_id"] != state["assignment"]["case_id"] or state["condition"] != state["treatment"]:
        raise TrialError("The case/condition differs from its assigned slot.")
    manifest = json.loads((Path(state["case_dir"]) / ".triage/manifest.json").read_text(encoding="utf-8"))
    if manifest["case_id"] != state["case_id"] or _editable_paths(manifest) != state["editable_paths"]:
        raise TrialError("The reserved case or production allowlist changed.")
    return state


def _locate(value, lab=None):
    lab = lab or _lab_api()
    directory = _directory(value, lab)
    return directory, lab, _read(directory, lab)


def prepare_trial(planned_assignment: dict, directory, *, agent_id: str, lab=None) -> dict:
    """Materialize a neutral case and original failure; do not start its clock."""
    lab = lab or _lab_api()
    if planned_assignment not in plan_trials() or not agent_id.strip():
        raise TrialError("Declare one frozen plan slot and an agent identity.")
    case_id, treatment = planned_assignment["case_id"], planned_assignment["treatment"]
    _trial_case(case_id, lab)
    directory = _directory(directory, lab)
    if directory.exists():
        raise TrialError("The trial directory exists; no files were overwritten.")
    directory.mkdir(parents=True)
    case_dir = directory / "case"
    manifest = lab.prepare_case(case_id, case_dir)
    editable = _editable_paths(manifest)
    if (case_dir / ".git").exists() or manifest["case_id"] != case_id:
        raise TrialError("The participant workspace is not a neutral reserved case.")
    before = v2._source_hashes(case_dir, editable)
    run = lab.run_case(case_dir)
    if run["exit_code"] != 1 or not run["protected_files_ok"] or not run["source_stable"]:
        raise TrialError("Preparation did not reproduce an intact stable failure.")
    if before != v2._source_hashes(case_dir, editable):
        raise TrialError("Production changed while the original failure was prepared.")
    (directory / "original-failure.log").write_bytes(run["stdout"].encode("utf-8"))
    state = {
        "schema_version": 1, "experiment": "triage-v3-repair", "run_type": "agent-run",
        "agent_id": agent_id, "agent_settings": dict(v2.SETTINGS),
        "settings_source": "orchestrator declaration", "case_id": case_id,
        "condition": treatment, "treatment": treatment, "assignment": dict(planned_assignment),
        "plan_sha256": _plan_hash(), "case_dir": str(case_dir), "project": manifest.get("project"),
        "editable_paths": editable, "manifest_sha256": _digest(case_dir / ".triage/manifest.json"),
        "original_buggy_source_sha256": before,
        "baseline_run": {key: value for key, value in run.items() if key != "stdout"},
        "prepared_at": v2._clock()["utc"], "cap_seconds": CAP_SECONDS, "status": "ready",
        "start_clock": None, "end_clock": None, "agent_elapsed_seconds": None,
        "timing_issue": None, "attempts": [], "hints": [], "verifications": [],
        "verified_repair": False,
        "unmeasured": {"human_elapsed_seconds": None, "human_minutes_saved": None,
                       "token_usage": None, "cost_usd": None},
    }
    v2._write(directory, state)
    return state


def start_trial(directory, *, lab=None):
    directory, lab, _ = _locate(directory, lab)
    return v2.start_trial(directory, lab=lab)


def begin_attempt(directory, command, *, tool="shell", lab=None):
    directory, lab, _ = _locate(directory, lab)
    return v2.begin_attempt(directory, command, tool=tool, lab=lab)


def end_attempt(directory, attempt_id, *, exit_code=None, observation=None, lab=None):
    directory, lab, _ = _locate(directory, lab)
    return v2.end_attempt(directory, attempt_id, exit_code=exit_code, observation=observation, lab=lab)


def check_trial(directory, *, verify=False, lab=None):
    directory, lab, state = _locate(directory, lab)
    if verify and state["treatment"] != "normal_tools" and not any(item.get("callback_invoked") for item in state["hints"]):
        raise TrialError("The assigned hint treatment requires a live callback attempt before verification.")
    return v2.check_trial(directory, verify=verify, lab=lab)


def verify_trial(directory, *, lab=None):
    return check_trial(directory, verify=True, lab=lab)


def stop_trial(directory, *, reason="unsolved", lab=None):
    directory, lab, _ = _locate(directory, lab)
    return v2.stop_trial(directory, reason=reason, lab=lab)


def status(directory, *, lab=None):
    directory, lab, state = _locate(directory, lab)
    report = v2.status(directory, lab=lab)
    report.update(experiment=state["experiment"], treatment=state["treatment"],
                  slot=state["assignment"]["slot"], plan_sha256=state["plan_sha256"],
                  live_hint_calls=sum(item.get("callback_invoked", False) for item in state["hints"]),
                  hint_statuses=[item["status"] for item in state["hints"]])
    return report


async def hint_trial(directory, *, callback, callback_version: str, treatment: str,
                     endpoint="http://127.0.0.1:8792", fresh_run=False, lab=None):
    """Call an explicit live bridge inside the clock; no cached hint-file input exists."""
    endpoint = validate_loopback_url(endpoint)
    if not callable(callback) or not callback_version.strip() or treatment not in TREATMENTS[1:]:
        raise TrialError("Declare a live callback, version, and one Qwen treatment.")
    directory, lab, _ = _locate(directory, lab)
    with v2._lock(directory):
        state = _read(directory, lab)
        v2._active(state)
        if treatment != state["treatment"]:
            raise TrialError("The requested hint treatment does not match the trial ledger.")
        before = v2._clock()
        elapsed, issue = v2._elapsed(state["start_clock"], before)
        if issue or elapsed >= CAP_SECONDS:
            v2._finish(state, "invalid" if issue else "capped", before)
            v2._write(directory, state)
            raise TrialError("The trial clock/cap does not permit a hint.")
        source = inspect.getsourcefile(callback)
        entry = {
            "version": callback_version, "treatment": treatment, "endpoint": endpoint,
            "callback": f"{callback.__module__}:{callback.__qualname__}",
            "callback_source_sha256_before": _digest(Path(source)) if source and Path(source).is_file() else None,
            "callback_source_sha256_after": None, "callback_invoked": False,
            "start_clock": before, "end_clock": None, "elapsed_seconds": None,
            "status": "pending", "evidence_run": None, "fresh_run_requested": fresh_run,
            "report_path": None, "report_sha256": None, "case_source_sha256_before": None,
            "case_source_sha256_after": None, "case_source_stable": None,
        }
        state["hints"].append(entry)
        v2._write(directory, state)
        try:
            case_dir = Path(state["case_dir"])
            current = v2._source_hashes(case_dir, state["editable_paths"])
            if current != state["original_buggy_source_sha256"] and not fresh_run:
                raise TrialError("Production changed; request an explicit fresh protected run before a live hint.")
            if fresh_run:
                observed = lab.run_case(case_dir)
                entry["evidence_run"] = observed
                if not observed["protected_files_ok"] or not observed["source_stable"] or observed["exit_code"] in (0, None):
                    raise TrialError("Fresh evidence is passing, unstable, or invalid; no failure hint was requested.")
            else:
                observed = {**state["baseline_run"], "stdout": (directory / "original-failure.log").read_text(encoding="utf-8")}
            evidence = lab.prepare_case_evidence(case_dir, observed)
            entry["case_source_sha256_before"] = v2._source_hashes(case_dir, state["editable_paths"])
            if evidence.get("source_context_omitted") or entry["case_source_sha256_before"] != observed.get("editable_sha256_after"):
                raise TrialError("The failure and current source differ; fresh protected evidence is required.")
            entry["callback_invoked"] = True
            entry["callback_invoked_at"] = v2._clock()["utc"]
            v2._write(directory, state)
            result = callback(evidence, project=case_dir, treatment=treatment, endpoint=endpoint)
            report = await result if inspect.isawaitable(result) else result
            if not isinstance(report, dict):
                raise TrialError("The live bridge must return a structured report.")
            folder = directory / "hints"
            folder.mkdir(exist_ok=True)
            target = folder / f"hint-{len(state['hints'])}.json"
            with target.open("x", encoding="utf-8") as output:
                json.dump(report, output, indent=2, ensure_ascii=False, allow_nan=False)
            report_sha256 = _digest(target)
            entry.update(status=report.get("status"), report_path=str(target), report_sha256=report_sha256,
                         report_source=report.get("source"), model=report.get("model"),
                         runtime=report.get("runtime"), provenance=report.get("provenance"),
                         assistant_version=report.get("assistant_version"),
                         specification_sha256=report.get("specification_sha256"))
            return report
        except BaseException as exc:
            entry["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "error"
            entry["error_type"] = type(exc).__name__
            raise
        finally:
            after = v2._clock()
            entry["end_clock"] = after
            entry["elapsed_seconds"], entry["timing_issue"] = v2._elapsed(before, after)
            entry["callback_source_sha256_after"] = _digest(Path(source)) if source and Path(source).is_file() else None
            entry["callback_source_stable"] = entry["callback_source_sha256_before"] == entry["callback_source_sha256_after"]
            entry["case_source_sha256_after"] = v2._source_hashes(Path(state["case_dir"]), state["editable_paths"])
            entry["case_source_stable"] = entry["case_source_sha256_before"] == entry["case_source_sha256_after"] if entry["case_source_sha256_before"] is not None else None
            v2._write(directory, state)


def _callback(value):
    module, separator, name = value.partition(":")
    if not separator or not module.startswith("decilist."):
        raise TrialError("Select an explicit trusted decilist.module:function bridge.")
    target = getattr(importlib.import_module(module), name)
    if not callable(target):
        raise TrialError("The callback entry point is not callable.")
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("prepare", "status", "start", "attempt-start", "attempt-end", "check", "verify", "stop", "hint"):
        sub = subparsers.add_parser(action)
        sub.add_argument("--trial", required=True)
        if action == "prepare":
            sub.add_argument("--slot", type=int, choices=range(1, 9), required=True)
            sub.add_argument("--agent-id", required=True)
        elif action == "attempt-start":
            sub.add_argument("--command", required=True)
            sub.add_argument("--tool", default="shell")
        elif action == "attempt-end":
            sub.add_argument("--id", required=True)
            sub.add_argument("--exit-code", type=int)
            sub.add_argument("--observation")
        elif action == "stop":
            sub.add_argument("--reason", choices=("unsolved", "withdrawn", "cap"), default="unsolved")
        elif action == "hint":
            sub.add_argument("--callback", required=True)
            sub.add_argument("--callback-version", required=True)
            sub.add_argument("--treatment", choices=TREATMENTS[1:], required=True)
            sub.add_argument("--url", default="http://127.0.0.1:8792")
            sub.add_argument("--fresh-run", action="store_true")
            sub.add_argument("--format", choices=("text", "json"), default="json")
    args = parser.parse_args(argv)
    try:
        if args.action == "prepare":
            result = prepare_trial(plan_trials()[args.slot-1], args.trial, agent_id=args.agent_id)
        elif args.action == "hint":
            validate_loopback_url(args.url)
            result = asyncio.run(hint_trial(args.trial, callback=_callback(args.callback), callback_version=args.callback_version,
                                           treatment=args.treatment, endpoint=args.url, fresh_run=args.fresh_run))
        elif args.action == "attempt-start":
            result = {"attempt_id": begin_attempt(args.trial, args.command, tool=args.tool)}
        elif args.action == "attempt-end":
            result = end_attempt(args.trial, args.id, exit_code=args.exit_code, observation=args.observation)
        elif args.action == "stop":
            result = stop_trial(args.trial, reason=args.reason)
        else:
            result = {"status": status, "start": start_trial, "check": check_trial, "verify": verify_trial}[args.action](args.trial)
        if args.action == "hint" and args.format == "text":
            from decilist.triage_v3 import format_text
            print(format_text(result), end="")
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        return 0
    except (OSError, ValueError, AttributeError) as exc:
        print(f"V3 agent trial: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
