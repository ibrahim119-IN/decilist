"""Eight reserved fresh-agent V5 repair sessions; never human productivity data.

The assisted arm receives exactly one live workflow call after clock start and
before recorded inspection or repair actions. Failure/abstention remains in the
assigned arm, after which the agent can use normal tools. Filesystem isolation
and the action ledger cannot prove that no unrecorded inspection occurred.
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
from pathlib import Path
from uuid import uuid4

from . import triage_agent_trial as clocks
from .run_triage_v5 import (_production_roots, production_hashes, verify_candidate,
                            workflow_bridge, _json_bytes, _provider, check_ready, _workflow_module, retain_context,
                            protected_checks_complete, retain_lab_audit)
from .triage_study import _digest, validate_loopback_url

TrialError = clocks.TrialError
CASE_IDS = ("r01", "r02", "r03", "r04")
TREATMENTS = ("normal_tools", "verified_assistant")
CAP_SECONDS = 900
EXPERIMENT = "triage-v5-repair"


def _lab_api():
    from . import triage_v5_lab
    return triage_v5_lab


def plan_trials() -> list[dict]:
    order = [("r01", "normal_tools"), ("r02", "verified_assistant"),
             ("r03", "normal_tools"), ("r04", "verified_assistant"),
             ("r01", "verified_assistant"), ("r02", "normal_tools"),
             ("r03", "verified_assistant"), ("r04", "normal_tools")]
    return [{"slot": slot, "case_id": case, "treatment": treatment}
            for slot, (case, treatment) in enumerate(order, 1)]


def _plan_hash():
    from .run_triage_v5 import fingerprint
    return fingerprint(plan_trials())


def _trial_case(case_id, lab):
    if case_id not in CASE_IDS or case_id not in lab.trial_case_ids() or lab.get_case_role(case_id) != "trial_regression":
        raise TrialError("Only reserved r01–r04 trial_regression cases may be prepared")


def _directory(value, lab):
    directory, root = Path(value).resolve(), (Path(lab.LAB_ROOT) / "agent-trials").resolve()
    if directory == root or not directory.is_relative_to(root):
        raise TrialError("V5 trial records must be children of the V5 agent-trials directory")
    return directory


def _source_hashes(case_dir, roots):
    return production_hashes(Path(case_dir), roots)


def _protected_hashes(case_dir, roots):
    case_dir = Path(case_dir)
    return {path.relative_to(case_dir).as_posix(): _digest(path) for path in sorted(case_dir.rglob("*"))
            if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts
            and not path.relative_to(case_dir).as_posix().startswith(tuple(root.rstrip("/") + "/" for root in roots))
            and not path.relative_to(case_dir).as_posix().startswith(".triage/runs/")}


def _read(directory: Path, lab):
    state = clocks._read(directory)
    _trial_case(state["case_id"], lab)
    if (state.get("experiment") != EXPERIMENT or state.get("plan_sha256") != _plan_hash()
            or state.get("assignment") not in plan_trials()):
        raise TrialError("The V5 experiment or fixed assignment plan changed")
    assignment = state["assignment"]
    if state.get("case_id") != assignment["case_id"] or state.get("treatment") != assignment["treatment"] or state.get("condition") != assignment["treatment"]:
        raise TrialError("The assigned case or treatment changed")
    manifest = json.loads((Path(state["case_dir"]) / ".triage/manifest.json").read_text(encoding="utf-8"))
    if manifest.get("case_id") != state["case_id"] or _production_roots(manifest) != state["editable_paths"]:
        raise TrialError("The reserved case or broad production roots changed")
    return state


def _locate(value, lab=None):
    lab = lab or _lab_api()
    directory = _directory(value, lab)
    return directory, lab, _read(directory, lab)


def prepare_trial(planned_assignment: dict, directory, *, agent_id: str,
                  candidate_freeze, config_path, endpoint="http://127.0.0.1:8792", provider_factory=None, lab=None):
    """Synchronous CLI/API wrapper; event-loop callers use prepare_trial_async."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(prepare_trial_async(planned_assignment, directory, agent_id=agent_id,
            candidate_freeze=candidate_freeze, config_path=config_path, endpoint=endpoint,
            provider_factory=provider_factory, lab=lab))
    raise TrialError("Use await prepare_trial_async inside an active event loop")


