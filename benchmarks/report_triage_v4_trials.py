"""All-slot V4 export with a fresh protected root check outside agent clocks."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import difflib
from pathlib import Path
from statistics import median
from time import perf_counter

from . import triage_v4_trial as trial
from . import triage_agent_trial as clocks
from .report_triage_v3_trials import _strict_json, _number
from .run_triage_v4 import export_report, public_report, verify_candidate
from .triage_study import _digest

LIMITATIONS = [
    "Eight fresh agent sessions cover four reserved cases and two treatments; this is not a representative developer sample.",
    "Elapsed time includes tool/model waiting and orchestration; human time and human minutes saved are unmeasured.",
    "Same-case signed differences are descriptive, not significance tests or a general speedup estimate.",
    "The root protected check runs after the timed session and never repairs participant source.",
    "Local ledgers and creation hashes detect drift, not adversarial tampering.",
    "Recorded phase ordering cannot prove absence of unrecorded source or log inspection.",
    "An assisted failure or abstention remains in its assigned arm, even if later repaired with normal tools.",
    "Source citations are grounding evidence and receive no correctness credit; passing tests does not prove complete causality.",
]


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _hints(directory, state, issues):
    records = state.get("hints", [])
    if not isinstance(records, list):
        issues.append("invalid_hint_list")
        return []
    result = []
    for entry in records:
        if not isinstance(entry, dict):
            issues.append("invalid_hint_record")
            result.append({"entry": {"status": "invalid_record"}, "report": None})
            continue
        item = {"entry": entry, "report": None, "current_report_sha256": None,
                "report_hash_matches_creation": False, "hash_observed_phase": "post_trial_root_export"}
        try:
            path = Path(entry["report_path"]).resolve()
            if not path.is_relative_to((directory / "hints").resolve()):
                raise ValueError("hint path escaped")
            item["current_report_sha256"] = _digest(path)
            item["report_hash_matches_creation"] = item["current_report_sha256"] == entry.get("report_sha256")
            if not item["report_hash_matches_creation"]:
                issues.append("hint_report_hash_changed_or_creation_hash_missing")
            report = item["report"] = _strict_json(path)
            if not isinstance(report, dict) or report.get("state", report.get("status")) != entry.get("report_status"):
                issues.append("hint_report_state_changed_or_invalid")
        except (KeyError, OSError, TypeError, ValueError):
            issues.append("hint_report_missing_invalid_or_escaped")
        if entry.get("callback_source_stable") is not True:
            issues.append("hint_callback_source_changed_or_unverified")
        if entry.get("protected_unchanged") is not True:
            issues.append("hint_protected_files_changed_or_unverified")
        if entry.get("case_source_stable") is not True:
            report = item.get("report") or {}
            if not (entry.get("permitted_verified_apply") is True and entry.get("apply_requested") is True
                    and report.get("state") == "hypothesis_supported_by_tests"
                    and report.get("apply", {}).get("applied") is True):
                issues.append("hint_changed_source_without_verified_apply")
        result.append(item)
    return result


def _inside(start, end, outer_start, outer_end):
    try:
        first, problem = clocks._elapsed(outer_start, start)
        duration, middle = clocks._elapsed(start, end)
        last, tail = clocks._elapsed(end, outer_end)
        return not (problem or middle or tail) and all(value is not None for value in (first, duration, last))
    except (KeyError, TypeError, ValueError):
        return False


def _initial_hint_protocol(state, issues):
    entries, actions, checks = state.get("hints", []), state.get("attempts", []), state.get("verifications", [])
    assisted = state.get("treatment") == "verified_assistant"
    details = {"required_hint_records": int(assisted),
               "recorded_hint_records": len(entries) if isinstance(entries, list) else None,
               "recorded_task_attempts": len(actions) if isinstance(actions, list) else None,
               "order_checked": "recorded_actions_and_protected_phases_only", "unrecorded_reads_verifiable": False}
    if not isinstance(entries, list):
        return details
    if not assisted:
        if entries:
            issues.append("normal_tools_requires_zero_hint_records")
        hint = None
    else:
        if len(entries) != 1:
            issues.append("assisted_run_requires_exactly_one_initial_hint")
            return details
        hint = entries[0]
        if not isinstance(hint, dict) or hint.get("workflow_call_attempted") is not True:
            issues.append("initial_hint_not_called_live")
            return details
        if not _inside(hint.get("start_clock"), hint.get("end_clock"), state.get("start_clock"), state.get("end_clock")):
            issues.append("initial_hint_outside_agent_clock")
    if not isinstance(actions, list) or not isinstance(checks, list):
        issues.append("invalid_recorded_phase_list")
        return details
    for phase in actions + checks:
        if not isinstance(phase, dict) or not isinstance(phase.get("phase"), str) or not phase["phase"]:
            issues.append("recorded_phase_missing_or_invalid")
            continue
        if not _inside(phase.get("start_clock"), phase.get("end_clock"), state.get("start_clock"), state.get("end_clock")):
            issues.append("recorded_phase_outside_clock_or_incomplete")
        if hint:
            try:
                _, problem = clocks._elapsed(hint["end_clock"], phase["start_clock"])
            except (KeyError, TypeError, ValueError):
                problem = "unverifiable"
            if problem:
                issues.append("recorded_phase_precedes_initial_hint_or_order_unverifiable")
    return details


def _observe(directory: Path, lab):
    observed = {"directory": str(directory), "issues": [], "declared_slot": None,
                "recorded_outcome": None, "root_verification": None, "root_verification_attempted": False,
                "hints": [], "protocol_valid": False, "timing_eligible": False,
                "timed_success_confirmed": False, "agent_elapsed_seconds": None}
    raw = {"directory": str(directory), "state": None}
    issues = observed["issues"]
    root = (Path(lab.LAB_ROOT) / "agent-trials").resolve()
    if directory == root or not directory.is_relative_to(root):
        issues.append("trial_directory_outside_agent_trials")
        return observed, raw
    try:
        raw["raw_ledger_text"] = (directory / "trial.json").read_text(encoding="utf-8")
        raw["current_ledger_sha256"] = _digest(directory / "trial.json")
        state = _strict_json(directory / "trial.json")
        if not isinstance(state, dict):
            raise ValueError("ledger must be an object")
        raw["state"] = state
    except (OSError, ValueError):
        issues.append("missing_or_invalid_ledger")
        return observed, raw
    assignment = state.get("assignment")
    slot = assignment.get("slot") if isinstance(assignment, dict) else None
    if type(slot) is int and 1 <= slot <= 8:
        observed["declared_slot"] = slot
        expected = trial.plan_trials()[slot - 1]
        if (assignment != expected or state.get("case_id") != expected["case_id"]
                or state.get("treatment") != expected["treatment"] or state.get("condition") != expected["treatment"]):
            issues.append("assignment_case_or_treatment_changed")
    else:
        issues.append("missing_or_invalid_assignment_slot")
    if state.get("run_type") != "agent-run" or state.get("experiment") != trial.EXPERIMENT:
        issues.append("invalid_experiment_identity")
    if state.get("agent_settings") != clocks.SETTINGS or state.get("plan_sha256") != trial._plan_hash():
        issues.append("agent_settings_or_plan_changed")
    if state.get("cap_seconds") != trial.CAP_SECONDS:
        issues.append("time_cap_changed")
    observed.update(recorded_outcome=state.get("status"), agent_id=state.get("agent_id"),
                    agent_settings=state.get("agent_settings"), recorded_elapsed_seconds=state.get("agent_elapsed_seconds"),
                    attempts=state.get("attempts", []), timed_verifications=state.get("verifications", []))
    observed["hints"] = _hints(directory, state, issues)
    observed["initial_hint_protocol"] = _initial_hint_protocol(state, issues)
    outcome = state.get("status")
    if outcome not in clocks.ENDED:
        issues.append("trial_not_finished")
        return observed, raw
    try:
        elapsed, problem = clocks._elapsed(state["start_clock"], state["end_clock"])
        if problem or not _number(state.get("agent_elapsed_seconds")) or elapsed is None or abs(elapsed - state["agent_elapsed_seconds"]) > 1e-6:
            issues.append("agent_clock_invalid_or_elapsed_disagrees")
        else:
            observed["agent_elapsed_seconds"] = elapsed
    except (KeyError, TypeError, ValueError):
        issues.append("agent_clock_missing_or_invalid")
    if outcome == "invalid":
        issues.append("trial_recorded_invalid")
    try:
        config, candidate = Path(state["model_config_path"]), Path(state["candidate_manifest_path"])
        if _digest(config) != state.get("model_config_sha256") or _digest(candidate) != state.get("candidate_manifest_sha256"):
            raise ValueError("candidate/configuration hash changed")
        verified_candidate = verify_candidate(candidate, config, qwen_url=state["endpoint"])
        if verified_candidate != state.get("candidate_identity"):
            raise ValueError("recorded candidate identity changed")
    except (KeyError, OSError, TypeError, ValueError) as exc:
        issues.append("candidate_identity_drift_or_unverifiable")
        observed["candidate_error"] = {"type": type(exc).__name__, "message": str(exc)}
    try:
        case_dir = (directory / "case").resolve()
        if not case_dir.is_relative_to(directory) or state.get("case_dir") != str(case_dir):
            raise ValueError("case_directory_changed_or_escaped")
        manifest = _strict_json(case_dir / ".triage/manifest.json")
        trial._trial_case(manifest.get("case_id"), lab)
        roots = trial._production_roots(manifest)
        if roots != state.get("editable_paths") or roots != state.get("allowed_production_roots"):
            issues.append("broad_production_roots_changed")
        if _digest(case_dir / ".triage/manifest.json") != state.get("manifest_sha256"):
            issues.append("protected_manifest_changed")
        if _digest(directory / "original-failure.log") != state.get("baseline_run", {}).get("run_sha256"):
            issues.append("original_failure_log_changed")
        before = trial._source_hashes(case_dir, roots)
        original_hashes = state.get("original_buggy_source_sha256")
        initial = directory / "initial-source"
        source_diff, changed = [], []
        if not isinstance(original_hashes, dict):
            issues.append("original_source_snapshot_hashes_missing")
            original_hashes = {}
        for relative in sorted(set(original_hashes) | set(before)):
            old_path, current_path = initial / relative, case_dir / relative
            if (not old_path.resolve().is_relative_to(initial.resolve())
                    or not current_path.resolve().is_relative_to(case_dir)):
                raise ValueError("source audit path escaped")
            old = b""
            if relative in original_hashes:
                if old_path.is_symlink() or not old_path.is_file() or _digest(old_path) != original_hashes[relative]:
                    issues.append("original_source_snapshot_changed_or_missing")
                else:
                    old = old_path.read_bytes()
            if original_hashes.get(relative) != before.get(relative):
                changed.append(relative)
                current = current_path.read_bytes() if current_path.is_file() else b""
                source_diff.extend(difflib.unified_diff(old.decode("utf-8", "replace").splitlines(True),
                    current.decode("utf-8", "replace").splitlines(True), fromfile="initial/" + relative,
                    tofile="terminal/" + relative))
        observed["source_audit"] = {"changed_production_paths": changed,
            "diff": "".join(source_diff), "source_citation_correctness": None,
            "snapshot_hashes_match": "original_source_snapshot_changed_or_missing" not in issues
                and "original_source_snapshot_hashes_missing" not in issues}
        protected_before = trial._protected_hashes(case_dir, roots)
        terminal_match = before == state.get("final_source_sha256")
        observed.update(current_source_sha256_before_root=before, source_matches_timed_terminal=terminal_match,
                        original_buggy_source_sha256=state.get("original_buggy_source_sha256"),
                        final_source_sha256=state.get("final_source_sha256"))
        if not terminal_match:
            issues.append("source_changed_after_timed_terminal_or_missing_hashes")
        if protected_before != state.get("protected_sha256") or protected_before != state.get("final_protected_sha256"):
            issues.append("protected_tests_or_configuration_changed")
        timed = [row for row in state.get("verifications", []) if isinstance(row, dict) and row.get("kind") == "verify" and row.get("verified_endpoint") is True]
        last = timed[-1] if timed else {}
        timed_hashes = (last.get("validation", {}).get("run") or {}).get("editable_sha256_after")
        timed_match = bool(last and timed_hashes == before and last.get("end_clock") == state.get("end_clock"))
        observed.update(timed_verification_source_sha256=timed_hashes, source_matches_timed_verification=timed_match)
        if outcome == "solved" and (not timed_match or state.get("verified_repair") is not True):
            issues.append("solved_record_missing_matching_timed_verification")
        observed["root_verification_attempted"] = True
        started, tick = _utc(), perf_counter()
        try:
            validation = lab.validate_repair(case_dir)
            after, protected_after = trial._source_hashes(case_dir, roots), trial._protected_hashes(case_dir, roots)
            run = validation.get("run") or {}
            passed = bool(validation.get("passed") is True and validation.get("integrity_ok") is True
                and run.get("exit_code") == 0 and run.get("protected_files_ok") is True
                and run.get("source_stable") is True and not run.get("timed_out")
                and run.get("editable_sha256_after") == before and before == after and protected_before == protected_after)
            observed["root_verification"] = {"phase": "post_trial_root_verification", "outside_agent_clock": True,
                "started_at": started, "finished_at": _utc(), "elapsed_seconds": perf_counter() - tick,
                "validation": validation, "repair_reproduced": passed}
            observed["current_source_sha256_after_root"] = after
            if before != after or protected_before != protected_after:
                issues.append("source_or_tests_changed_during_root_verification")
            if validation.get("integrity_ok") is not True or run.get("protected_files_ok") is not True:
                issues.append("fresh_root_integrity_failed")
            if outcome == "solved" and not passed:
                issues.append("timed_success_did_not_reproduce")
        except Exception as exc:
            observed["root_verification"] = {"phase": "post_trial_root_verification", "outside_agent_clock": True,
                "started_at": started, "finished_at": _utc(), "elapsed_seconds": perf_counter() - tick,
                "repair_reproduced": False, "error_type": type(exc).__name__, "error": str(exc)}
            issues.append("root_verification_failed")
        if outcome == "solved" and observed["agent_elapsed_seconds"] is not None and observed["agent_elapsed_seconds"] > trial.CAP_SECONDS:
            issues.append("solved_record_exceeds_cap")
    except Exception as exc:
        observed["root_verification_error"] = {"type": type(exc).__name__, "message": str(exc)}
        issues.append("root_source_or_test_inspection_failed")
    observed["issues"] = sorted(set(issues))
    observed["protocol_valid"] = not issues
    observed["timing_eligible"] = not issues and observed["agent_elapsed_seconds"] is not None
    observed["timed_success_confirmed"] = bool(not issues and outcome == "solved" and observed["root_verification"]["repair_reproduced"])
    return observed, raw


def _summaries(slots):
    summaries = {}
    for treatment in trial.TREATMENTS:
        rows = [row for row in slots if row["treatment"] == treatment]
        solved = [row["agent_elapsed_seconds"] for row in rows if row["timed_success_confirmed"]]
        summaries[treatment] = {"planned_cases": 4, "outcomes": dict(Counter(row["outcome"] for row in rows)),
            "confirmed_solved": len(solved), "median_seconds_confirmed_solved": median(solved) if solved else None}
    differences = []
    for case_id in trial.CASE_IDS:
        normal = next(row for row in slots if row["case_id"] == case_id and row["treatment"] == "normal_tools")
        assisted = next(row for row in slots if row["case_id"] == case_id and row["treatment"] == "verified_assistant")
        eligible = normal["timed_success_confirmed"] and assisted["timed_success_confirmed"]
        differences.append({"case_id": case_id, "normal_tools_seconds": normal["agent_elapsed_seconds"],
            "verified_assistant_seconds": assisted["agent_elapsed_seconds"], "eligible": eligible,
            "difference_seconds_assistant_minus_normal": assisted["agent_elapsed_seconds"] - normal["agent_elapsed_seconds"] if eligible else None,
            "reason": None if eligible else "one_or_both_not_confirmed_solved"})
    return summaries, differences


def build_report(trial_directories, *, lab=None):
    lab = lab or trial._lab_api()
    observations, raw = [], []
    for value in trial_directories:
        observed, private = _observe(Path(value).resolve(), lab)
        observations.append(observed)
        raw.append(private)
    slots = []
    for planned in trial.plan_trials():
        records = [row for row in observations if row["declared_slot"] == planned["slot"]]
        single = records[0] if len(records) == 1 else None
        outcome = "missing" if not records else "duplicate" if len(records) > 1 else single["recorded_outcome"] if single["protocol_valid"] else "invalid"
        slots.append({**planned, "record_count": len(records), "records": records, "outcome": outcome,
            "recorded_outcome": single["recorded_outcome"] if single else None,
            "agent_elapsed_seconds": single["agent_elapsed_seconds"] if single else None,
            "timing_eligible": bool(single and single["timing_eligible"]),
            "timed_success_confirmed": bool(single and single["timed_success_confirmed"])})
    summaries, differences = _summaries(slots)
    return {"schema_version": 1, "suite": "triage-v4-agent-repair-report", "run_type": "agent-run",
        "created_at": _utc(), "reporter_source_sha256": _digest(Path(__file__)), "plan_sha256": trial._plan_hash(),
        "expected_slots": 8, "observed_records": len(observations), "slots": slots,
        "unassigned_records": [row for row in observations if row["declared_slot"] is None],
        "complete_unique_plan": all(row["record_count"] == 1 for row in slots) and all(row["declared_slot"] is not None for row in observations),
        "per_treatment": summaries, "paired_same_case_differences": differences,
        "human_elapsed_seconds": None, "human_minutes_saved": None, "cost_usd": None,
        "limitations": list(LIMITATIONS), "private_full_records": raw}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.exists() or args.private_output.exists():
            raise ValueError("Use new report filenames")
        allowed = (Path(trial._lab_api().LAB_ROOT) / "agent-trials").resolve()
        if args.trials_root.resolve() != allowed and not args.trials_root.resolve().is_relative_to(allowed):
            raise ValueError("The trials root must remain inside V4 agent-trials")
        report = build_report(sorted(path.parent for path in args.trials_root.rglob("trial.json")))
        export_report(report, public_output=args.output, private_output=args.private_output)
        import json
        print(json.dumps({"expected_slots": 8, "observed_records": report["observed_records"],
            "complete_unique_plan": report["complete_unique_plan"], "per_treatment": report["per_treatment"]}, indent=2))
        return 0 if report["complete_unique_plan"] and all(row["outcome"] != "invalid" for row in report["slots"]) else 1
    except (OSError, ValueError) as exc:
        print("V4 trial report: " + str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
