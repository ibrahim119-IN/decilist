"""Evaluator tests. Never include this file or the private catalog in model input."""
from collections import Counter
import json
from pathlib import Path
from uuid import uuid4

import pytest

from benchmarks import triage_v4_lab as lab


@pytest.fixture(scope="module")
def replayed():
    root = lab.LAB_ROOT / ("unit-proof-" + uuid4().hex[:12])
    proof = lab.verify_all(root)
    return root, proof


def fresh(case_id="d01", *, work_root=None):
    root = Path(work_root) if work_root is not None else lab.LAB_ROOT
    return Path(lab.prepare_case(case_id, root / ("unit-" + uuid4().hex[:12]), work_root=work_root)["case_dir"])


def test_membership_source_pin_and_no_difficulty_claim():
    catalog = lab.load_catalog()
    assert catalog["version"] == 4
    assert catalog["base_revision"] == "1c2e0ff46cca171c717b098b97c0cf768088beef"
    assert Counter(row["split"] for row in catalog["cases"]) == {
        "development": 2, "evaluation": 6, "reserved": 4, "controls": 2}
    assert lab.development_case_ids() == ("d01", "d02")
    assert lab.trial_case_ids() == ("r01", "r02", "r03", "r04")
    assert all(lab.get_case_role(case_id) == "trial_regression" for case_id in lab.trial_case_ids())
    assert "no guaranteed speedup" in catalog["purpose"]
    assert "does not establish human difficulty" in catalog["difficulty_policy"]
    for case in catalog["cases"]:
        surface = case["investigation_surface"]
        assert len({path for path, _ in surface}) >= 2
        assert sum(len(symbols) for _, symbols in surface) >= 5
        assert "@pytest.mark.parametrize" in case["case_tests"]
        assert "@pytest.mark.parametrize" in case["regression_tests"]
        assert case["repair_invariants"] and case["root_cause_discriminating_facts"]
        assert not case["structural_indicators"]["human_difficulty_established"]
    with pytest.raises(ValueError, match="unknown"):
        lab.get_case_role("unknown")
    with pytest.raises(ValueError, match="unknown split"):
        lab.case_ids("unknown")


@pytest.mark.parametrize("case_id", ["d01", "d02", "e01", "e02", "e03", "e04", "e05", "e06",
                                      "r01", "r02", "r03", "r04", "c01", "c02"])
def test_every_candidate_has_observed_pass_fail_repair(case_id, replayed):
    root, proof = replayed
    row = next(row for row in proof["cases"] if row["case_id"] == case_id)
    assert row["verified"], {key: row.get(key) for key in ("case_id", "error", "baseline", "mutation", "reference_repair")}
    assert row["candidate_status"] == "executable_regression"
    assert row["baseline"]["exit_code"] == 0
    assert row["mutation"]["exit_code"] in (1, 2)
    assert row["reference_repair"]["passed"]
    assert "6 passed" in row["baseline"]["stdout"] or "12 passed" in row["baseline"]["stdout"]
    assert row["mutation"]["source_stable"] and row["mutation"]["protected_files_ok"]
    assert (root / case_id / row["mutation"]["log_path"]).is_file()


def test_proof_retains_source_snapshots_and_development_view(replayed):
    root, proof = replayed
    assert proof["candidate_count"] == 14 and proof["verified_count"] == 14 and proof["all_verified"]
    assert (root / "catalog_snapshot.json").read_bytes() == lab.CATALOG.read_bytes()
    assert (root / "lab_snapshot.py").read_bytes() == Path(lab.__file__).read_bytes()
    assert len(json.loads((root / "proof.json").read_text())["cases"]) == 14
    development = json.loads((root / "development-proof.json").read_text())
    assert {row["case_id"] for row in development["cases"]} == {"d01", "d02"}


def test_manifests_are_neutral_and_production_roots_are_broad(replayed):
    root, proof = replayed
    wanted = {"schema_version", "suite", "case_id", "allowed_production_roots", "failing_tests", "regression_tests", "test_command"}
    forbidden = {"mutations", "private_mechanism", "repair_invariants", "minimal_repair_invariants", "inspection_targets",
                 "root_cause_discriminating_facts", "acceptable_routes", "candidate_correct_position", "reference_repair"}
    for row in proof["cases"]:
        directory = root / row["case_id"]
        manifest = json.loads((directory / lab.MANIFEST_PATH).read_text())
        assert set(manifest) == wanted
        assert manifest["allowed_production_roots"] == list(lab.PRODUCTION_ROOTS)
        assert manifest["failing_tests"] == [lab.TEST_PATH]
        assert manifest["regression_tests"] == [lab.REGRESSION_TEST_PATH]
        assert not (directory / "benchmarks").exists()
        evidence = lab.prepare_case_evidence(directory, row["mutation"])
        assert forbidden.isdisjoint(evidence)
        if row["case_id"] in lab.trial_case_ids():
            with pytest.raises(ValueError, match="reserved blind"):
                lab.prepare_machine_evidence(directory, row["mutation"])
    directory = root / "d01"
    adjacent_production = directory / "vendor/httpx/_status_codes.py"
    original = adjacent_production.read_bytes()
    try:
        adjacent_production.write_bytes(original + b"\n")
        assert lab._integrity(directory, None)[0]
    finally:
        adjacent_production.write_bytes(original)