async def prepare_trial_async(planned_assignment: dict, directory, *, agent_id: str,
                  candidate_freeze, config_path, endpoint="http://127.0.0.1:8792", provider_factory=None, lab=None):
    # This exact gate precedes the reserved membership accessor and all case IO.
    candidate = verify_candidate(Path(candidate_freeze) if candidate_freeze else None,
                                 Path(config_path), qwen_url=endpoint)
    lab = lab or _lab_api()
    config = _workflow_module().load_model_config(Path(config_path))
    provider = (provider_factory or _provider)(config, endpoint)
    try:
        runtime_identity = await provider.ready()
        check_ready(runtime_identity, config, candidate)
        lab.verify_freeze()
    finally:
        await provider.aclose()
    if planned_assignment not in plan_trials() or not isinstance(agent_id, str) or not agent_id.strip():
        raise TrialError("Declare one fixed assignment and a fresh agent identity")
    case_id, treatment = planned_assignment["case_id"], planned_assignment["treatment"]
    _trial_case(case_id, lab)
    directory = _directory(directory, lab)
    if directory.exists():
        raise TrialError("The trial directory exists; previous evidence was not overwritten")
    directory.mkdir(parents=True)
    case_dir = directory / "case"
    manifest = lab.prepare_case(case_id, case_dir)
    roots = _production_roots(manifest)
    if (case_dir / ".git").exists() or manifest.get("case_id") != case_id:
        raise TrialError("The participant workspace is not a neutral reserved case")
    before = _source_hashes(case_dir, roots)
    # Evaluator audit material remains outside the participant case tree.
    for relative in before:
        target = directory / "initial-source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as handle:
            handle.write((case_dir / relative).read_bytes())
    run = lab.run_case(case_dir)
    retain_lab_audit(case_dir, run)
    if (run.get("exit_code") not in (1, 2) or not run.get("protected_files_ok")
            or not run.get("source_stable") or run.get("timed_out")
            or not protected_checks_complete(run)
            or before != _source_hashes(case_dir, roots) or before != run.get("editable_sha256_after")):
        raise TrialError("Preparation did not reproduce an intact stable protected failure")
    (directory / "original-failure.log").write_bytes(run["stdout"].encode())
    state = {"schema_version": 1, "experiment": EXPERIMENT, "run_type": "agent-run",
        "agent_id": agent_id, "agent_settings": dict(clocks.SETTINGS), "settings_source": "orchestrator declaration",
        "case_id": case_id, "condition": treatment, "treatment": treatment,
        "assignment": dict(planned_assignment), "plan_sha256": _plan_hash(),
        "case_dir": str(case_dir), "editable_paths": roots, "allowed_production_roots": roots,
        "manifest_sha256": _digest(case_dir / ".triage/manifest.json"),
        "candidate_manifest_path": str(Path(candidate_freeze).resolve()),
        "candidate_manifest_sha256": _digest(Path(candidate_freeze)), "candidate_identity": candidate,
        "preparation_runtime_identity": runtime_identity, "corpus_verified_before_preparation": True,
        "model_config_path": str(Path(config_path).resolve()), "model_config_sha256": _digest(Path(config_path)),
        "endpoint": validate_loopback_url(endpoint), "original_buggy_source_sha256": before,
        "protected_sha256": _protected_hashes(case_dir, roots),
        "baseline_run": {key: value for key, value in run.items() if key != "stdout"},
        "prepared_at": clocks._clock()["utc"], "cap_seconds": CAP_SECONDS, "status": "ready",
        "start_clock": None, "end_clock": None, "agent_elapsed_seconds": None, "timing_issue": None,
        "attempts": [], "hints": [], "verifications": [], "verified_repair": False,
        "unmeasured": {"human_elapsed_seconds": None, "human_minutes_saved": None, "token_usage": None, "cost_usd": None},
        "inspection_protocol": {"one_initial_hint_before_recorded_work": treatment == "verified_assistant",
                                "unrecorded_reads_verifiable": False}}
    clocks._write(directory, state)
    return state


def _active_with_cap(state):
    clocks._active(state)
    now = clocks._clock()
    elapsed, issue = clocks._elapsed(state["start_clock"], now)
    if issue or elapsed >= CAP_SECONDS:
        _finish(state, "invalid" if issue else "capped", now)
        raise TrialError("The trial clock or 900-second cap does not permit more work")
    return now


