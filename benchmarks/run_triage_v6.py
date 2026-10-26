"""V6 fixed model/context matrix with retained operational and case outcomes.

Development supports one available model. Fresh held-out work requires both
frozen profiles and advances four fixed batches through an exclusive ledger.
The root controls model-server switches; this module never launches a server.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from . import triage_v6_lab as lab
from .run_triage_v3 import _public, _hash_text
from .triage_study import validate_loopback_url
from .run_triage_v5 import (fingerprint, _json_bytes, _production_roots, production_hashes,
    workflow_test_args, score_output, summarize, retain_context, retain_lab_audit,
    protected_checks_complete, public_report, export_report, _validate_proof)

ROOT = Path(__file__).resolve().parents[1]
SUITE = "triage-v6-model-context-matrix"
PROTOCOL_FILE = "docs/triage-v6-experiment.md"
PROFILES = ("current", "stronger")
CONTEXT_POLICIES = ("v5", "v6")
GENERATION_PROFILES = {"current": "qwen3_coder_non_reasoning", "stronger": "qwen3_6_closed_thinking"}
EXPECTED_WEIGHTS = {"current": "f6050b4da22869aa3fb9d94bb3314e9fc5a027d4741c1f97761c387d6b2b2335",
    "stronger": "73f8260284708ed78ae266df672288b6ad1f2c73ec7ffeb7514b5cecdba646c9"}
EXPECTED_BUILD = "b11889-d854d98ba"
CASE_ORDER = {"development": ("d01", "d02"), "evaluation": tuple(f"e{index:02}" for index in range(1, 7)),
    "controls": ("c01", "c02")}
HELDOUT_ORDER = (*CASE_ORDER["evaluation"], *CASE_ORDER["controls"])
BATCHES = tuple({"batch": f"{profile}-{context}", "position": index, "model_profile": profile,
                 "context_policy": context} for index, (profile, context) in enumerate(
                     (("current", "v5"), ("current", "v6"), ("stronger", "v5"), ("stronger", "v6")), 1))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _workflow_module():
    from decilist import triage_v6
    return triage_v6


def _generator_module():
    from decilist import triage_hypothesis_v6
    return triage_hypothesis_v6


def _source_files():
    files = {path.relative_to(ROOT).as_posix() for path in (ROOT / "packages/python/src/decilist").glob("*.py")}
    for version in (4, 5, 6):
        files.update(path.relative_to(ROOT).as_posix() for path in (ROOT / "benchmarks").glob(f"*v{version}*.py"))
    files.update({PROTOCOL_FILE, "benchmarks/triage_v6_cases.json", "benchmarks/triage_v6_freeze.json",
        "benchmarks/triage_agent_trial.py", "benchmarks/triage_study.py", "benchmarks/run_triage_v3.py",
        "benchmarks/report_triage_v3_trials.py"})
    return tuple(sorted(files))


def _source_hashes():
    return {name: sha((ROOT / name).read_bytes()) for name in _source_files()}


def _specification():
    return {"workflow_version": _workflow_module().VERSION, "hypothesis": _generator_module().specification(),
        "batches": list(BATCHES), "case_order": list(HELDOUT_ORDER), "context_chars": 24000,
        "runtime_context_tokens": 16384, "max_generations": 2, "max_generated_tokens": 1200,
        "provider_token_cap": 1536, "temperature": 0, "seed": 42, "cache_prompt": False,
        "same_workflow_generator_and_guards": True, "source_citation_is_correctness": False}


def _config(path, model_profile):
    if model_profile not in PROFILES:
        raise ValueError("select current or stronger model_profile")
    config = _workflow_module().load_model_config(Path(path))
    if config.get("generation_profile") != GENERATION_PROFILES[model_profile] or config.get("expected_build") != EXPECTED_BUILD:
        raise ValueError("model role has a different generation profile or pinned runtime build")
    return config


def _validate_mode(proof, config):
    _generator_module().validate_generation_mode_proof(proof,
        generation_profile=config["generation_profile"],
        expected_chat_template_sha256=config["expected_chat_template_sha256"],
        model=config["model"], expected_build=config["expected_build"])


def load_profiles(path):
    path = Path(path)
    from decilist.llama_cpp import _nonfinite, _unique_object
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object, parse_constant=_nonfinite)
    json.dumps(value, allow_nan=False)
    if not isinstance(value, dict) or value.get("schema_version") != 1 or set(value.get("profiles", {})) != set(PROFILES):
        raise ValueError("profiles manifest requires schema_version=1 and both current/stronger profiles")
    profiles = {}
    for role in PROFILES:
        row = value["profiles"][role]
        if not isinstance(row, dict) or set(row) != {"config_path", "endpoint", "protocol_metadata"}:
            raise ValueError("each profile needs config_path, endpoint and protocol_metadata")
        config_path = Path(row["config_path"])
        if not config_path.is_absolute():
            config_path = path.resolve().parent / config_path
        profiles[role] = {"config_path": config_path, "config": _config(config_path, role),
            "endpoint": validate_loopback_url(row["endpoint"]), "protocol_metadata": row["protocol_metadata"]}
    return profiles


def candidate_snapshot(profiles_path):
    """Pure snapshot: supplied native proof is validated; no runtime is called."""
    profiles_path = Path(profiles_path)
    profiles = load_profiles(profiles_path)
    result = {"schema_version": 1, "suite": SUITE, "profiles_manifest_sha256": sha(profiles_path.read_bytes()),
        "sources_sha256": _source_hashes(), "corpus_freeze_sha256": sha(lab.FREEZE.read_bytes()),
        "catalog_sha256": sha(lab.CATALOG.read_bytes()), "specification": _specification(), "profiles": {}}
    for role, row in profiles.items():
        metadata, config = row["protocol_metadata"], row["config"]
        if (not isinstance(metadata, dict) or not isinstance(metadata.get("ready_identity"), dict)
                or metadata.get("model_artifact_sha256") != EXPECTED_WEIGHTS[role]
                or not _hash_text(metadata.get("runtime_binary_sha256"))):
            raise ValueError("each frozen profile needs its preselected weights and measured runtime identities")
        check_ready(metadata["ready_identity"], config)
        _validate_mode(metadata.get("generation_mode_proof"), config)
        result["profiles"][role] = {"config_sha256": sha(row["config_path"].read_bytes()),
            "configuration": _public(config), "endpoint": row["endpoint"], "protocol_metadata": _public(metadata)}
    binaries = {row["protocol_metadata"]["runtime_binary_sha256"] for row in result["profiles"].values()}
    if len(binaries) != 1:
        raise ValueError("both model treatments require the same frozen runtime executable")
    return _public(result)


def verify_candidate(path, profiles_path):
    if path is None or profiles_path is None:
        raise ValueError("held-out work and trials require one candidate containing both profiles before case access")
    saved = json.loads(Path(path).read_text(encoding="utf-8"))
    current = candidate_snapshot(profiles_path)
    if saved != current:
        raise ValueError("frozen V6 candidate/source/profile/configuration drift")
    return saved


def check_ready(actual, config, candidate=None, model_profile=None):
    if (not isinstance(actual, dict) or actual.get("model") != config["model"]
            or actual.get("build_info") != config["expected_build"] or actual.get("n_ctx") != 16384
            or actual.get("chat_template_sha256") != config["expected_chat_template_sha256"]):
        raise ValueError("runtime model/build/context/actual template identity differs from the selected profile")
    if candidate is not None and _public(actual) != candidate["profiles"][model_profile]["protocol_metadata"]["ready_identity"]:
        raise ValueError("actual runtime ready identity differs from its frozen native record")


def _provider(config, endpoint):
    return _generator_module().V6LlamaCppProvider(base_url=endpoint, **config)


async def preflight_provider(provider, config, *, candidate=None, model_profile=None):
    tick = perf_counter()
    ready = await provider.ready()
    check_ready(ready, config, candidate, model_profile)
    await provider.apply_template([dict(message) for message in _generator_module().MODE_PROBE_MESSAGES])
    proof = getattr(provider, "generation_mode_proof", None)
    _validate_mode(proof, config)
    after = await provider.ready()
    check_ready(after, config, candidate, model_profile)
    if _public(after) != _public(ready):
        raise ValueError("runtime ready identity changed across the native mode probe")
    return {"ready_identity": ready, "generation_mode_proof": proof,
            "ready_identity_after": after, "ready_identity_stable": True,
            "probe_messages": [dict(message) for message in _generator_module().MODE_PROBE_MESSAGES],
            "elapsed_ms": (perf_counter() - tick) * 1000,
            "actual_native_no_override_probe": True, "inference_requested": False}


async def capture_profile(config_path, *, model_profile, endpoint="http://127.0.0.1:8792", setup_metadata,
                          provider_factory=None):
    """Capture actual native identity/mode for one available profile; no inference."""
    config = _config(config_path, model_profile)
    endpoint = validate_loopback_url(endpoint)
    if (not isinstance(setup_metadata, dict) or setup_metadata.get("model_artifact_sha256") != EXPECTED_WEIGHTS[model_profile]
            or not _hash_text(setup_metadata.get("runtime_binary_sha256"))):
        raise ValueError("supply measured preselected model/runtime hashes and optional launch/allocation metadata")
    if {"ready_identity", "generation_mode_proof"} & set(setup_metadata):
        raise ValueError("native identity and mode proof are captured, not supplied as setup labels")
    json.dumps(setup_metadata, allow_nan=False)
    provider = (provider_factory or _provider)(config, endpoint)
    try:
        measured = await preflight_provider(provider, config, model_profile=model_profile)
    finally:
        await provider.aclose()
    return {**setup_metadata, **measured, "captured_at": _utc(), "model_profile": model_profile,
        "config_sha256": sha(Path(config_path).read_bytes()),
        "weights_and_launch_evidence_source": "external setup record; native HTTP identity is not file attestation"}


async def workflow_bridge(evidence, *, project, manifest, work_dir, config_path, endpoint,
                          provider=None, apply=False, context_policy="v6"):
    if context_policy not in CONTEXT_POLICIES:
        raise ValueError("context_policy must be exactly v5 or v6")
    tests, regressions = workflow_test_args(manifest, "failing_tests"), workflow_test_args(manifest, "regression_tests")
    report = await _workflow_module().run_triage_v6(project, tests, regressions, work_dir, config_path,
        capture_failure_text=True, apply=apply, provider=provider, endpoint=endpoint,
        context_policy=context_policy, max_context_chars=24000, allowed_production_roots=_production_roots(manifest))
    report["measurement_test_args"] = {"failing_tests": tests, "regression_tests": regressions}
    retain_context(report, work_dir=Path(work_dir))
    return report


def _atomic(path, value):
    temporary = path.with_name("." + path.name + "." + uuid4().hex + ".tmp")
    with temporary.open("xb") as handle:
        handle.write(_json_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


@contextmanager
def _matrix_lock(root):
    lock = root / ".matrix.lock"
    with lock.open("x", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    try:
        yield
    finally:
        lock.unlink()


def _matrix_read(root):
    path = root / "matrix.json"
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("suite") != SUITE or value.get("plan") != list(BATCHES) or not isinstance(value.get("batches"), list):
        raise ValueError("invalid or changed matrix assignment ledger")
    records = value["batches"]
    if len(records) > 4 or any(row.get("batch") != BATCHES[index]["batch"] for index, row in enumerate(records)):
        raise ValueError("matrix batches differ from the prespecified first-observation order")
    return value


def matrix_progress(root):
    value = _matrix_read(Path(root))
    if value is None:
        return {"suite": SUITE, "batches": [], "next_batch": BATCHES[0]["batch"], "complete": False}
    active = next((row for row in value["batches"] if row.get("status") == "running"), None)
    return {**value, "complete": len(value["batches"]) == 4 and active is None,
            "next_batch": None if active or len(value["batches"]) == 4 else BATCHES[len(value["batches"])]["batch"]}


def mark_incomplete(root, *, reason):
    root = Path(root)
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("declare the observed operational interruption")
    with _matrix_lock(root):
        value = _matrix_read(root)
        active = [row for row in (value or {}).get("batches", []) if row.get("status") == "running"]
        if len(active) != 1:
            raise ValueError("no unique running matrix batch can be marked incomplete")
        active[0].update(status="incomplete", interruption=reason, finished_at=_utc())
        if active[0].get("case_access_started") is not True:
            value.setdefault("operational_attempts", []).append({**active[0], "first_observation_consumed": False})
            value["batches"].remove(active[0])
        _atomic(root / "matrix.json", value)
    return matrix_progress(root)


def _reserve(root, batch, candidate_sha256, run_root):
    with _matrix_lock(root):
        value = _matrix_read(root) or {"schema_version": 1, "suite": SUITE, "plan": list(BATCHES),
            "candidate_manifest_sha256": candidate_sha256, "batches": []}
        if value.get("candidate_manifest_sha256") != candidate_sha256:
            raise ValueError("matrix candidate changed; a new candidate cannot reuse first observations")
        progress = matrix_progress(root)
        if progress["next_batch"] != batch["batch"]:
            raise ValueError("batch already attempted, interrupted, complete or out of its fixed order; use explicit replay")
        value["batches"].append({**batch, "status": "running", "started_at": _utc(),
            "run_root": str(run_root), "case_access_started": False, "case_order": list(HELDOUT_ORDER)})
        _atomic(root / "matrix.json", value)


def _matrix_update(root, batch_id, **changes):
    with _matrix_lock(root):
        value = _matrix_read(root)
        target = next(row for row in value["batches"] if row["batch"] == batch_id)
        target.update(changes)
        _atomic(root / "matrix.json", value)


def _storage(output, private_output, run_root, matrix_root, selected_lab):
    from decilist.triage_verify_v5 import _checked_directory
    paths = (Path(output), Path(private_output))
    if paths[0].resolve() == paths[1].resolve() or any(path.exists() for path in paths):
        raise ValueError("choose distinct new public/private output filenames")
    allowed = Path(selected_lab.LAB_ROOT).resolve()
    for path in (run_root, *([matrix_root] if matrix_root else [])):
        checked = _checked_directory(path)
        if checked == allowed or not checked.is_relative_to(allowed):
            raise ValueError("matrix and run directories must be children of the configured lab root")
    if run_root.exists() or any(path.resolve().is_relative_to((run_root / "cases").resolve()) for path in paths):
        raise ValueError("run directory must be new and reports must remain outside protected cases")
    for parent in {path.parent for path in paths} | {run_root} | ({matrix_root} if matrix_root else set()):
        _checked_directory(parent)
        parent.mkdir(parents=True, exist_ok=True)
        _checked_directory(parent)
        probe = parent / (".write-check-" + uuid4().hex)
        with probe.open("xb"):
            pass
        probe.unlink()


def _operational(args, error, *, source_before=None, candidate=None, runtime=None, selected=None,
                 case_access_started=False, first_observation_consumed=False, selected_lab=None):
    value = {"schema_version": 1, "suite": SUITE, "created_at": _utc(), "status": "operational_failure",
        "case_access_started": case_access_started, "first_observation_consumed": first_observation_consumed,
        "rows": [], "expected_case_order": list(HELDOUT_ORDER) if (selected or {}).get("batch") else [], "candidate": candidate,
        "sources_sha256_before": source_before, "runtime_preflight": runtime, "selection": selected,
        "operational_error": {"type": type(error).__name__, "message": str(error)},
        "summary": {"observations": 0, "errors": 1, "operational_errors": 1, "root_protected_repairs": 0},
        "valid_frozen_comparison": False, "human_time_saved": None}
    try:
        export_report(value, public_output=args.output, private_output=args.private_output)
    except (OSError, ValueError, TypeError) as export_error:
        # A used/unwritable requested name cannot erase the operational failure.
        folder = Path((selected_lab or lab).LAB_ROOT) / "measurements" / "operational"
        folder.mkdir(parents=True, exist_ok=True)
        key = uuid4().hex
        value["requested_export_error"] = {"type": type(export_error).__name__, "message": str(export_error)}
        export_report(value, public_output=folder / (key + ".public.json"), private_output=folder / (key + ".private.json"))
    return public_report(value)


async def run(args, *, lab_api=None, callback=None, provider_factory=None):
    selected_lab = lab_api or lab
    provider, candidate, runtime, sources_before, selection = None, None, None, None, None
    reserved, case_access = False, False
    replay = bool(getattr(args, "replay", False))
    matrix_root = Path(args.matrix_root) if getattr(args, "matrix_root", None) else None
    split = "development" if args.split in {"development", "dev"} else args.split
    try:
        if split not in {"development", "matrix", "evaluation", "controls"}:
            raise ValueError("reserved cases cannot enter machine evaluation")
        if split in {"evaluation", "controls"} and not replay:
            raise ValueError("fresh held-out observations require the fixed eight-case matrix batch; use --replay for exposed cases")
        if split == "matrix":
            selection = next((dict(row) for row in BATCHES if row["batch"] == args.batch), None)
            if selection is None or (matrix_root is None and not replay):
                raise ValueError("select a declared matrix batch and its persistent matrix-root")
        else:
            selection = {"model_profile": args.model_profile, "context_policy": args.context_policy, "batch": None}
        role, context_policy = selection["model_profile"], selection["context_policy"]
        if role not in PROFILES or context_policy not in CONTEXT_POLICIES:
            raise ValueError("select a declared model profile and v5/v6 context policy")
        if split != "development" or getattr(args, "candidate_freeze", None):
            candidate = verify_candidate(getattr(args, "candidate_freeze", None), getattr(args, "profiles", None))
            profiles = load_profiles(args.profiles)
            profile = profiles[role]
            config_path, config, endpoint = profile["config_path"], profile["config"], profile["endpoint"]
        else:
            config_path = Path(args.model_config)
            config, endpoint = _config(config_path, role), validate_loopback_url(args.qwen_url)
        config_before = sha(config_path.read_bytes())
        sources_before = _source_hashes()
        run_root = Path(getattr(args, "work_dir", None) or Path(selected_lab.LAB_ROOT) / "measurements" / "v6" / uuid4().hex)
        _storage(args.output, args.private_output, run_root, matrix_root, selected_lab)
        provider = (provider_factory or _provider)(config, endpoint)
        runtime = await preflight_provider(provider, config, candidate=candidate, model_profile=role)
        selected_lab.verify_freeze(require_retained_proof=True)
        if _source_hashes() != sources_before or sha(config_path.read_bytes()) != config_before:
            raise ValueError("source or selected configuration changed during preflight; no case was opened")
        if candidate:
            verify_candidate(args.candidate_freeze, args.profiles)
        candidate_sha = sha(Path(args.candidate_freeze).read_bytes()) if candidate else None
        if split == "matrix" and not replay:
            _reserve(matrix_root, selection, candidate_sha, run_root)
            reserved = True
        case_access = True
        if reserved:
            _matrix_update(matrix_root, selection["batch"], case_access_started=True)
        ids = HELDOUT_ORDER if split == "matrix" else CASE_ORDER[split]
        observed_ids = ((*selected_lab.case_ids("evaluation"), *selected_lab.case_ids("controls")) if split == "matrix"
                        else tuple(selected_lab.case_ids(split)))
        if tuple(observed_ids) != tuple(ids) or set(ids) & set(selected_lab.trial_case_ids()):
            raise ValueError("case membership/order differs from the declared V6 split")
        proof_path = Path(args.proof)
        proof_data = json.loads(proof_path.read_text(encoding="utf-8"))
        proof = _validate_proof(proof_data, tuple(item for item in ids if item != "c01"))
        if "c01" in ids:
            proof.update(_validate_proof(proof_data, ("c01",), split="controls"))
    except Exception as error:
        if provider is not None:
            await provider.aclose()
        if reserved and case_access:
            _matrix_update(matrix_root, selection["batch"], status="incomplete", finished_at=_utc(),
                interruption={"type": type(error).__name__, "message": str(error)})
            return _operational(args, error, source_before=sources_before, candidate=candidate, runtime=runtime,
                selected=selection, case_access_started=True, first_observation_consumed=True, selected_lab=selected_lab)
        return _operational(args, error, source_before=sources_before, candidate=candidate, runtime=runtime,
            selected=selection, case_access_started=case_access, selected_lab=selected_lab)
    rows = []
    observations = run_root / "observations"
    observations.mkdir()
    close_error = None
    try:
        for case_id in ids:
            started, tick = perf_counter(), perf_counter()
            stages, report, validation, error = {}, {}, None, None
            manifest = observed = evidence = before = after = None
            stage = "fixture_and_evidence"
            case_dir = run_root / "cases" / case_id
            try:
                manifest = selected_lab.prepare_case(case_id, case_dir)
                roots = _production_roots(manifest)
                before = production_hashes(case_dir, roots)
                if before != proof[case_id].get("seeded_source_sha256"):
                    raise ValueError("fresh seeded source differs from frozen fixture proof")
                observed = retain_lab_audit(case_dir, selected_lab.run_case(case_dir))
                expected = (0,) if case_id == "c01" else (1, 2)
                if (observed.get("exit_code") not in expected or not observed.get("protected_files_ok")
                        or not observed.get("source_stable") or observed.get("timed_out") or not protected_checks_complete(observed)):
                    raise ValueError("fresh protected fixture did not reproduce its audited state")
                evidence = selected_lab.prepare_machine_evidence(case_dir, observed)
                stages[stage] = (perf_counter() - tick) * 1000
                stage, tick = "workflow", perf_counter()
                report = await (callback or workflow_bridge)(evidence, project=case_dir, manifest=manifest,
                    work_dir=run_root / "workflow" / case_id, config_path=config_path, endpoint=endpoint,
                    provider=provider, apply=bool(getattr(args, "apply", False)), context_policy=context_policy)
                if (not isinstance(report, dict) or report.get("context_policy") != context_policy
                        or not isinstance(report.get("attempts", []), list) or len(report.get("attempts", [])) > 2):
                    raise ValueError("workflow report changed the context policy or exceeded its generation contract")
                stages[stage] = (perf_counter() - tick) * 1000
                if report.get("apply", {}).get("applied"):
                    stage, tick = "root_protected_validation", perf_counter()
                    validation = selected_lab.validate_repair(case_dir)
                    retain_lab_audit(case_dir, validation.get("run") or {})
                    validation["collection_matches_baseline"] = validation["run"]["collection_sha256"] == observed["collection_sha256"]
                    if not validation["collection_matches_baseline"]:
                        raise ValueError("root protected collection differs from the original observation")
                    stages[stage] = (perf_counter() - tick) * 1000
                after = production_hashes(case_dir, roots)
            except Exception as caught:
                stages[stage] = (perf_counter() - tick) * 1000
                error = {"stage": stage, "type": type(caught).__name__, "message": str(caught)}
                report = {**(report if isinstance(report, dict) else {}), "state_before_measurement_error": report.get("state") if isinstance(report, dict) else None,
                    "state": "unavailable", "measurement_error": error}
                if manifest is not None:
                    try:
                        after = production_hashes(case_dir, _production_roots(manifest))
                    except (OSError, ValueError) as hash_error:
                        error["terminal_source_hash_error"] = {"type": type(hash_error).__name__, "message": str(hash_error)}
            score = score_output(report, validation)
            if error:
                score.update(workflow_supported_by_unchanged_tests=False, root_protected_repair_verified=False)
            row = {"case_id": case_id, "case_dir": str(case_dir), "role": "control" if case_id.startswith("c") else "regression",
                "split": "controls" if case_id.startswith("c") else split, "repetition": 1,
                "model_profile": role, "context_policy": context_policy, "report": report, "root_validation": validation,
                "measurement_error": error, "stage_timings_ms": stages, "end_to_end_ms": (perf_counter() - started) * 1000,
                "source_sha256_before": before, "source_sha256_after": after, "baseline_run": observed,
                "input_evidence": evidence, "input_evidence_sha256": fingerprint(evidence), "input_manifest": manifest, "score": score}
            with (observations / (case_id + ".json")).open("xb") as handle:
                handle.write(_json_bytes(row))
            rows.append(row)
    except BaseException:
        if reserved:
            _matrix_update(matrix_root, selection["batch"], status="incomplete", finished_at=_utc(), retained_observations=len(rows))
        raise
    finally:
        try:
            await provider.aclose()
        except Exception as error:
            close_error = {"type": type(error).__name__, "message": str(error)}
    source_error = None
    candidate_stable = True
    try:
        sources_after = _source_hashes()
    except (OSError, ValueError) as error:
        sources_after = None
        source_error = {"type": type(error).__name__, "message": str(error)}
    try:
        if sha(config_path.read_bytes()) != config_before:
            raise ValueError("selected configuration changed during the batch")
        if candidate:
            verify_candidate(args.candidate_freeze, args.profiles)
    except (OSError, ValueError, TypeError) as error:
        candidate_stable = False
        source_error = {"stage": "candidate_end_check", "type": type(error).__name__, "message": str(error)}
    stable = sources_before == sources_after
    result = {"schema_version": 1, "suite": SUITE, "created_at": _utc(),
        "status": "complete" if stable and candidate_stable else "identity_drift",
        "selection": selection, "split": split, "case_order": list(ids), "rows": rows, "summary": summarize(rows),
        "replay": replay, "first_observation": split == "matrix" and not replay, "case_access_started": True,
        "candidate": candidate, "candidate_manifest_sha256": candidate_sha, "runtime_preflight": runtime,
        "runtime_config_sha256": config_before, "proof_sha256": sha(proof_path.read_bytes()),
        "sources_sha256_before": sources_before, "sources_sha256_after": sources_after,
        "source_stable_during_measurement": stable, "candidate_stable_during_measurement": candidate_stable,
        "valid_frozen_comparison": bool(candidate and stable and candidate_stable and not close_error and not replay and split == "matrix"),
        "provider_close_error": close_error, "source_verification_error": source_error,
        "limitations": ["The same six defects form paired engineering observations, not 24 independent tasks.",
            "A native template proof verifies the configured bounded non-thinking profile, not general reasoning capability.",
            "Warm task clocks exclude downloads, initial loading and model switches; workflow, waiting and failed stages remain included.",
            "Passing unchanged tests supports a candidate intervention; source citations do not establish correctness.",
            "External model/runtime hashes and HTTP identity are recorded setup evidence, not cryptographic server attestation."],
        "unmeasured": {"human_time_saved": None, "cost_usd": None, "token_usage": None}}
    if close_error or source_error or not stable or not candidate_stable:
        result["summary"]["errors"] += 1
        result["summary"]["post_case_operational_errors"] = 1
    try:
        export_report(result, public_output=args.output, private_output=args.private_output)
    except BaseException:
        if reserved:
            _matrix_update(matrix_root, selection["batch"], status="incomplete", finished_at=_utc(), retained_observations=len(rows),
                interruption="final export failed; write-once per-case observations remain in run_root")
        raise
    if reserved:
        _matrix_update(matrix_root, selection["batch"], status="complete" if result["valid_frozen_comparison"] else "invalid_post_case_identity_or_operation", finished_at=_utc(),
            public_report_path=str(args.output), private_report_path=str(args.private_output), retained_observations=len(rows),
            source_stable_during_measurement=stable, valid_frozen_comparison=result["valid_frozen_comparison"],
            public_report_sha256=sha(Path(args.output).read_bytes()), private_report_sha256=sha(Path(args.private_output).read_bytes()))
    return public_report(result)


def matrix_summary(reports):
    """Describe every planned cell and paired case; never select fastest retries."""
    cells = []
    for planned in BATCHES:
        records = [report for report in reports if isinstance(report.get("selection"), dict)
            and report["selection"].get("batch") == planned["batch"] and not report.get("replay")
            and (report.get("first_observation") or report.get("first_observation_consumed"))]
        unique = records[0] if len(records) == 1 else None
        complete = bool(unique and unique.get("valid_frozen_comparison") and tuple(unique.get("case_order", [])) == HELDOUT_ORDER
                        and tuple(row.get("case_id") for row in unique.get("rows", [])) == HELDOUT_ORDER)
        cells.append({**planned, "record_count": len(records), "outcome": "missing" if not records else "duplicate" if len(records) > 1 else "complete" if complete else "invalid",
            "summary": unique.get("summary") if unique else None, "records": records})
    paired = []
    for case_id in CASE_ORDER["evaluation"]:
        comparisons = []
        for left, right in ((0, 1), (2, 3), (0, 2), (1, 3)):
            a, b = cells[left], cells[right]
            first = next((row for row in (a["records"][0].get("rows", []) if a["record_count"] == 1 else []) if row["case_id"] == case_id), None)
            second = next((row for row in (b["records"][0].get("rows", []) if b["record_count"] == 1 else []) if row["case_id"] == case_id), None)
            comparisons.append({"from": a["batch"], "to": b["batch"],
                "valid_first_observation_pair": a["outcome"] == b["outcome"] == "complete",
                "from_protected_repair": first["score"]["root_protected_repair_verified"] if first else None,
                "to_protected_repair": second["score"]["root_protected_repair_verified"] if second else None,
                "difference_ms_to_minus_from": second["end_to_end_ms"] - first["end_to_end_ms"]
                    if first and second and a["outcome"] == b["outcome"] == "complete" else None})
        paired.append({"case_id": case_id, "comparisons": comparisons})
    candidate_ids = {report.get("candidate_manifest_sha256") for report in reports if report.get("first_observation")}
    return {"suite": SUITE, "cells": cells, "paired_same_case": paired,
        "operational_records": [report for report in reports if report.get("status") == "operational_failure"],
        "replay_records": [report for report in reports if report.get("replay")],
        "complete_unique_matrix": all(cell["outcome"] == "complete" for cell in cells) and len(candidate_ids) == 1,
        "human_time_saved": None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("dev", "development", "matrix", "evaluation", "controls"), default="development")
    parser.add_argument("--batch", choices=tuple(row["batch"] for row in BATCHES))
    parser.add_argument("--model-profile", choices=PROFILES, default="current")
    parser.add_argument("--context-policy", choices=CONTEXT_POLICIES, default="v6")
    parser.add_argument("--model-config")
    parser.add_argument("--qwen-url", default="http://127.0.0.1:8792")
    parser.add_argument("--profiles")
    parser.add_argument("--candidate-freeze")
    parser.add_argument("--matrix-root", type=Path)
    parser.add_argument("--matrix-status", action="store_true")
    parser.add_argument("--mark-incomplete")
    parser.add_argument("--proof")
    parser.add_argument("--output")
    parser.add_argument("--private-output")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--capture-profile", choices=PROFILES)
    parser.add_argument("--setup-metadata", type=Path)
    parser.add_argument("--capture-output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.capture_profile:
            if not args.model_config or not args.setup_metadata or not args.capture_output:
                raise ValueError("profile capture requires --model-config, --setup-metadata and --capture-output")
            if args.capture_output.exists():
                raise ValueError("capture output exists; original native evidence is never overwritten")
            result = asyncio.run(capture_profile(args.model_config, model_profile=args.capture_profile,
                endpoint=args.qwen_url, setup_metadata=json.loads(args.setup_metadata.read_text(encoding="utf-8"))))
            args.capture_output.parent.mkdir(parents=True, exist_ok=True)
            with args.capture_output.open("xb") as handle:
                handle.write(_json_bytes(result))
            print(json.dumps({"recorded": True, "model_profile": args.capture_profile,
                "inference_requested": False, "sha256": sha(args.capture_output.read_bytes())}, indent=2))
            return 0
        elif args.matrix_status or args.mark_incomplete:
            if args.matrix_root is None:
                raise ValueError("matrix progress requires --matrix-root")
            result = mark_incomplete(args.matrix_root, reason=args.mark_incomplete) if args.mark_incomplete else matrix_progress(args.matrix_root)
        else:
            if not args.proof or not args.output or not args.private_output:
                raise ValueError("measurement requires --proof, --output and --private-output")
            result = asyncio.run(run(args))
        print(json.dumps(public_report(result), indent=2))
        return 1 if result.get("status") in {"operational_failure", "identity_drift"} or result.get("summary", {}).get("errors") else 0
    except (OSError, ValueError, TypeError) as error:
        print("V6 measurement: " + str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
