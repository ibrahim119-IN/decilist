"""Post-trial protected verification and all-slot export; never a human speedup claim."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from time import perf_counter

from decilist.triage_evidence import prepare_evidence
from . import triage_v3_trial as trial
from . import triage_agent_trial as clocks
from .run_triage_v3 import _public as shared_public
from .triage_study import _digest, _editable_paths

TRACE_KEYS = {"raw_logits", "token_logits", "vocabulary_mask", "raw_vocab_mask",
              "raw_protocol_trace", "private_protocol_trace", "full_vocabulary_scores"}
LIMITATIONS = [
    "Eight independent agent sessions on two reserved cases, with two cases per treatment; not a representative developer sample.",
    "Agent elapsed time includes tool/model waiting and orchestration overhead; it is not human time saved.",
    "Same-case differences are descriptive signed seconds, not significance tests or a general speedup estimate.",
    "Fresh protected root verification is outside the timed agent run; it never repairs or edits participant source.",
    "Local ledgers are not a tamper-proof audit; current verification and source/hash comparisons are reported separately.",
    "Hint creation hashes detect report drift against mutable local records; instruction-based filesystem isolation is not a security seal.",
]


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _strict_json(path: Path):
    def reject(value):
        raise ValueError("Non-finite JSON value: " + value)
    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("Non-finite JSON number")
        return result
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject, parse_float=finite_float)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _safe_case(directory: Path, state: dict, lab):
    root = (Path(lab.LAB_ROOT) / "agent-trials").resolve()
    if directory == root or not directory.is_relative_to(root):
        raise ValueError("trial_directory_outside_agent_trials")
    case_dir = (directory / "case").resolve()
    if not case_dir.is_relative_to(directory) or state.get("case_dir") != str(case_dir):
        raise ValueError("case_directory_changed_or_escaped")
    manifest = _strict_json(case_dir / ".triage/manifest.json")
    case_id = manifest.get("case_id")
    if case_id not in trial.CASE_IDS or case_id not in lab.trial_case_ids() or lab.get_case_role(case_id) != "trial_regression":
        raise ValueError("case_is_not_reserved_trial_regression")
    paths = _editable_paths(manifest)
    for path in paths:
        if not (case_dir / path).resolve().is_relative_to(case_dir):
            raise ValueError("production_source_path_escaped")
    return case_dir, manifest, paths


def _hints(directory: Path, state: dict, issues: list[str]):
    result = []
    entries = state.get("hints", [])
    if not isinstance(entries, list):
        issues.append("invalid_hint_list")
        return result
    for entry in entries:
        if not isinstance(entry, dict):
            issues.append("invalid_hint_record")
            continue
        report, report_sha = None, None
        if entry.get("report_path"):
            creation_hash = entry.get("report_sha256")
            creation_known = isinstance(creation_hash, str) and len(creation_hash) == 64 and all(char in "0123456789abcdef" for char in creation_hash)
            if not creation_known:
                issues.append("hint_report_creation_hash_missing")
            try:
                path = Path(entry["report_path"]).resolve()
            except (TypeError, ValueError):
                issues.append("hint_report_path_invalid")
                result.append({"entry": entry, "report": None, "current_report_sha256": None})
                continue
            if not path.is_relative_to((directory / "hints").resolve()):
                issues.append("hint_report_path_escaped")
            else:
                try:
                    report_sha = _digest(path)
                    if creation_known and report_sha != creation_hash:
                        issues.append("hint_report_hash_changed")
                    report = _strict_json(path)
                    if not isinstance(report, dict):
                        raise ValueError("hint report must be an object")
                    if report.get("status") != entry.get("status"):
                        issues.append("hint_report_status_changed")
                except (OSError, ValueError, TypeError):
                    issues.append("hint_report_missing_or_invalid")
        if entry.get("callback_source_stable") is False:
            issues.append("hint_callback_changed_during_request")
        if entry.get("case_source_stable") is False:
            issues.append("hint_context_changed_during_request")
        if entry.get("callback_invoked") and state.get("end_clock") is not None:
            try:
                first, problem = clocks._elapsed(state["start_clock"], entry["start_clock"])
                last, tail_problem = clocks._elapsed(entry["end_clock"], state["end_clock"])
                if problem or tail_problem or first is None or last is None:
                    issues.append("live_hint_outside_agent_clock")
            except (KeyError, TypeError, ValueError):
                issues.append("live_hint_clock_missing_or_invalid")
        result.append({"entry": entry, "report": report,
                       "current_report_sha256": report_sha,
                       "report_hash_matches_creation": report_sha is not None and report_sha == entry.get("report_sha256"),
                       "hash_observed_phase": "post_trial_export"})
    return result


def _observe(directory: Path, lab):
    observation = {"directory": str(directory), "issues": [], "declared_slot": None,
                   "recorded_outcome": None, "root_verification": None,
                   "root_verification_attempted": False,
                   "hints": [], "protocol_valid": False, "timed_success_confirmed": False,
                   "timing_eligible": False, "agent_elapsed_seconds": None}
    private = {"directory": str(directory), "state": None, "hints": []}
    issues = observation["issues"]
    root = (Path(lab.LAB_ROOT) / "agent-trials").resolve()
    if directory == root or not directory.is_relative_to(root):
        issues.append("trial_directory_outside_agent_trials")
        return observation, private
    target = directory / "trial.json"
    try:
        private["raw_ledger_text"] = target.read_text(encoding="utf-8")
        private["current_ledger_sha256"] = _digest(target)
        state = _strict_json(target)
        if not isinstance(state, dict):
            raise ValueError("ledger must be an object")
        private["state"] = state
    except (OSError, ValueError):
        issues.append("missing_or_invalid_ledger")
        return observation, private
    assignment = state.get("assignment")
    slot = assignment.get("slot") if isinstance(assignment, dict) else None
    if type(slot) is int and 1 <= slot <= len(trial.plan_trials()):
        observation["declared_slot"] = slot
        expected = trial.plan_trials()[slot-1]
        if assignment != expected:
            issues.append("assignment_does_not_match_predeclared_slot")
        if state.get("case_id") != expected["case_id"] or state.get("treatment") != expected["treatment"] or state.get("condition") != expected["treatment"]:
            issues.append("case_or_treatment_changed")
    else:
        issues.append("missing_or_invalid_assignment_slot")
    if state.get("run_type") != "agent-run" or state.get("experiment") != "triage-v3-repair":
        issues.append("invalid_experiment_identity")
    if state.get("agent_settings") != clocks.SETTINGS or state.get("plan_sha256") != trial._plan_hash():
        issues.append("agent_settings_or_plan_changed")
    if state.get("cap_seconds") != trial.CAP_SECONDS:
        issues.append("time_cap_changed")
    outcome = state.get("status")
    observation.update(recorded_outcome=outcome, agent_id=state.get("agent_id"),
                       agent_settings=state.get("agent_settings"),
                       recorded_elapsed_seconds=state.get("agent_elapsed_seconds"),
                       attempts=state.get("attempts", []), timed_verifications=state.get("verifications", []),
                       original_buggy_source_sha256=state.get("original_buggy_source_sha256"),
                       final_source_sha256=state.get("final_source_sha256"))
    observation["hints"] = _hints(directory, state, issues)
    private["hints"] = observation["hints"]
    if outcome not in clocks.ENDED:
        issues.append("trial_not_finished")
        return observation, private
    try:
        elapsed, timing_issue = clocks._elapsed(state["start_clock"], state["end_clock"])
        if timing_issue or not _number(state.get("agent_elapsed_seconds")) or elapsed is None or abs(elapsed-state["agent_elapsed_seconds"]) > 1e-6:
            issues.append("agent_clock_invalid_or_recorded_elapsed_disagrees")
        else:
            observation["agent_elapsed_seconds"] = elapsed
    except (KeyError, TypeError, ValueError):
        issues.append("agent_clock_missing_or_invalid")
    if outcome == "invalid":
        issues.append("trial_recorded_invalid")
    if state.get("treatment") == "normal_tools":
        if any(item["entry"].get("callback_invoked") for item in observation["hints"]):
            issues.append("normal_tools_received_a_hint")
    elif not any(item["entry"].get("callback_invoked") for item in observation["hints"]):
        issues.append("assigned_hint_treatment_was_not_called_live")
    try:
        case_dir, manifest, paths = _safe_case(directory, state, lab)
        if manifest["case_id"] != state.get("case_id") or paths != state.get("editable_paths"):
            issues.append("case_manifest_or_allowlist_changed")
        if _digest(case_dir / ".triage/manifest.json") != state.get("manifest_sha256"):
            issues.append("protected_manifest_hash_changed")
        if _digest(directory / "original-failure.log") != (state.get("baseline_run") or {}).get("run_sha256"):
            issues.append("original_failure_log_changed")
        before = clocks._source_hashes(case_dir, paths)
        terminal_hashes = state.get("final_source_sha256")
        terminal_matches = isinstance(terminal_hashes, dict) and set(terminal_hashes) == set(paths) and before == terminal_hashes
        observation.update(current_source_sha256_before_root=before, source_matches_timed_terminal=terminal_matches)
        if not terminal_matches:
            issues.append("source_changed_after_timed_terminal_or_hashes_missing")
        timed = [item for item in state.get("verifications", []) if isinstance(item, dict) and item.get("kind") == "verify" and item.get("verified_endpoint") is True]
        last = timed[-1] if timed else None
        timed_hashes = ((last or {}).get("validation", {}).get("run") or {}).get("editable_sha256_after")
        timed_matches = last is not None and timed_hashes == before and last.get("end_clock") == state.get("end_clock")
        observation.update(timed_verification_source_sha256=timed_hashes, source_matches_timed_verification=timed_matches)
        if outcome == "solved" and (not timed_matches or state.get("verified_repair") is not True):
            issues.append("solved_record_missing_matching_timed_verification")
        start = _utc()
        tick = perf_counter()
        observation["root_verification_attempted"] = True
        try:
            validation = lab.validate_repair(case_dir)
        except Exception as exc:
            observation["root_verification"] = {"phase": "post_trial_root_verification", "outside_agent_clock": True,
                                                 "started_at": start, "finished_at": _utc(),
                                                 "elapsed_seconds": perf_counter()-tick, "validation": None,
                                                 "repair_reproduced": False, "error_type": type(exc).__name__}
            observation["current_source_sha256_after_root"] = clocks._source_hashes(case_dir, paths)
            raise
        elapsed_root = perf_counter()-tick
        after = clocks._source_hashes(case_dir, paths)
        run = validation.get("run") or {}
        reproduced = bool(validation.get("passed") is True and validation.get("integrity_ok") is True
                          and run.get("exit_code") == 0 and run.get("protected_files_ok") is True
                          and run.get("source_stable") is True and not run.get("timed_out") and before == after)
        observation["root_verification"] = {"phase": "post_trial_root_verification", "outside_agent_clock": True,
                                             "started_at": start, "finished_at": _utc(),
                                             "elapsed_seconds": elapsed_root, "validation": validation,
                                             "repair_reproduced": reproduced}
        observation["current_source_sha256_after_root"] = after
        if before != after:
            issues.append("source_changed_during_root_verification")
        if validation.get("integrity_ok") is not True or run.get("protected_files_ok") is not True:
            issues.append("fresh_root_integrity_failed")
        if outcome == "solved" and not reproduced:
            issues.append("timed_success_did_not_reproduce")
        if outcome == "solved" and observation["agent_elapsed_seconds"] is not None and observation["agent_elapsed_seconds"] > trial.CAP_SECONDS:
            issues.append("solved_record_exceeds_cap")
    except Exception as exc:
        observation["root_verification_error"] = {"type": type(exc).__name__, "message": str(exc)}
        issues.append("root_verification_or_source_inspection_failed")
    observation["issues"] = sorted(set(issues))
    observation["protocol_valid"] = not issues
    observation["timing_eligible"] = not issues and observation["agent_elapsed_seconds"] is not None
    observation["timed_success_confirmed"] = bool(not issues and outcome == "solved" and observation.get("root_verification", {}).get("repair_reproduced"))
    return observation, private


def _reported_model(report):
    if report.get("model") is not None:
        return report["model"]
    runtime = report.get("runtime")
    if isinstance(runtime, dict) and runtime.get("model") is not None:
        return runtime["model"]
    decision = report.get("decision")
    return decision.get("model") if isinstance(decision, dict) else None


def _summaries(slots):
    by_treatment = {}
    for treatment in trial.TREATMENTS:
        rows = [row for row in slots if row["treatment"] == treatment]
        records = [record for row in rows for record in row["records"]]
        hints = [item for record in records for item in record["hints"]]
        model_reports = [item["report"] if isinstance(item["report"], dict) else {} for item in hints]
        durations = [item["entry"].get("elapsed_seconds") for item in hints]
        solved = [row["agent_elapsed_seconds"] for row in rows if row["timed_success_confirmed"]]
        by_treatment[treatment] = {
            "planned_cases": 2, "outcomes": dict(Counter(row["outcome"] for row in rows)),
            "confirmed_solved": len(solved),
            "median_seconds_confirmed_solved": median(solved) if solved else None,
            "cases": [{key: row[key] for key in ("slot", "case_id", "outcome", "recorded_outcome", "agent_elapsed_seconds", "timed_success_confirmed", "timing_eligible")} for row in rows],
            "hint_requests": len(hints), "hint_statuses": dict(Counter(str(item["entry"].get("status")) for item in hints)),
            "model_report_statuses": dict(Counter(str(report.get("status", item["entry"].get("status"))) for report, item in zip(model_reports, hints))),
            "reported_models": [_reported_model(report) for report in model_reports],
            "hint_total_seconds": sum(durations) if durations and all(_number(value) for value in durations) else None,
            "known_hint_seconds_sum": sum(value for value in durations if _number(value)),
            "hint_durations_unknown": sum(not _number(value) for value in durations),
        }
    differences = []
    for case_id in trial.CASE_IDS:
        normal = next(row for row in slots if row["case_id"] == case_id and row["treatment"] == "normal_tools")
        for treatment in trial.TREATMENTS[1:]:
            other = next(row for row in slots if row["case_id"] == case_id and row["treatment"] == treatment)
            eligible = normal["timed_success_confirmed"] and other["timed_success_confirmed"]
            differences.append({"case_id": case_id, "treatment": treatment,
                                "normal_tools_seconds": normal["agent_elapsed_seconds"],
                                "treatment_seconds": other["agent_elapsed_seconds"],
                                "difference_seconds_treatment_minus_normal": other["agent_elapsed_seconds"]-normal["agent_elapsed_seconds"] if eligible else None,
                                "eligible": eligible, "reason": None if eligible else "one_or_both_not_confirmed_solved"})
    return by_treatment, differences


def build_report(trial_directories, *, lab=None):
    """Rerun finished protected cases, retaining all expected and extra observations."""
    lab = lab or trial._lab_api()
    observations, private = [], []
    for value in trial_directories:
        observed, raw = _observe(Path(value).resolve(), lab)
        observations.append(observed)
        private.append(raw)
    slots = []
    for planned in trial.plan_trials():
        records = [row for row in observations if row["declared_slot"] == planned["slot"]]
        single = records[0] if len(records) == 1 else None
        if not records:
            outcome = "missing"
        elif len(records) > 1:
            outcome = "duplicate"
        else:
            outcome = single["recorded_outcome"] if single["protocol_valid"] else "invalid"
        slots.append({**planned, "record_count": len(records), "records": records,
                      "outcome": outcome, "recorded_outcome": single["recorded_outcome"] if single else None,
                      "agent_elapsed_seconds": single["agent_elapsed_seconds"] if single else None,
                      "timing_eligible": bool(single and single["timing_eligible"]),
                      "timed_success_confirmed": bool(single and single["timed_success_confirmed"])})
    summaries, differences = _summaries(slots)
    return {"schema_version": 1, "suite": "triage-v3-agent-repair-report", "run_type": "agent-run",
            "created_at": _utc(), "reporter_source_sha256": _digest(Path(__file__)),
            "plan_sha256": trial._plan_hash(), "expected_slots": 8,
            "observed_records": len(observations), "slots": slots,
            "unassigned_records": [row for row in observations if row["declared_slot"] is None],
            "complete_unique_plan": all(row["record_count"] == 1 for row in slots) and all(row["declared_slot"] is not None for row in observations),
            "per_treatment": summaries, "paired_same_case_differences": differences,
            "human_elapsed_seconds": None, "human_minutes_saved": None, "cost_usd": None,
            "limitations": list(LIMITATIONS), "private_full_records": private}


def _trim_traces(value):
    if isinstance(value, list):
        return [_trim_traces(item) for item in value]
    if isinstance(value, dict):
        output, omitted = {}, []
        for key, item in value.items():
            if key in TRACE_KEYS:
                encoded = json.dumps(item, sort_keys=True, ensure_ascii=False).encode()
                omitted.append({"field": key, "sha256": hashlib.sha256(encoded).hexdigest(), "bytes": len(encoded)})
            else:
                output[key] = _trim_traces(item)
        if omitted:
            output["public_trace_omissions"] = omitted
        return output
    return value


def _extra_paths(value):
    """Cover arbitrary POSIX roots and path-like keys beyond shared exporter patterns."""
    if isinstance(value, list):
        return [_extra_paths(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            clean_key = prepare_evidence(key)["log"]
            public_key = key if clean_key == key else "[PATH_KEY_" + hashlib.sha256(key.encode()).hexdigest() + "]"
            result[public_key] = _extra_paths(item)
        return result
    if isinstance(value, str):
        return prepare_evidence(value)["log"]
    return value


def public_report(report):
    visible = {key: value for key, value in report.items() if key != "private_full_records"}
    return _extra_paths(shared_public(_trim_traces(visible)))


def export_report(report, *, public_output, private_output):
    paths = [Path(public_output), Path(private_output)]
    if paths[0].resolve() == paths[1].resolve() or any(path.exists() for path in paths):
        raise ValueError("Use distinct new public/private filenames; existing evidence is never overwritten.")
    for raw in report.get("private_full_records", []):
        case = (Path(raw["directory"]) / "case").resolve()
        if any(path.resolve().is_relative_to(case) for path in paths):
            raise ValueError("Reports must remain outside every protected case directory.")
    for path, value in ((paths[1], report), (paths[0], public_report(report))):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, indent=2, ensure_ascii=False, allow_nan=False)
            output.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.exists() or args.private_output.exists():
            raise ValueError("Output already exists; choose new report filenames.")
        allowed = (Path(trial._lab_api().LAB_ROOT) / "agent-trials").resolve()
        selected = args.trials_root.resolve()
        if selected != allowed and not selected.is_relative_to(allowed):
            raise ValueError("The trials root must stay inside the V3 agent-trials directory.")
        directories = sorted(path.parent for path in args.trials_root.rglob("trial.json"))
        report = build_report(directories)
        export_report(report, public_output=args.output, private_output=args.private_output)
        print(json.dumps({"expected_slots": 8, "observed_records": report["observed_records"],
                          "outcomes": dict(Counter(row["outcome"] for row in report["slots"])),
                          "complete_unique_plan": report["complete_unique_plan"]}, indent=2))
        return 0 if report["complete_unique_plan"] and all(row["outcome"] != "invalid" for row in report["slots"]) else 1
    except (OSError, ValueError) as exc:
        print("Trial report: " + str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