def _finish(state, outcome, now):
    state["end_clock"] = now
    state["agent_elapsed_seconds"], state["timing_issue"] = clocks._elapsed(state["start_clock"], now)
    state["status"] = "invalid" if state["timing_issue"] else outcome
    state["final_source_sha256"] = _source_hashes(Path(state["case_dir"]), state["editable_paths"])
    state["final_protected_sha256"] = _protected_hashes(Path(state["case_dir"]), state["editable_paths"])


def start_trial(directory, *, lab=None):
    directory, lab, _ = _locate(directory, lab)
    with clocks._lock(directory):
        state = _read(directory, lab)
        if state["status"] != "ready":
            raise TrialError("The trial clock cannot be restarted")
        state["start_clock"], state["status"] = clocks._clock(), "active"
        clocks._write(directory, state)
        return state


def _initial_hint_finished(state):
    if state["treatment"] == "verified_assistant":
        entries = state["hints"]
        if len(entries) != 1 or not entries[0].get("workflow_call_attempted") or entries[0].get("end_clock") is None:
            raise TrialError("The assisted arm requires one initial live workflow call before recorded inspection or repair")


def begin_attempt(directory, command, *, tool="shell", phase="inspect_or_repair", lab=None):
    if not command or not isinstance(command, (str, list)) or not isinstance(phase, str) or not phase:
        raise TrialError("Record the actual action and its phase")
    directory, lab, _ = _locate(directory, lab)
    with clocks._lock(directory):
        state = _read(directory, lab)
        try:
            now = _active_with_cap(state)
        except TrialError:
            clocks._write(directory, state)
            raise
        _initial_hint_finished(state)
        identifier = uuid4().hex
        state["attempts"].append({"id": identifier, "tool": tool, "phase": phase, "command": command,
            "origin": "agent-or-orchestrator-observed", "executed_by_runner": False,
            "start_clock": now, "end_clock": None, "elapsed_seconds": None,
            "exit_code": None, "observation": None})
        clocks._write(directory, state)
        return identifier


def end_attempt(directory, attempt_id, *, exit_code=None, observation=None, lab=None):
    directory, lab, _ = _locate(directory, lab)
    with clocks._lock(directory):
        state = _read(directory, lab)
        clocks._active(state)
        item = next((item for item in state["attempts"] if item["id"] == attempt_id), None)
        if item is None or item["end_clock"] is not None:
            raise TrialError("No unfinished recorded action has that identity")
        item["end_clock"] = clocks._clock()
        item["elapsed_seconds"], item["timing_issue"] = clocks._elapsed(item["start_clock"], item["end_clock"])
        item.update(exit_code=exit_code, observation=observation,
                    editable_source_sha256_after=_source_hashes(Path(state["case_dir"]), state["editable_paths"]))
        clocks._write(directory, state)
        return item


