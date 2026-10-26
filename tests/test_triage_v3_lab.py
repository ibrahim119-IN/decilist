from collections import Counter
import json
from pathlib import Path
from uuid import uuid4

import pytest

from benchmarks import triage_v3_lab as lab


@pytest.fixture(scope="module")
def replayed():
    root = lab.LAB_ROOT / ("proof-" + uuid4().hex[:12])
    root.mkdir(parents=True)
    proof = {"suite": "testtriage-structured-v3", "cases": []}
    for case in lab.load_catalog()["cases"]:
        proof["cases"].append(lab.verify_fixture(case["id"], root / case["id"]))
    (root / "proof.json").write_bytes((json.dumps(proof, indent=2) + "\n").encode("utf-8"))
    # A separate development artifact avoids opening evaluation/trial outcomes
    # while selecting an assistant from the two declared development cases.
    dev_ids = {case["id"] for case in lab.load_catalog()["cases"] if case["split"] == "dev"}
    development = {"suite": proof["suite"], "cases": [case for case in proof["cases"] if case["case_id"] in dev_ids]}
    (root / "development-proof.json").write_bytes((json.dumps(development, indent=2) + "\n").encode("utf-8"))
    return root, proof


def fresh(case_id):
    return Path(lab.prepare_case(case_id, lab.LAB_ROOT / ("test-" + uuid4().hex[:12]))["case_dir"])


def test_new_membership_and_structural_design_are_explicit():
    catalog = lab.load_catalog()
    cases = catalog["cases"]
    assert len(cases) == 12
    regressions = [case for case in cases if case["role"] == "regression"]
    assert len(regressions) == 8
    assert {case["id"] for case in regressions if case["split"] == "dev"} == {"h01", "h05"}
    assert sum(case["split"] == "evaluation" for case in regressions) == 6
    assert sum(case["split"] == "controls" for case in cases) == 2
    assert lab.trial_case_ids() == ("r01", "r02")
    assert all(lab.get_case_role(case_id) == "trial_regression" for case_id in lab.trial_case_ids())
    with pytest.raises(ValueError, match="unknown"):
        lab.get_case_role("unknown")
    for case in regressions:
        indicators = case["structural_indicators"]
        assert indicators["minimum_observation_phases"] >= 3
        assert indicators["minimum_relevant_callable_transitions"] >= 3
        assert indicators["shared_state_or_reconstruction_invariant"]
        assert case["root_cause_discriminating_facts"] and case["minimal_repair_invariants"]
        assert case["inspection_targets"]
    assert "not a guaranteed difficulty" in catalog["purpose"]


def test_private_ranking_oracles_are_balanced_and_specific():
    cases = [case for case in lab.load_catalog()["cases"] if case["role"] == "regression"]
    assert Counter(case["candidate_correct_position"] for case in cases) == {1: 2, 2: 2, 3: 2, 4: 2}
    for case in cases:
        candidates = case["inspection_candidates"]
        assert len(candidates) == 4 and {row["id"] for row in candidates} == set("ABCD")
        assert sum(row["kind"] == "review" for row in candidates) == 1
        selected = next(row for row in candidates if row["id"] in case["acceptable_candidate_ids"])
        assert selected["kind"] == "inspect"
        assert any(selected["path"] == target["path"] and selected["symbol"] in target["symbols"]
                   for target in case["inspection_targets"])
        assert all("seeded" not in mutation["new"] for mutation in case["mutations"])


@pytest.mark.parametrize("case_id", [f"h{i:02}" for i in range(1, 9)] + ["r01", "r02", "c01", "c02"])
def test_observed_original_failure_and_reference_repair(case_id, replayed):
    root, proof = replayed
    result = next(row for row in proof["cases"] if row["case_id"] == case_id)
    assert result["verified"], {"case_id": case_id, "baseline": result["baseline"], "mutation": result["mutation"]}
    assert result["baseline"]["exit_code"] == 0
    assert result["mutation"]["exit_code"] in (1, 2)
    assert result["reference_repair"]["passed"]
    wanted = "6 passed" if case_id == "c02" else "3 passed"
    assert wanted in result["baseline"]["stdout"]
    assert wanted in result["reference_repair"]["run"]["stdout"]
    assert result["mutation"]["source_stable"]
    assert (root / case_id / result["mutation"]["log_path"]).is_file()


