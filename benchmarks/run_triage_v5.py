"""Frozen V5 repair measurement, with protected repair outcomes kept separate.

Development may run before candidate selection. Evaluation and controls require
an exact candidate manifest and runtime identity check before case membership,
proof contents, source or logs are inspected. No reserved case enters this API.
Every selected attempt, error, output and timing is retained in write-once
private evidence; public output redacts local paths and raw protocol traces.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import median
from time import perf_counter
from uuid import uuid4

from . import triage_v5_lab as lab
from .run_triage_v3 import _public, _hash_text
from .triage_study import validate_loopback_url

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_FILE = "docs/triage-v5-experiment.md"
SUITE = "triage-v5-repair-measurement"
CASE_ORDER = {"development": ("d01", "d02"), "evaluation": tuple(f"e{index:02}" for index in range(1, 7)),
              "controls": ("c01", "c02")}
TRACE_KEYS = {"raw_logits", "token_logits", "vocabulary_mask", "raw_vocab_mask",
              "raw_protocol_trace", "private_protocol_trace", "full_vocabulary_scores"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(value) -> str:
    return sha(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())


def _json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()


def _source_files() -> tuple[str, ...]:
    """Freeze the complete loaded product and every V5 measurement module."""
    files = {path.relative_to(ROOT).as_posix() for path in (ROOT / "packages/python/src/decilist").glob("*.py")}
    for pattern in ("*v4*.py", "*v5*.py"):
        files.update(path.relative_to(ROOT).as_posix() for path in (ROOT / "benchmarks").glob(pattern))
    files.update({PROTOCOL_FILE, "benchmarks/triage_v5_cases.json", "benchmarks/triage_v5_freeze.json",
                  "benchmarks/triage_agent_trial.py", "benchmarks/triage_study.py", "benchmarks/run_triage_v3.py",
                  "benchmarks/report_triage_v3_trials.py"})
    return tuple(sorted(files))


def _source_hashes() -> dict:
    return {name: sha((ROOT / name).read_bytes()) for name in _source_files()}


def _workflow_module():
    from decilist import triage_v5
    return triage_v5


def _specification() -> dict:
    from decilist import triage_hypothesis_v5
    workflow = _workflow_module()
    return {"workflow_version": workflow.VERSION, "hypothesis": triage_hypothesis_v5.specification(),
            "measurement_schema": 1, "source_citation_is_correctness": False,
            "primary_endpoint": "unchanged_protected_tests_pass", "root_oracle_validation_separate": True,
            "runtime_context_tokens": 16384, "max_generations": 2, "legacy_log_is_workflow_gate": False}


def _protocol_metadata(value: dict) -> dict:
    item = value.get("qwen", {}) if isinstance(value, dict) else {}
    if (not isinstance(item.get("ready_identity"), dict) or not item["ready_identity"]
            or any(not _hash_text(item.get(key)) for key in ("model_artifact_sha256", "runtime_binary_sha256"))):
        raise ValueError("protocol_metadata requires Qwen ready_identity and measured model/runtime SHA-256 identities")
    if item["ready_identity"].get("n_ctx") != 16384:
        raise ValueError("V5 requires the prespecified 16384-token runtime context")
    json.dumps(value, allow_nan=False)
    return value


def candidate_snapshot(config_path: Path, *, protocol_metadata: dict,
                       qwen_url="http://127.0.0.1:8792") -> dict:
    """Build a candidate without reading case contents or contacting a runtime."""
    qwen_url = validate_loopback_url(qwen_url)
    config_path = Path(config_path)
    config = _workflow_module().load_model_config(config_path)
    metadata = _protocol_metadata(protocol_metadata)
    return {"schema_version": 1, "suite": SUITE,
            "corpus_freeze_sha256": sha(lab.FREEZE.read_bytes()),
            "catalog_sha256": sha(lab.CATALOG.read_bytes()), "sources_sha256": _source_hashes(),
            "specification": _specification(), "runtime_config_sha256": sha(config_path.read_bytes()),
            "runtime": _public({"url": qwen_url, **config}), "protocol_metadata": _public(metadata)}


def verify_candidate(path: Path | None, config_path: Path, *, qwen_url="http://127.0.0.1:8792") -> dict:
    if path is None:
        raise ValueError("evaluation, controls and reserved trials require --candidate-freeze before case inspection")
    saved = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(saved, dict) or saved.get("schema_version") != 1 or saved.get("suite") != SUITE:
        raise ValueError("unsupported V5 candidate manifest")
    current = candidate_snapshot(Path(config_path), protocol_metadata=saved.get("protocol_metadata", {}), qwen_url=qwen_url)
    for key, value in current.items():
        if saved.get(key) != value:
            raise ValueError("frozen V5 candidate drift: " + key)
    return saved


def check_ready(actual: dict, config: dict, candidate: dict | None) -> None:
    if actual.get("model") != config["model"] or actual.get("build_info") != config["expected_build"]:
        raise ValueError("Qwen runtime does not match the configured model/build")
    if actual.get("n_ctx") != 16384:
        raise ValueError("Qwen runtime context differs from the prespecified 16384 tokens")
    if candidate is not None and _public(actual) != candidate["protocol_metadata"]["qwen"]["ready_identity"]:
        raise ValueError("runtime ready identity differs from the frozen V5 candidate")


def _provider(config, endpoint):
    from decilist.triage_hypothesis_v5 import V5LlamaCppProvider
    return V5LlamaCppProvider(base_url=endpoint, **config)


def _production_roots(manifest: dict) -> list[str]:
    from pathlib import PurePosixPath, PureWindowsPath
    roots = manifest.get("allowed_production_roots")
    if not isinstance(roots, list) or not roots or len(set(roots)) != len(roots):
        raise ValueError("manifest requires broad allowed_production_roots")
    for root in roots:
        if (not isinstance(root, str) or not root or "\\" in root or PurePosixPath(root).is_absolute()
                or PureWindowsPath(root).drive or any(part in {"..", ".", ""} for part in root.rstrip("/").split("/"))):
            raise ValueError("production roots must be normalized relative directories")
    return roots


def production_hashes(project: Path, roots: list[str]) -> dict:
    result = {}
    project = Path(project).resolve()
    for root in roots:
        location = project / root
        if not location.resolve().is_relative_to(project) or location.is_symlink():
            raise ValueError("production root escaped the participant tree")
        for path in sorted(location.rglob("*")):
            if path.is_symlink() or not path.resolve().is_relative_to(project):
                raise ValueError("linked production source is not supported")
            if path.is_file() and "__pycache__" not in path.parts:
                result[path.relative_to(project).as_posix()] = sha(path.read_bytes())
    return result


async def workflow_bridge(evidence, *, project, manifest, work_dir, config_path, endpoint,
                          provider=None, apply=False):
    """Only observed evidence and neutral test selectors cross into the product."""
    # The active workflow reproduces trusted tests itself. An old/truncated log
    # cannot withhold it, and passing/mixed controls use the identical workflow.
    tests = workflow_test_args(manifest, "failing_tests")
    regressions = workflow_test_args(manifest, "regression_tests")
    # The same product entry point used by the CLI owns safe parent creation.
    destination = Path(work_dir)
    report = await _workflow_module().run_triage_v5(
        project, tests, regressions, destination, config_path,
        capture_failure_text=True, apply=apply, provider=provider, endpoint=endpoint,
        allowed_production_roots=_production_roots(manifest))
    report["measurement_test_args"] = {"failing_tests": tests, "regression_tests": regressions}
    retain_context(report, work_dir=destination)
    return report


def retain_context(report: dict, *, work_dir: Path) -> None:
    """Embed the actual saved context, preserving raw and canonical identities."""
    report["actual_context"] = None
    report["actual_context_json"] = None
    report["context_capture"] = {"status": "not_produced", "file_sha256": None, "canonical_sha256": None}
    value = report.get("context_path")
    if not value:
        return
    try:
        from decilist.triage_verify_v5 import _checked_directory, _is_link
        path = Path(value)
        parent = _checked_directory(path.parent)
        root = _checked_directory(Path(work_dir))
        if not parent.is_relative_to(root) or _is_link(path):
            raise ValueError("Workflow context escaped its declared evidence directory")
        data = path.read_bytes()
        if len(data) > 2 * 1024 * 1024:
            raise ValueError("Workflow context exceeds the retained evidence bound")
        from decilist.llama_cpp import _nonfinite, _unique_object
        context = json.loads(data, object_pairs_hook=_unique_object, parse_constant=_nonfinite)
        if not isinstance(context, dict) or sha(path.read_bytes()) != sha(data):
            raise ValueError("Workflow context is invalid or changed during retention")
        report["actual_context"] = context
        report["actual_context_json"] = data.decode("utf-8")
        report["context_capture"] = {"status": "retained", "path": str(path), "bytes": len(data),
            "file_sha256": sha(data), "canonical_sha256": fingerprint(context), "stable_during_capture": True,
            "source": "actual_workflow_context_file"}
    except (OSError, ValueError, TypeError) as exc:
        report["context_capture"] = {"status": "unavailable", "error_type": type(exc).__name__, "error": str(exc)}
        report.setdefault("errors", []).append({"stage": "context_retention", "type": type(exc).__name__, "message": str(exc)})


def workflow_test_args(manifest: dict, selection: str) -> list[str]:
    """Retain the frozen fixture's async execution mode in both test groups.

    The worker owns pytest/plugin isolation. Only the explicitly permitted async
    mode override is transferred from the neutral command; it never executes
    command text or accepts arbitrary command flags from model output.
    """
    tests, command = manifest.get(selection), manifest.get("test_command", [])
    if not isinstance(tests, list) or not tests or any(not isinstance(item, str) for item in tests):
        raise ValueError("manifest requires explicit test selectors")
    if not isinstance(command, list) or any(not isinstance(item, str) for item in command):
        raise ValueError("manifest test_command must contain literal argument strings")
    auto = any(command[index] == "-o" and command[index + 1] == "asyncio_mode=auto"
               for index in range(max(0, len(command) - 1)))
    return list(tests) + (["-o", "asyncio_mode=auto"] if auto else [])


def score_output(report: dict, root_validation: dict | None = None) -> dict:
    """Passing tests supports a repair; citations never receive correctness credit."""
    supported = report.get("state") == "hypothesis_supported_by_tests"
    run = (root_validation or {}).get("run") or {}
    root_pass = bool(root_validation and root_validation.get("passed") is True
                     and root_validation.get("integrity_ok") is True and run.get("exit_code") == 0
                     and run.get("protected_files_ok") is True and run.get("source_stable") is True
                     and not run.get("timed_out") and protected_checks_complete(run)
                     and root_validation.get("collection_matches_baseline") is True)
    attempts = []
    for attempt in report.get("attempts", []):
        if not isinstance(attempt, dict):
            continue
        generation = attempt.get("generation") or {}
        generated = generation.get("raw_output") is not None or generation.get("raw_completion") is not None
        requested = generated or any(error.get("stage") == "generation" for error in generation.get("errors", []) if isinstance(error, dict))
        attempts.append({"attempt": attempt.get("attempt"), "generation_helper_attempted": bool(generation),
            "model_request_attempted": requested, "generated_output_retained": generated,
            "structurally_valid_hypothesis": generation.get("status") == "hypothesis" and isinstance(generation.get("result"), dict),
            "patched_disposable_copy": bool((attempt.get("patch") or {}).get("replacements")),
            "target_tests_all_passed": _all_collected_passed(attempt.get("candidate")),
            "regression_tests_all_passed": _all_collected_passed(attempt.get("regression")),
            "rejected_or_error": bool(attempt.get("patch_error") or generation.get("errors")
                or any((attempt.get(key) or {}).get("state") in {"failed", "timeout", "error", "empty"} for key in ("candidate", "regression")))})
    return {"workflow_supported_by_unchanged_tests": supported,
            "applied": report.get("apply", {}).get("applied") is True,
            "root_protected_repair_verified": root_pass,
            "guarded": bool(report.get("guard_reason")) or report.get("reason") == "baseline_did_not_reproduce_a_real_test_failure",
            "needs_review": report.get("status") == "needs_review" or any(
                isinstance(attempt, dict) and (attempt.get("generation") or {}).get("status") == "needs_review"
                for attempt in report.get("attempts", [])), "source_citation_correctness": None,
            "complete_causal_correctness": None, "attempt_outcomes": attempts}


def protected_checks_complete(run: dict) -> bool:
    """V5 trusted lab audit must show every collected check actually executed."""
    return run.get("checks_complete") is True and run.get("test_execution_complete") is True


def retain_lab_audit(project: Path, run: dict) -> dict:
    """Retain trusted collection/outcome evidence and bind its original bytes."""
    from decilist.triage_verify_v5 import _checked_directory, _is_link
    from decilist.llama_cpp import _nonfinite, _unique_object
    project = Path(project).resolve()
    relative = run.get("audit_path")
    if not isinstance(relative, str):
        raise ValueError("protected run has no retained execution audit")
    path = project / relative
    parent = _checked_directory(path.parent)
    if not parent.is_relative_to((project / ".triage/runs").resolve()) or _is_link(path):
        raise ValueError("protected execution audit escaped its run directory")
    data = path.read_bytes()
    if len(data) > 2 * 1024 * 1024 or sha(data) != run.get("audit_sha256"):
        raise ValueError("protected execution audit is oversized or differs from its recorded hash")
    audit = json.loads(data, object_pairs_hook=_unique_object, parse_constant=_nonfinite)
    collected = audit.get("collected") if isinstance(audit, dict) else None
    if (not isinstance(collected, list) or not collected or any(not isinstance(item, str) for item in collected)
            or len(set(collected)) != len(collected) or sha(path.read_bytes()) != sha(data)):
        raise ValueError("protected collection audit is invalid or changed during capture")
    run["execution_audit"] = audit
    run["collection_sha256"] = fingerprint(collected)
    return run


def _all_collected_passed(run: dict | None) -> bool:
    if not isinstance(run, dict):
        return False
    collected, reports = run.get("collected", []), run.get("reports", [])
    if not isinstance(collected, list) or not isinstance(reports, list):
        return False
    calls = [row for row in reports if isinstance(row, dict) and row.get("when") == "call"]
    return bool(run.get("state") == "passed" and run.get("returncode") == 0 and collected
        and len(set(collected)) == len(collected) == len(calls)
        and {row.get("nodeid") for row in calls} == set(collected)
        and all(row.get("outcome") == "passed" for row in calls)
        and not any(row.get("outcome") == "failed" for row in reports if isinstance(row, dict)))


def summarize(rows: list[dict]) -> dict:
    first = [row for row in rows if row["repetition"] == 1]
    quality = [row for row in first if row["role"] == "regression"]
    attempt_rows = [attempt for row in quality for attempt in row["score"].get("attempt_outcomes", [])]
    attempt_fields = ("generation_helper_attempted", "model_request_attempted", "generated_output_retained",
                      "structurally_valid_hypothesis", "patched_disposable_copy", "target_tests_all_passed",
                      "regression_tests_all_passed", "rejected_or_error")
    workflow_errors = [_workflow_has_error(row["report"]) for row in rows]
    measurement_errors = [row["measurement_error"] is not None for row in rows]
    return {"unique_regressions": len(quality), "observations": len(rows),
            "first_observation_attempt_counts": {key: sum(attempt[key] for attempt in attempt_rows) for key in attempt_fields},
            "errors": sum(measured or workflow for measured, workflow in zip(measurement_errors, workflow_errors)),
            "measurement_errors": sum(measurement_errors), "workflow_errors": sum(workflow_errors),
            "workflow_supported": sum(row["score"]["workflow_supported_by_unchanged_tests"] for row in quality),
            "root_protected_repairs": sum(row["score"]["root_protected_repair_verified"] for row in quality),
            "guarded_first_observations": sum(row["score"]["guarded"] for row in first),
            "needs_review_first_observations": sum(row["score"]["needs_review"] for row in first),
            "first_observation_median_ms": median(row["end_to_end_ms"] for row in quality) if quality else None,
            "states": dict(Counter(str(row["report"].get("state")) for row in first)),
            "controls": [{"case_id": row["case_id"], "score": row["score"], "model_correctness_credit": None}
                         for row in first if row["role"] != "regression"],
            "human_time_saved": None, "complete_causal_correctness": None}


def _workflow_has_error(report: dict) -> bool:
    if report.get("errors"):
        return True
    if report.get("reason") in {"workflow_guard_or_runtime_failure", "baseline_integrity_failure",
        "candidate_integrity_failure", "verification_integrity_failure", "baseline_regression_unavailable_or_changed",
        "candidate_test_runtime_unavailable", "source_changed_before_feedback_attempt"}:
        return True
    return any(isinstance(attempt, dict) and bool((attempt.get("generation") or {}).get("errors"))
               for attempt in report.get("attempts", []))


def _trim_traces(value):
    if isinstance(value, list):
        return [_trim_traces(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in TRACE_KEYS:
                result[key + "_omitted"] = {"sha256": fingerprint(item), "private_only": True}
            else:
                result[key] = _trim_traces(item)
        return result
    return value


def public_report(report):
    # Evidence sanitizer also covers arbitrary POSIX roots and path-shaped keys.
    from .report_triage_v3_trials import _extra_paths
    result = _extra_paths(_public(_trim_traces({key: value for key, value in report.items() if key != "private_full_records"})))
    for row in result.get("rows", []):
        if isinstance(row, dict) and "input_evidence" in row:
            row["public_input_evidence_sha256"] = fingerprint(row["input_evidence"])
            row["public_evidence_matches_private_input"] = row.get("input_evidence_sha256") == row["public_input_evidence_sha256"]
    _public_context_hashes(result)
    return result


def _public_context_hashes(value):
    if isinstance(value, list):
        for item in value:
            _public_context_hashes(item)
    elif isinstance(value, dict):
        if "actual_context" in value:
            value["public_actual_context_sha256"] = fingerprint(value["actual_context"])
            value["public_context_matches_private_context"] = (
                value.get("context_capture", {}).get("canonical_sha256") == value["public_actual_context_sha256"])
            serialized = value.get("actual_context_json")
            value["public_serialized_context_sha256"] = sha(serialized.encode()) if isinstance(serialized, str) else None
            value["public_serialized_context_matches_private_file"] = (
                value["public_serialized_context_sha256"] == value.get("context_capture", {}).get("file_sha256")
                if isinstance(serialized, str) else None)
        for item in list(value.values()):
            if isinstance(item, (list, dict)):
                _public_context_hashes(item)


def export_report(report, *, public_output, private_output):
    paths = [Path(public_output), Path(private_output)]
    if paths[0].resolve() == paths[1].resolve() or any(path.exists() for path in paths):
        raise ValueError("Use distinct new public/private filenames; evidence is never overwritten")
    cases = [Path(row["case_dir"]).resolve() for row in report.get("rows", []) if isinstance(row, dict) and row.get("case_dir")]
    cases.extend((Path(row["directory"]) / "case").resolve() for row in report.get("private_full_records", [])
                 if isinstance(row, dict) and row.get("directory"))
    if any(path.resolve() == case or path.resolve().is_relative_to(case) for path in paths for case in cases):
        raise ValueError("Reports must remain outside every protected case directory")
    for path, value in ((paths[1], report), (paths[0], public_report(report))):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(_json_bytes(value))


def _validate_proof(proof: dict, ids: tuple[str, ...], *, split: str | None = None) -> dict:
    rows = proof.get("cases", [])
    if not isinstance(rows, list) or len({row.get("case_id") for row in rows}) != len(rows):
        raise ValueError("proof has missing or duplicate case identities")
    by_id = {row["case_id"]: row for row in rows}
    for case_id in ids:
        row = by_id.get(case_id, {})
        phases = [row.get("baseline", {}), row.get("mutation", {}), row.get("reference_repair", {}).get("run", {})]
        expected_seed_codes = (0,) if split == "controls" and case_id == "c01" else (1, 2)
        if not row.get("verified") or phases[0].get("exit_code") != 0 or phases[1].get("exit_code") not in expected_seed_codes or not row.get("reference_repair", {}).get("passed"):
            raise ValueError("proof does not establish original-pass/seed-fail/reference-pass for " + case_id)
        for phase in phases:
            if (not phase.get("source_stable") or not phase.get("protected_files_ok") or phase.get("timed_out")
                    or not protected_checks_complete(phase)
                    or sha(phase.get("stdout", "").encode()) != phase.get("run_sha256")):
                raise ValueError("proof phase integrity mismatch for " + case_id)
    return by_id


async def run(args, *, lab_api=None, callback=None, provider_factory=None) -> dict:
    selected_lab = lab_api or lab
    if args.split not in {"dev", "development", "evaluation", "controls"}:
        raise ValueError("reserved cases cannot enter machine evaluation")
    if type(args.repetitions) is not int or not 1 <= args.repetitions <= 10:
        raise ValueError("use 1–10 repetitions")
    if args.split in {"evaluation", "controls"} and args.repetitions != 1:
        raise ValueError("held-out V5 cases are attempted once each in their fixed order")
    output, private_output = Path(args.output), Path(args.private_output)
    if output.resolve() == private_output.resolve() or output.exists() or private_output.exists():
        raise ValueError("use new distinct report filenames")
    config_path, endpoint = Path(args.model_config), validate_loopback_url(args.qwen_url)
    candidate = None
    if args.split in {"evaluation", "controls"} or getattr(args, "candidate_freeze", None):
        candidate = verify_candidate(Path(args.candidate_freeze) if args.candidate_freeze else None,
                                     config_path, qwen_url=endpoint)
    config = _workflow_module().load_model_config(config_path)
    sources_before = _source_hashes()
    provider = (provider_factory or _provider)(config, endpoint)
    try:
        # Readiness identity is checked before neutral membership or proof data.
        actual = await provider.ready()
        check_ready(actual, config, candidate)
        selected_lab.verify_freeze()
        split = "development" if args.split in {"dev", "development"} else args.split
        ids = selected_lab.case_ids(split)
        if tuple(ids) != CASE_ORDER[split] or set(ids) & set(selected_lab.trial_case_ids()):
            raise ValueError("case membership/order differs from the fixed V5 split")
        proof_path = Path(args.proof)
        proof = _validate_proof(json.loads(proof_path.read_text(encoding="utf-8")), ids, split=split)
        run_root = Path(getattr(args, "work_dir", None) or selected_lab.LAB_ROOT / "measurements" / uuid4().hex)
        if run_root.exists():
            raise ValueError("measurement work directory already exists")
        run_root.mkdir(parents=True)
        rows = []
        for case_id in ids:
            for repetition in range(1, args.repetitions + 1):
                started = perf_counter()
                stages, report, validation, error = {}, {}, None, None
                case_dir = run_root / "cases" / f"{case_id}-{repetition}"
                before = after = observed = evidence = manifest = None
                stage = "fixture_and_evidence"
                tick = perf_counter()
                try:
                    manifest = selected_lab.prepare_case(case_id, case_dir)
                    roots = _production_roots(manifest)
                    before = production_hashes(case_dir, roots)
                    if before != proof[case_id].get("seeded_source_sha256"):
                        raise ValueError("fresh mutant differs from frozen fixture proof")
                    observed = selected_lab.run_case(case_dir)
                    retain_lab_audit(case_dir, observed)
                    expected_seed_codes = (0,) if split == "controls" and case_id == "c01" else (1, 2)
                    if (observed.get("exit_code") not in expected_seed_codes or not observed.get("protected_files_ok")
                            or not observed.get("source_stable") or observed.get("timed_out")
                            or not protected_checks_complete(observed)):
                        raise ValueError("fresh protected failure did not reproduce")
                    evidence = selected_lab.prepare_machine_evidence(case_dir, observed)
                    stages["fixture_and_evidence_ms"] = (perf_counter() - tick) * 1000
                    stage = "workflow"
                    tick = perf_counter()
                    report = await (callback or workflow_bridge)(evidence, project=case_dir, manifest=manifest,
                        work_dir=run_root / "workflow" / f"{case_id}-{repetition}", config_path=config_path,
                        endpoint=endpoint, provider=provider, apply=getattr(args, "apply", False))
                    if not isinstance(report, dict):
                        raise ValueError("workflow must return a structured report")
                    if not isinstance(report.get("attempts", []), list) or len(report.get("attempts", [])) > 2:
                        raise ValueError("V5 permits at most two generation attempts per workflow")
                    stages["workflow_ms"] = (perf_counter() - tick) * 1000
                    # The evaluator oracle is distinct from disposable verification.
                    if report.get("apply", {}).get("applied"):
                        stage = "root_protected_validation"
                        tick = perf_counter()
                        validation = selected_lab.validate_repair(case_dir)
                        retain_lab_audit(case_dir, validation.get("run") or {})
                        validation["collection_matches_baseline"] = validation["run"]["collection_sha256"] == observed["collection_sha256"]
                        if not validation["collection_matches_baseline"]:
                            raise ValueError("root protected test collection changed from the original observation")
                        stages["root_protected_validation_ms"] = (perf_counter() - tick) * 1000
                    after = production_hashes(case_dir, roots)
                except Exception as exc:
                    stages[stage + "_ms"] = (perf_counter() - tick) * 1000
                    error = {"stage": stage, "type": type(exc).__name__, "message": str(exc)}
                    report = {**report, "state_before_measurement_error": report.get("state"),
                              "state": "unavailable", "measurement_error": error}
                    if manifest is not None:
                        try:
                            after = production_hashes(case_dir, _production_roots(manifest))
                        except (OSError, ValueError) as hash_error:
                            error["terminal_source_hash_error"] = {"type": type(hash_error).__name__, "message": str(hash_error)}
                rows.append({"case_id": case_id, "case_dir": str(case_dir), "role": selected_lab.get_case_role(case_id), "split": split,
                    "repetition": repetition, "report": report, "root_validation": validation,
                    "measurement_error": error, "stage_timings_ms": stages,
                    "baseline_run": observed, "input_evidence": evidence, "input_manifest": manifest,
                    "input_evidence_sha256": fingerprint(evidence),
                    "end_to_end_ms": (perf_counter() - started) * 1000,
                    "source_sha256_before": before, "source_sha256_after": after,
                    "score": score_output(report, validation)})
    finally:
        await provider.aclose()
    result = {"schema_version": 1, "suite": SUITE, "split": split,
        "created_at": datetime.now(timezone.utc).isoformat(), "candidate": candidate,
        "candidate_manifest_sha256": sha(Path(args.candidate_freeze).read_bytes()) if getattr(args, "candidate_freeze", None) else None,
        "runtime_identity": actual, "runtime_config_sha256": sha(config_path.read_bytes()),
        "proof_sha256": sha(proof_path.read_bytes()), "sources_sha256_before": sources_before,
        "case_order": list(ids), "repetitions": args.repetitions,
        "sources_sha256_after": _source_hashes(), "rows": rows, "summary": summarize(rows),
        "limitations": ["Unchanged tests support a candidate repair and do not establish complete causality.",
            "Source citations are grounding evidence and receive no correctness credit.",
            "Fresh root protected validation is reported separately from workflow verification.",
            "Only first observations measure unique cases; repetitions measure timing and stability.",
            "These are agent/tool measurements; human elapsed time, time saved, token cost and GPU execution are unmeasured.",
            "Loopback runtime identities and external artifact hashes are setup evidence, not cryptographic attestation."],
        "unmeasured": {"human_time_saved": None, "cost_usd": None, "token_usage": None}}
    result["source_stable_during_measurement"] = result["sources_sha256_before"] == result["sources_sha256_after"]
    result["valid_frozen_comparison"] = candidate is not None and result["source_stable_during_measurement"]
    export_report(result, public_output=output, private_output=private_output)
    return public_report(result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("dev", "development", "evaluation", "controls"), required=True)
    parser.add_argument("--proof", required=True)
    parser.add_argument("--model-config", required=True)
    parser.add_argument("--candidate-freeze")
    parser.add_argument("--qwen-url", default="http://127.0.0.1:8792")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--output", required=True)
    parser.add_argument("--private-output", required=True)
    parser.add_argument("--work-dir")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(run(args))
        print(json.dumps(result["summary"], indent=2))
        return 0 if not result["summary"]["errors"] else 1
    except (OSError, ValueError) as exc:
        print("V5 measurement: " + str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