async def hint_trial(directory, *, callback=None, callback_version="triage-v5-workflow.1",
                     config_path=None, candidate_freeze=None, endpoint="http://127.0.0.1:8792", apply=False,
                     provider_factory=None, lab=None):
    endpoint = validate_loopback_url(endpoint)
    directory, lab, _ = _locate(directory, lab)
    with clocks._lock(directory):
        state = _read(directory, lab)
        try:
            before = _active_with_cap(state)
        except TrialError:
            clocks._write(directory, state)
            raise
        if state["treatment"] != "verified_assistant" or state["hints"] or state["attempts"] or state["verifications"]:
            raise TrialError("Only the assisted arm permits exactly one initial workflow call before recorded work")
        config_path = Path(config_path or state["model_config_path"])
        candidate_freeze = Path(candidate_freeze or state["candidate_manifest_path"])
        if (_digest(config_path) != state["model_config_sha256"] or _digest(candidate_freeze) != state["candidate_manifest_sha256"]
                or endpoint != state["endpoint"]):
            raise TrialError("The prepared candidate, configuration or endpoint changed")
        candidate = verify_candidate(candidate_freeze, config_path, qwen_url=endpoint)
        target = callback or workflow_bridge
        if not callable(target) or not callback_version.strip() or type(apply) is not bool:
            raise TrialError("Declare one live workflow callback, version and explicit apply boolean")
        source = inspect.getsourcefile(target)
        entry = {"version": callback_version, "callback": f"{target.__module__}:{target.__qualname__}",
            "callback_source_sha256_before": _digest(Path(source)) if source and Path(source).is_file() else None,
            "start_clock": before, "end_clock": None, "elapsed_seconds": None,
            "status": "pending", "callback_invoked": False, "apply_requested": apply,
            "workflow_call_attempted": False, "runtime_identity_valid": None,
            "report_path": None, "report_sha256": None, "evidence_run": None,
            "case_source_sha256_before": _source_hashes(Path(state["case_dir"]), state["editable_paths"]), "case_source_sha256_after": None,
            "case_source_stable": None, "permitted_verified_apply": False}
        state["hints"].append(entry)
        clocks._write(directory, state)
        report = None
        provider = None
        try:
            entry["workflow_call_attempted"] = True
            clocks._write(directory, state)
            config = _workflow_module().load_model_config(config_path)
            provider = (provider_factory or _provider)(config, endpoint)
            entry["runtime_identity"] = await provider.ready()
            try:
                check_ready(entry["runtime_identity"], config, candidate)
                entry["runtime_identity_valid"] = True
            except ValueError:
                entry["runtime_identity_valid"] = False
                raise
            case_dir = Path(state["case_dir"])
            current = _source_hashes(case_dir, state["editable_paths"])
            if current != state["original_buggy_source_sha256"]:
                raise TrialError("Source changed before the sole initial workflow call")
            observed = {**state["baseline_run"], "stdout": (directory / "original-failure.log").read_text(encoding="utf-8")}
            evidence = lab.prepare_case_evidence(case_dir, observed)
            if current != observed.get("editable_sha256_after"):
                raise TrialError("The initial saved production identity changed")
            entry["case_source_sha256_before"] = current
            manifest = json.loads((case_dir / ".triage/manifest.json").read_text(encoding="utf-8"))
            entry["callback_invoked"] = True
            entry["callback_invoked_at"] = clocks._clock()["utc"]
            clocks._write(directory, state)
            value = target(evidence, project=case_dir, manifest=manifest, work_dir=directory / "workflow",
                           config_path=config_path, endpoint=endpoint, provider=provider, apply=apply)
            if inspect.isawaitable(value):
                remaining = CAP_SECONDS - clocks._elapsed(state["start_clock"], clocks._clock())[0]
                report = await asyncio.wait_for(value, timeout=max(.001, remaining))
            else:
                report = value
            if not isinstance(report, dict):
                raise TrialError("The live workflow callback must return a structured report")
            if not isinstance(report.get("attempts", []), list) or len(report.get("attempts", [])) > 2:
                raise TrialError("V5 permits at most two generation attempts in the initial workflow")
            if report.get("context_path") and "actual_context" not in report:
                retain_context(report, work_dir=directory / "workflow")
            entry["status"] = report.get("state", report.get("status", "unverified"))
        except BaseException as exc:
            entry.update(status="interrupted" if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)) else "error",
                         error_type=type(exc).__name__, error=str(exc))
            report = {**(report if isinstance(report, dict) else {}),
                      "state_before_measurement_error": report.get("state") if isinstance(report, dict) else None,
                      "state": "unavailable", "error": {"type": type(exc).__name__, "message": str(exc)},
                      "callback_invoked": entry["callback_invoked"]}
            if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
                raise
        finally:
            if provider is not None:
                try:
                    await provider.aclose()
                except Exception as exc:
                    entry["provider_close_error"] = {"type": type(exc).__name__, "message": str(exc)}
            after = clocks._clock()
            entry["end_clock"] = after
            entry["elapsed_seconds"], entry["timing_issue"] = clocks._elapsed(before, after)
            entry["callback_source_sha256_after"] = _digest(Path(source)) if source and Path(source).is_file() else None
            entry["callback_source_stable"] = entry["callback_source_sha256_before"] == entry["callback_source_sha256_after"]
            entry["case_source_sha256_after"] = _source_hashes(Path(state["case_dir"]), state["editable_paths"])
            entry["case_source_stable"] = entry["case_source_sha256_before"] == entry["case_source_sha256_after"]
            applied = bool(report and report.get("state") == "hypothesis_supported_by_tests" and apply
                           and report.get("apply", {}).get("applied") is True)
            entry["permitted_verified_apply"] = applied
            protected = _protected_hashes(Path(state["case_dir"]), state["editable_paths"])
            entry["protected_unchanged"] = protected == state["protected_sha256"]
            entry["report_status"] = report.get("state", report.get("status")) if report else None
            if report is not None:
                folder = directory / "hints"
                folder.mkdir(exist_ok=True)
                path = folder / "hint-1.json"
                with path.open("xb") as handle:
                    handle.write(_json_bytes(report))
                entry["report_path"], entry["report_sha256"] = str(path), _digest(path)
            if (not entry["protected_unchanged"] or entry["runtime_identity_valid"] is False
                    or (not entry["case_source_stable"] and not applied)):
                _finish(state, "invalid", after)
            else:
                elapsed, issue = clocks._elapsed(state["start_clock"], after)
                if issue or elapsed >= CAP_SECONDS:
                    _finish(state, "invalid" if issue else "capped", after)
            clocks._write(directory, state)
        if applied and state["status"] == "active":
            verification = _check_state(directory, lab, state, verify=True)
            return {**report, "trial_verification": verification, "trial_status": state["status"]}
        return report