def test_controls_preserve_actual_failure_and_visibility_policy(replayed):
    root, proof = replayed
    rows = {row["case_id"]: row for row in proof["cases"]}
    missing = lab.prepare_machine_evidence(root / "c01", rows["c01"]["mutation"])
    assert missing["exit_code"] == 1 and missing["references"] == [] and missing["truncated"]
    mixed = lab.prepare_machine_evidence(root / "c02", rows["c02"]["mutation"])
    assert mixed["exit_code"] == 1
    assert "test_request_records" in mixed["log"] and "test_second_provider_records" in mixed["log"]


def test_test_and_manifest_tampering_cannot_redefine_protection():
    directory = fresh()
    (directory / lab.TEST_PATH).write_text("def test_bypass(): assert True\n")
    manifest = json.loads((directory / lab.MANIFEST_PATH).read_text())
    manifest["failing_tests"] = []
    (directory / lab.MANIFEST_PATH).write_text(json.dumps(manifest))
    outcome = lab.validate_repair(directory)
    assert not outcome["passed"] and not outcome["integrity_ok"]
    assert outcome["run"]["exit_code"] is None
    assert any("protected file changed" in issue for issue in outcome["run"]["integrity_issues"])


def test_work_root_is_configurable_and_checkout_is_rejected(tmp_path):
    work_root = tmp_path / "portable-work"
    directory = fresh(work_root=work_root)
    assert directory.is_relative_to(work_root)
    assert lab._integrity(directory, work_root)[0]
    assert not lab._integrity(directory, None)[0]
    with pytest.raises(ValueError, match="outside the checkout"):
        lab.prepare_case("d01", lab.REPO_ROOT / "forbidden", work_root=lab.REPO_ROOT)
    with pytest.raises(ValueError, match="already exists"):
        lab.prepare_case("d01", directory, work_root=work_root)


def test_external_source_and_both_licenses_are_pinned():
    catalog = lab.load_catalog()
    project, files = catalog["projects"]["httpx"], lab._base_files()
    assert project["version"] == "0.28.1" and project["license"] == "BSD-3-Clause"
    assert len(project["verified_modules"]) >= 7
    for relative, record in project["verified_modules"].items():
        data = files[relative]
        assert lab.digest(data) == record["sha256"]
        assert __import__("hashlib").sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == record["git_blob_sha"]
    assert lab.digest(files["vendor/LICENSE.httpx.md"]) == project["license_sha256"]
    assert lab.digest(files["LICENSE.decilist"]) == catalog["projects"]["decilist"]["license_sha256"]


def test_wrong_dependency_version_is_rejected_without_installing(monkeypatch):
    import httpx
    lab._base_files.cache_clear()
    monkeypatch.setattr(httpx, "__version__", "unverified")
    try:
        with pytest.raises(ValueError, match="already installed httpx==0.28.1"):
            lab._base_files()
    finally:
        lab._base_files.cache_clear()


def test_failed_candidate_is_retained_instead_of_filtered(monkeypatch, tmp_path):
    def failed_candidate(case_id, destination, **kwargs):
        raise ValueError("ineffective candidate observation")
    monkeypatch.setattr(lab, "verify_fixture", failed_candidate)
    proof = lab.verify_all(tmp_path / "kept", ids=("d01", "d02"), work_root=tmp_path)
    assert proof["candidate_count"] == 2 and proof["verified_count"] == 0 and not proof["all_verified"]
    assert [row["case_id"] for row in proof["cases"]] == ["d01", "d02"]
    assert all(row["candidate_status"] == "retained_candidate_failure" for row in proof["cases"])
    assert (tmp_path / "kept/catalog_snapshot.json").is_file()


def test_freeze_is_specific_and_covers_all_source():
    frozen = lab.verify_freeze()
    assert frozen["suite"] == "testtriage-repair-v4"
    assert frozen["development_ids"] == ["d01", "d02"]
    assert len(frozen["evaluation_ids"]) == 6 and len(frozen["reserved_trial_ids"]) == 4
    assert set(frozen["files_sha256"]) == {"benchmarks/triage_v4_lab.py", "benchmarks/triage_v4_cases.json", "tests/test_triage_v4_lab.py"}
    assert frozen["fixture_proof"]["all_verified"]
