"""Natural transfer identity and neutral bridge adapter.

Candidate construction reads code, profile metadata, the protocol and the
author's SAFE freeze descriptor only. It never imports the natural bridge or
opens a case, sealed registry, test, historical source, transcript or reference.
The injected bridge is invoked only after candidate verification by the trial.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from benchmarks.run_triage_v3 import _public, _hash_text
from benchmarks.run_triage_v5 import (_json_bytes, fingerprint, _production_roots, production_hashes,
    workflow_test_args, retain_context, public_report, export_report)
from benchmarks.run_triage_v6 import (_provider, _config, _validate_mode, check_ready, preflight_provider,
    EXPECTED_WEIGHTS)
from benchmarks.triage_study import _digest, validate_loopback_url

REPO_ROOT = Path(__file__).resolve().parents[1]
MEASUREMENT_ROOT = Path(__file__).resolve().parent
MEASUREMENT_FILES = ("natural_candidate.py", "natural_trial.py", "report_natural_trials.py")
CASE_IDS = ("NAT-01", "NAT-03", "NAT-04")
SUITE = "natural-historical-transfer-agent-trials"


def _workflow_module():
    from decilist import triage_lines_v6
    return triage_lines_v6


def _generator_module():
    from decilist import triage_line_hypothesis_v6
    return triage_line_hypothesis_v6


class SelectedProtocolError(ValueError):
    """Reject mismatched provenance while retaining the complete workflow output."""
    def __init__(self, message, workflow_report):
        super().__init__(message)
        self.workflow_report = workflow_report


def _json(path):
    from decilist.llama_cpp import _nonfinite, _unique_object
    return json.loads(Path(path).read_bytes(), object_pairs_hook=_unique_object, parse_constant=_nonfinite)


def _input_paths(profiles_path):
    path = Path(profiles_path).resolve()
    value = _json(path)
    required = {"schema_version", "profiles", "bridge_freeze_path", "bridge_path"}
    if not isinstance(value, dict) or set(value) != required or value["schema_version"] != 1:
        raise ValueError("natural profile manifest needs schema_version, profiles, bridge_freeze_path and bridge_path")
    if "stronger" not in value["profiles"] or set(value["profiles"]) - {"current", "stronger"}:
        raise ValueError("natural transfer requires the preselected stronger profile")
    def local(raw):
        target = Path(raw)
        return target.resolve() if target.is_absolute() else (path.parent / target).resolve()
    row = value["profiles"]["stronger"]
    if set(row) != {"config_path", "endpoint", "protocol_metadata"}:
        raise ValueError("stronger profile needs config_path, endpoint and actual protocol_metadata")
    return value, {"manifest": path, "config": local(row["config_path"]),
                   "safe_freeze": local(value["bridge_freeze_path"]), "bridge": local(value["bridge_path"])}


def load_profiles(profiles_path):
    value, paths = _input_paths(profiles_path)
    row = value["profiles"]["stronger"]
    return {"stronger": {"config_path": paths["config"], "config": _config(paths["config"], "stronger"),
        "endpoint": validate_loopback_url(row["endpoint"]), "protocol_metadata": row["protocol_metadata"]}}


def _sources():
    """Freeze all product code and imported measurement helpers, without catalogs."""
    result = {}
    for directory, pattern in ((REPO_ROOT / "packages/python/src/decilist", "*.py"),
                               (REPO_ROOT / "benchmarks", "*.py")):
        for path in sorted(directory.glob(pattern)):
            result[str(path.relative_to(REPO_ROOT).as_posix())] = _digest(path)
    return result


def candidate_snapshot(profiles_path):
    value, paths = _input_paths(profiles_path)
    profile = load_profiles(profiles_path)["stronger"]
    config, metadata = profile["config"], profile["protocol_metadata"]
    if (not isinstance(metadata, dict) or metadata.get("model_artifact_sha256") != EXPECTED_WEIGHTS["stronger"]
            or not _hash_text(metadata.get("runtime_binary_sha256"))):
        raise ValueError("retain measured preselected stronger weights/runtime identities")
    check_ready(metadata.get("ready_identity"), config)
    _validate_mode(metadata.get("generation_mode_proof"), config)
    safe = _json(paths["safe_freeze"])
    if (safe.get("schema_version") != 1 or not _hash_text(safe.get("fixture_set_identity"))
            or safe.get("strict_cases") != 3 or safe.get("descriptor", {}).get("strict_case_ids") != list(CASE_IDS)
            or safe.get("descriptor", {}).get("allowed_production_roots") != ["src"]
            or safe.get("bridge_code_sha256") != _digest(paths["bridge"])):
        raise ValueError("safe author bridge identity does not match the strict predeclared transfer pool")
    for name, wanted in safe.get("frozen_support_sha256", {}).items():
        if Path(name).name != name or _digest(REPO_ROOT / "packages/python/src/decilist" / name) != wanted:
            raise ValueError("frozen bridge support changed: " + name)
    protocol = REPO_ROOT / "docs/triage-natural-transfer-protocol.md"
    snapshot = {"schema_version": 1, "suite": SUITE, "profiles_manifest_sha256": _digest(paths["manifest"]),
        "protocol_sha256": _digest(protocol), "repo_sources_sha256": _sources(),
        "measurement_sources_sha256": {name: _digest(MEASUREMENT_ROOT / name) for name in MEASUREMENT_FILES},
        "bridge_source_sha256": _digest(paths["bridge"]), "safe_bridge_freeze_sha256": _digest(paths["safe_freeze"]),
        "fixture_set_identity": safe["fixture_set_identity"], "safe_bridge_identity": safe,
        "profiles": {"stronger": {"config_sha256": _digest(paths["config"]), "endpoint": profile["endpoint"],
            "configuration": _public(config), "protocol_metadata": _public(metadata)}},
        "specification": {"workflow_version": _workflow_module().VERSION, "context_policy": "v6",
            "context_chars": 24000, "generations": 2, "generated_tokens": 1200, "provider_cap": 1536,
            "temperature": 0, "seed": 42, "cache_prompt": False, "serving_context": 16384,
            "case_ids": list(CASE_IDS), "first_assistant_call_is_timed": True,
            "source_citation_is_correctness": False, "no_prior_transfer_assistant_evaluation": True}}
    generator = _generator_module()
    snapshot["specification"].update(generator=generator.specification(), edit_protocol=generator.VERSION,
        generation_module=generator.__name__)
    return _public(snapshot)


def verify_candidate(candidate_path, profiles_path):
    if not candidate_path or not profiles_path:
        raise ValueError("freeze the exact natural-transfer candidate before any case exposure")
    saved, current = _json(candidate_path), candidate_snapshot(profiles_path)
    if saved != current:
        raise ValueError("natural candidate code/profile/protocol/bridge/measurement identity drift")
    return saved


def retain_lab_audit(project, run):
    """Validate an embedded native audit; do not open a sealed transcript."""
    audit = run.get("execution_audit")
    if not isinstance(audit, dict):
        raise ValueError("natural bridge did not retain the actual execution audit")
    collected, reports = audit.get("collected"), audit.get("reports")
    expected = run.get("expected_check_count")
    if (not isinstance(collected, list) or not collected or len(set(collected)) != len(collected)
            or not isinstance(reports, list) or type(expected) is not int or expected < 1):
        raise ValueError("natural collection/audit identity is incomplete")
    digest = hashlib.sha256(json.dumps(collected, sort_keys=True).encode()).hexdigest()
    if run.get("collection_sha256") != digest:
        raise ValueError("natural collected-node identity differs from its retained audit")
    calls = [row for row in reports if isinstance(row, dict) and row.get("when") == "call"]
    complete = bool(len(collected) == expected == len(calls)
        and {row.get("nodeid") for row in calls} == set(collected)
        and all(row.get("outcome") in {"passed", "failed"} and not row.get("wasxfail") for row in calls)
        and not audit.get("collection_errors")
        and all(row.get("outcome") != "failed" for row in audit.get("collection_reports", [])))
    run["checks_complete"] = run.get("checks_complete") is True and complete
    run["test_execution_complete"] = run.get("test_execution_complete") is True and complete
    run["execution_audit_sha256"] = fingerprint(audit)
    return run


def protected_checks_complete(run):
    return run.get("checks_complete") is True and run.get("test_execution_complete") is True


def _protected_manifest(directory):
    return _json(Path(directory).parent / "participant-manifest.json")


def protected_hashes(directory, roots):
    directory = Path(directory)
    protected = set(_protected_manifest(directory).get("protected_sha256", {}))
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("linked participant content is not permitted")
        if not path.is_file():
            continue
        if any(part in {"__pycache__", ".pytest_cache"} for part in path.relative_to(directory).parts):
            continue
        relative = path.relative_to(directory).as_posix()
        if relative in protected or not relative.startswith(tuple(root.rstrip("/") + "/" for root in roots)):
            result[relative] = _digest(path)
    return result


class NaturalLab:
    """Neutral facade only; the root injects the author bridge after freeze."""
    def __init__(self, bridge, *, expected_identity, expected_counts=None, lab_root=None):
        self.bridge = bridge
        self.expected_identity = expected_identity
        self.expected_counts = dict(expected_counts or {})
        self.LAB_ROOT = Path(lab_root if lab_root is not None else bridge.ROOT).resolve()

    def trial_case_ids(self):
        return CASE_IDS

    def get_case_role(self, case_id):
        return "natural_transfer" if case_id in CASE_IDS else "excluded_overlap_control"

    def verify_freeze(self, *, require_retained_proof=False):
        if require_retained_proof is not True:
            raise ValueError("natural transfer requires actual retained author proof")
        result = self.bridge.verify_identity(self.expected_identity)
        if (not isinstance(result, dict) or result.get("verified") is not True
                or result.get("fixture_set_identity") != self.expected_identity
                or any(result.get(key) is not True for key in ("expected_identity_match", "protected_canonical_ok",
                    "upstream_source_identities_ok", "proof_verified", "main_environment_unchanged"))):
            raise ValueError("natural canonical/proof/dependency identity verification failed")
        return result

    def prepare_case(self, case_id, directory):
        raw = self.bridge.prepare_case(case_id, directory)
        if (raw.get("case_id") != case_id or Path(raw.get("project_root", "")).resolve() != Path(directory).resolve()
                or raw.get("allowed_production_roots") != ["src"]
                or raw.get("fixture_set_identity") != self.expected_identity):
            raise ValueError("natural participant manifest changed case, broad source scope or canonical identity")
        source = production_hashes(Path(directory), ["src"])
        if raw.get("canonical_source_sha256") != source:
            raise ValueError("prepared source does not match exact historical canonical bytes")
        if (case_id in self.expected_counts and raw.get("expected_check_count") != self.expected_counts[case_id]):
            raise ValueError("prepared collection count differs from the safe predeclared identity")
        return raw

    def _run(self, directory, *, validate):
        directory = Path(directory)
        manifest = _protected_manifest(directory)
        case_id = manifest["case_id"]
        before = production_hashes(directory, ["src"])
        protected_before = protected_hashes(directory, ["src"])
        destination = self.LAB_ROOT / "measurement-runtime-private" / uuid4().hex
        function = self.bridge.validate_repair if validate else self.bridge.run_case
        raw = function(case_id, directory, destination)
        run = raw.get("run", raw)
        run = dict(run)
        after = production_hashes(directory, ["src"])
        if (run.get("case_id") != case_id or run.get("group") != "all"
                or run.get("expected_check_count") != manifest["expected_check_count"]
                or run.get("editable_sha256_before") != before or run.get("editable_sha256_after") != after):
            raise ValueError("protected run tested another case, subset, or source identity")
        run["editable_sha256_before"], run["editable_sha256_after"] = before, after
        run["source_stable"] = run.get("source_stable") is True and before == after
        run["protected_files_ok"] = (run.get("protected_files_ok") is True
            and protected_before == manifest["protected_sha256"] == protected_hashes(directory, ["src"]))
        if run.get("fixture_set_identity") != self.expected_identity:
            raise ValueError("run provenance differs from the frozen historical fixture set")
        run["bridge_run_sha256"] = run.get("run_sha256")
        run["stdout"] = run.get("stdout", "") + run.get("stderr", "")
        run["run_sha256"] = hashlib.sha256(run["stdout"].encode()).hexdigest()
        retain_lab_audit(directory, run)
        if not validate:
            return run
        calls = [row for row in run["execution_audit"]["reports"] if row.get("when") == "call"]
        passed = bool(raw.get("passed", raw.get("accepted")) is True and run.get("exit_code") == 0
            and run["source_stable"] and run["protected_files_ok"] and protected_checks_complete(run)
            and all(row.get("outcome") == "passed" for row in calls))
        return {"passed": passed, "integrity_ok": run["protected_files_ok"], "run": run,
            "source": "author_bridge_full_protected_rerun", "outside_model": True}

    def run_case(self, directory):
        return self._run(directory, validate=False)

    def validate_repair(self, directory):
        return self._run(directory, validate=True)

    def prepare_case_evidence(self, directory, observed):
        return {"log": observed["stdout"], "exit_code": observed["exit_code"],
            "evidence_source": "actual_broken_historical_test_observation"}


def load_lab(profiles_path):
    """Explicit root-only bridge loading; never called by candidate construction."""
    _, paths = _input_paths(profiles_path)
    safe = _json(paths["safe_freeze"])
    if _digest(paths["bridge"]) != safe["bridge_code_sha256"]:
        raise ValueError("root-owned bridge source changed before import")
    key = "natural_transfer_root_bridge_" + _digest(paths["bridge"])[:16]
    spec = importlib.util.spec_from_file_location(key, paths["bridge"])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return NaturalLab(module, expected_identity=safe["fixture_set_identity"],
        expected_counts={row["id"]: row["all_count"] for row in safe["descriptor"]["cases"]})


async def workflow_bridge(evidence, *, project, manifest, work_dir, config_path, endpoint,
                          provider=None, apply=True, context_policy="v6"):
    if context_policy != "v6" or apply is not True:
        raise ValueError("natural assisted treatment is exactly the frozen V6 --apply workflow")
    tests = list(manifest["failing_tests"])
    regressions = list(manifest["regression_tests"])
    report = await _workflow_module().run_triage_v6(project, tests, regressions, work_dir, config_path,
        capture_failure_text=True, apply=True, provider=provider, endpoint=endpoint,
        max_context_chars=24000, allowed_production_roots=["src"], context_policy="v6")
    generator = _generator_module()
    if (report.get("workflow_version") != _workflow_module().VERSION
            or report.get("edit_protocol") != generator.VERSION
            or report.get("generation_module") != generator.__name__
            or report.get("generation_specification") != generator.specification()):
        raise SelectedProtocolError("Natural workflow returned a different selected line protocol or generator specification", report)
    report["measurement_test_args"] = {"failing_tests": tests, "regression_tests": regressions}
    retain_context(report, work_dir=Path(work_dir))
    return report