def check_trial(directory, *, verify=False, lab=None):
    directory, lab, _ = _locate(directory, lab)
    with clocks._lock(directory):
        state = _read(directory, lab)
        clocks._active(state)
        _initial_hint_finished(state)
        return _check_state(directory, lab, state, verify=verify)


def _check_state(directory, lab, state, *, verify):
    before = clocks._clock()
    try:
        if verify:
            validation = lab.validate_repair(Path(state["case_dir"]))
        else:
            run = lab.run_case(Path(state["case_dir"]))
            validation = {"passed": False, "integrity_ok": run["protected_files_ok"], "run": run}
        retain_lab_audit(Path(state["case_dir"]), validation.get("run") or {})
    except Exception as exc:
        validation = {"passed": False, "integrity_ok": False, "run": None,
                      "error": {"type": type(exc).__name__, "message": str(exc)}}
    after = clocks._clock()
    run = validation.get("run") or {}
    sources = _source_hashes(Path(state["case_dir"]), state["editable_paths"])
    protected_ok = _protected_hashes(Path(state["case_dir"]), state["editable_paths"]) == state["protected_sha256"]
    verified = bool(verify and validation.get("passed") is True and validation.get("integrity_ok") is True
        and run.get("exit_code") == 0 and run.get("protected_files_ok") is True
        and run.get("source_stable") is True and not run.get("timed_out")
        and run.get("editable_sha256_after") == sources and protected_ok and protected_checks_complete(run)
        and run.get("collection_sha256") == state["baseline_run"].get("collection_sha256"))
    record = {"kind": "verify" if verify else "check", "phase": "protected_verification",
              "start_clock": before, "end_clock": after, "elapsed_seconds": clocks._elapsed(before, after)[0],
              "validation": validation, "verified_endpoint": verified, "protected_unchanged": protected_ok}
    state["verifications"].append(record)
    elapsed, issue = clocks._elapsed(state["start_clock"], after)
    if not validation.get("integrity_ok") or not protected_ok or issue:
        _finish(state, "invalid", after)
    elif verified:
        state["verified_repair"] = True
        _finish(state, "solved" if elapsed <= CAP_SECONDS else "capped", after)
    elif elapsed >= CAP_SECONDS:
        _finish(state, "capped", after)
    record.update(trial_status=state["status"], agent_elapsed_seconds=state["agent_elapsed_seconds"] if state["status"] in clocks.ENDED else elapsed)
    clocks._write(directory, state)
    return record


def verify_trial(directory, *, lab=None):
    return check_trial(directory, verify=True, lab=lab)


def stop_trial(directory, *, reason="unsolved", lab=None):
    if reason not in {"unsolved", "withdrawn", "cap"}:
        raise TrialError("Choose unsolved, withdrawn or cap")
    directory, lab, _ = _locate(directory, lab)
    with clocks._lock(directory):
        state = _read(directory, lab)
        clocks._active(state)
        now = clocks._clock()
        elapsed, _ = clocks._elapsed(state["start_clock"], now)
        if reason == "cap" and elapsed is not None and elapsed < CAP_SECONDS:
            raise TrialError("The 900-second cap has not elapsed")
        _finish(state, "capped" if reason == "cap" or elapsed is not None and elapsed >= CAP_SECONDS else reason, now)
        clocks._write(directory, state)
        return state