def test_evidence_and_manifests_never_include_oracle_metadata(replayed):
    root, proof = replayed
    forbidden = {"acceptable_routes", "inspection_candidates", "acceptable_candidate_ids",
                 "root_cause_discriminating_facts", "minimal_repair_invariants", "mutations",
                 "candidate_correct_position", "inspection_targets", "targeted_inspection_assertion"}
    for observed in proof["cases"]:
        case_id = observed["case_id"]
        manifest = json.loads((root / case_id / lab.MANIFEST_PATH).read_text())
        assert forbidden.isdisjoint(manifest)
        assert not (root / case_id / "benchmarks/triage_v3_cases.json").exists()
        evidence = lab.prepare_case_evidence(root / case_id, observed["mutation"])
        assert forbidden.isdisjoint(evidence)
        if lab.get_case_role(case_id) == "trial_regression":
            with pytest.raises(ValueError, match="reserved blind"):
                lab.prepare_machine_evidence(root / case_id, observed["mutation"])


def test_controls_and_development_artifact_remain_separate(replayed):
    root, proof = replayed
    records = {row["case_id"]: row for row in proof["cases"]}
    missing = lab.prepare_machine_evidence(root / "c01", records["c01"]["mutation"])
    assert missing["exit_code"] == 1 and missing["references"] == [] and missing["truncated"]
    mixed = lab.prepare_machine_evidence(root / "c02", records["c02"]["mutation"])
    assert mixed["exit_code"] == 1
    assert "test_three_request_records" in mixed["log"] and "test_query_pipeline_result" in mixed["log"]
    development = json.loads((root / "development-proof.json").read_text())
    assert {row["case_id"] for row in development["cases"]} == {"h01", "h05"}


def test_protected_test_and_manifest_cannot_be_replaced():
    directory = fresh("h01")
    (directory / lab.TEST_PATH).write_text("def test_ok(): assert True\n")
    manifest_path = directory / lab.MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text())
    manifest["protected_sha256"][lab.TEST_PATH] = lab.digest((directory / lab.TEST_PATH).read_bytes())
    manifest_path.write_text(json.dumps(manifest))
    outcome = lab.validate_repair(directory)
    assert not outcome["passed"] and not outcome["integrity_ok"]
    assert outcome["run"]["exit_code"] is None


def test_external_snapshot_and_license_are_pinned():
    project = lab.load_catalog()["projects"]["httpx"]
    assert project["version"] == "0.28.1" and project["license"] == "BSD-3-Clause"
    baseline = lab._base_files()
    for path, details in project["verified_modules"].items():
        assert lab.digest(baseline[path]) == details["sha256"]
    assert lab.digest(baseline["vendor/LICENSE.httpx.md"]) == project["license_sha256"]


def test_wrong_httpx_version_is_rejected_without_an_install(monkeypatch):
    import httpx
    lab._base_files.cache_clear()
    monkeypatch.setattr(httpx, "__version__", "different-version")
    try:
        with pytest.raises(ValueError, match="already installed httpx==0.28.1"):
            lab._base_files()
    finally:
        lab._base_files.cache_clear()


def test_isolation_and_freeze_are_specific_to_v3():
    assert lab.LAB_ROOT.name == "triage-v3-lab"
    assert lab.BASE_REVISION == "34d0ce4459ad4b46143f8bb3d0cb4f6e45f277af"
    with pytest.raises(ValueError):
        lab.prepare_case("h01", lab.REPO_ROOT / "forbidden")
    freeze = lab.verify_freeze()
    assert freeze["suite"] == "testtriage-structured-v3"
    assert set(freeze["reserved_trial_ids"]) == {"r01", "r02"}