def status(directory, *, lab=None):
    _, _, state = _locate(directory, lab)
    return {key: state.get(key) for key in ("experiment", "run_type", "agent_id", "agent_settings", "case_id",
        "treatment", "assignment", "status", "agent_elapsed_seconds", "timing_issue", "verified_repair", "unmeasured")} | {
        "tool_attempts": len(state["attempts"]), "hint_requests": len(state["hints"]),
        "live_hint_calls": sum(item.get("callback_invoked") is True for item in state["hints"]),
        "hint_statuses": [item["status"] for item in state["hints"]],
        "failed_verifications": sum(item["kind"] == "verify" and not item["verified_endpoint"] for item in state["verifications"]),
        "incomplete_attempts": sum(item["end_clock"] is None for item in state["attempts"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("prepare", "start", "hint", "attempt-start", "attempt-end", "check", "verify", "stop", "status"):
        sub = subparsers.add_parser(action)
        sub.add_argument("--trial", required=True)
        if action == "prepare":
            sub.add_argument("--slot", type=int, choices=range(1, 9), required=True)
            sub.add_argument("--agent-id", required=True)
            sub.add_argument("--candidate-freeze", required=True)
            sub.add_argument("--model-config", required=True)
            sub.add_argument("--url", default="http://127.0.0.1:8792")
        elif action == "hint":
            sub.add_argument("--apply", action="store_true")
            sub.add_argument("--url", default="http://127.0.0.1:8792")
            sub.add_argument("--format", choices=("text", "json"), default="text")
        elif action == "attempt-start":
            sub.add_argument("--command", required=True)
            sub.add_argument("--tool", default="shell")
            sub.add_argument("--phase", default="inspect_or_repair")
        elif action == "attempt-end":
            sub.add_argument("--id", required=True)
            sub.add_argument("--exit-code", type=int)
            sub.add_argument("--observation")
        elif action == "stop":
            sub.add_argument("--reason", choices=("unsolved", "withdrawn", "cap"), default="unsolved")
    args = parser.parse_args(argv)
    try:
        if args.action == "prepare":
            result = prepare_trial(plan_trials()[args.slot - 1], args.trial, agent_id=args.agent_id,
                candidate_freeze=args.candidate_freeze, config_path=args.model_config, endpoint=args.url)
        elif args.action == "hint":
            result = asyncio.run(hint_trial(args.trial, apply=args.apply, endpoint=args.url))
        elif args.action == "attempt-start":
            result = {"attempt_id": begin_attempt(args.trial, args.command, tool=args.tool, phase=args.phase)}
        elif args.action == "attempt-end":
            result = end_attempt(args.trial, args.id, exit_code=args.exit_code, observation=args.observation)
        elif args.action == "stop":
            result = stop_trial(args.trial, reason=args.reason)
        else:
            result = {"start": start_trial, "check": check_trial, "verify": verify_trial, "status": status}[args.action](args.trial)
        if args.action == "hint" and args.format == "text":
            print(_workflow_module_text(result), end="")
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        return 0
    except (OSError, ValueError) as exc:
        print("V5 agent trial: " + str(exc))
        return 2


def _workflow_module_text(result):
    """Use the product presentation, retaining useful unverified hypotheses."""
    from decilist.triage_v5 import format_text
    prepared = dict(result) if isinstance(result, dict) else {}
    if isinstance(prepared.get("error"), dict) and not prepared.get("errors"):
        prepared["errors"] = [prepared["error"]]
    attempts = prepared.get("attempts")
    if isinstance(attempts, list) and attempts and isinstance(attempts[-1], dict):
        final = attempts[-1]
        generated = (final.get("generation") or {}).get("result")
        if not final.get("hypothesis") and isinstance(generated, dict):
            prepared["attempts"] = [*attempts[:-1], {**final, "hypothesis": generated}]
    text = format_text(prepared)
    verification = prepared.get("trial_verification")
    if isinstance(verification, dict):
        text += "Protected verification: " + ("passed" if verification.get("verified_endpoint") is True else "not passed") + "\n"
    return text

if __name__ == "__main__":
    raise SystemExit(main())
