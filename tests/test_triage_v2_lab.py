import json
from pathlib import Path
from uuid import uuid4

import pytest

from benchmarks import triage_v2_lab as lab


@pytest.fixture(scope="module")
def replayed():
    root = lab.LAB_ROOT / ("proof-" + uuid4().hex[:12])
    root.mkdir(parents=True)
    proof = {"suite": "testtriage-fresh-v2", "cases": []}
    for case in lab.load_catalog()["cases"]:
        proof["cases"].append(lab.verify_fixture(case["id"], root / case["id"]))
    (root / "proof.json").write_bytes((json.dumps(proof, indent=2) + "\n").encode("utf-8"))
    return root, proof


def fresh(case_id):
    manifest = lab.prepare_case(case_id, lab.LAB_ROOT / ("test-" + uuid4().hex[:12]))
    return Path(manifest["case_dir"])


def test_new_corpus_membership_is_disjoint_and_trials_are_reserved():
    catalog = lab.load_catalog()
    cases = catalog["cases"]
    assert len(cases) == 19
    assert not {case["id"] for case in cases} & {f"t{i:02}" for i in range(1, 11)}
    assert sum(case["role"] == "regression" and case["split"] == "dev" for case in cases) == 4
    assert sum(case["role"] == "regression" and case["split"] == "evaluation" for case in cases) == 8
    assert {case["id"] for case in cases if case["role"] == "trial_regression"} == {"a01", "a02", "b01", "b02"}
    assert sum(case["project"] == "httpx" and case["role"] == "regression" for case in cases) == 4
    assert sum(case["split"] == "controls" for case in cases) == 3
    assert len({case["family"] for case in cases if case["role"] == "regression"}) >= 4
    for case in cases:
        assert case["targeted_inspection_assertion"]
        assert all("seeded" not in mutation.get("new", "") for mutation in case["mutations"])


@pytest.mark.parametrize("case_id", [f"v{i:02}" for i in range(1, 13)] + ["a01", "a02", "b01", "b02", "c01", "c02", "c03"])
def test_actual_baseline_failure_and_reference_repair(case_id, replayed):
    root, proof = replayed
    result = next(row for row in proof["cases"] if row["case_id"] == case_id)
    assert result["verified"], {"case_id": case_id, "baseline": result["baseline"], "mutation": result["mutation"]}
    assert result["baseline"]["exit_code"] == 0
    assert result["mutation"]["exit_code"] in (1, 2)
    assert result["reference_repair"]["passed"]
    wanted = "6 passed" if case_id == "c01" else "3 passed"
    assert wanted in result["baseline"]["stdout"]
    assert wanted in result["reference_repair"]["run"]["stdout"]
    assert result["mutation"]["source_stable"]
    assert (root / case_id / result["mutation"]["log_path"]).is_file()


def test_external_project_source_and_license_match_verified_hashes(replayed):
    root, _ = replayed
    project = lab.load_catalog()["projects"]["httpx"]
    assert project["version"] == "0.28.1" and project["license"] == "BSD-3-Clause"
    source = root / "v09/vendor/httpx/_urls.py"
    # Its editable module has a seeded defect; the trusted baseline bytes retain
    # the separately verified upstream hash, rather than calling the mutant upstream.
    assert lab.digest(lab._base_files()["vendor/httpx/_urls.py"]) == project["urls_sha256"]
    assert lab.digest((root / "v09/vendor/LICENSE.httpx.md").read_bytes()) == project["license_sha256"]
    assert source.is_file()
    manifest = json.loads((root / "v09" / lab.MANIFEST_PATH).read_text())
    assert manifest["project"] == "httpx"
    assert "vendor/LICENSE.httpx.md" in manifest["protected_sha256"]


def test_trial_manifests_are_neutral_and_refused_by_machine_preparation(replayed):
    root, proof = replayed
    for case_id in ("a01", "a02", "b01", "b02"):
        manifest = json.loads((root / case_id / lab.MANIFEST_PATH).read_text())
        assert {"acceptable_routes", "mutations", "targeted_inspection_assertion", "provenance", "test_key"}.isdisjoint(manifest)
        assert not (root / case_id / "benchmarks/triage_v2_cases.json").exists()
        assert len(manifest["editable_paths"]) == 1
        run = next(row for row in proof["cases"] if row["case_id"] == case_id)["mutation"]
        with pytest.raises(ValueError, match="reserved blind trial"):
            lab.prepare_machine_evidence(root / case_id, run)
        evidence = lab.prepare_case_evidence(root / case_id, run)
        assert evidence["exit_code"] == 1
        assert {"acceptable_routes", "mutations", "targeted_inspection_assertion", "provenance"}.isdisjoint(evidence)


def test_control_visibility_is_explicit_and_based_on_observed_failures(replayed):
    root, proof = replayed
    by_id = {row["case_id"]: row for row in proof["cases"]}
    mixed = lab.prepare_machine_evidence(root / "c01", by_id["c01"]["mutation"])
    assert "boolean" in mixed["log"].lower() and "timeout" in mixed["log"].lower()
    ambiguous = lab.prepare_machine_evidence(root / "c02", by_id["c02"]["mutation"])
    assert ambiguous["log"] == "AssertionError" and ambiguous["truncated"]
    assert ambiguous["references"] == [] and ambiguous["exit_code"] == 1
    insufficient = lab.prepare_machine_evidence(root / "c03", by_id["c03"]["mutation"])
    assert insufficient["exit_code"] == 1 and insufficient["references"] == [] and insufficient["truncated"]


def test_test_or_manifest_edits_cannot_weaken_validation():
    directory = fresh("v01")
    (directory / lab.TEST_PATH).write_text("def test_fake(): assert True\n")
    manifest_path = directory / lab.MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text())
    manifest["protected_sha256"][lab.TEST_PATH] = lab.digest((directory / lab.TEST_PATH).read_bytes())
    manifest_path.write_text(json.dumps(manifest))
    outcome = lab.validate_repair(directory)
    assert not outcome["passed"] and not outcome["integrity_ok"]
    assert outcome["run"]["exit_code"] is None


def test_external_only_allowlist_and_protected_license_are_enforced():
    directory = fresh("b01")
    lab._reference_repair(directory)
    assert lab.validate_repair(directory)["passed"]
    (directory / "vendor/LICENSE.httpx.md").write_text("changed\n")
    outcome = lab.validate_repair(directory)
    assert not outcome["passed"]
    assert any("LICENSE.httpx.md" in issue for issue in outcome["run"]["integrity_issues"])


def test_workspace_is_separate_and_checkout_is_never_a_destination():
    assert lab.LAB_ROOT.name == "triage-v2-lab"
    with pytest.raises(ValueError):
        lab.prepare_case("v01", lab.REPO_ROOT / "forbidden")


def test_another_installed_httpx_version_is_rejected_without_install(monkeypatch):
    import httpx
    lab._base_files.cache_clear()
    monkeypatch.setattr(httpx, "__version__", "different-version")
    try:
        with pytest.raises(ValueError, match="already installed httpx==0.28.1"):
            lab._base_files()
    finally:
        lab._base_files.cache_clear()


def test_external_source_hash_drift_is_rejected_without_touching_install(monkeypatch):
    import httpx
    target = (Path(httpx.__file__).resolve().parent / "_urls.py").resolve()
    actual_read = Path.read_bytes
    def changed_read(path):
        data = actual_read(path)
        return data + b"\n" if path.resolve() == target else data
    lab._base_files.cache_clear()
    monkeypatch.setattr(Path, "read_bytes", changed_read)
    try:
        with pytest.raises(ValueError, match="installed HTTPX source differs"):
            lab._base_files()
    finally:
        lab._base_files.cache_clear()


def test_new_freeze_covers_catalog_source_tests_and_external_snapshot():
    freeze = lab.verify_freeze()
    assert freeze["suite"] == "testtriage-fresh-v2"
    assert freeze["files_sha256"]["benchmarks/triage_v2_cases.json"] == lab.digest(lab.CATALOG.read_bytes())
    assert set(freeze["reserved_trial_ids"]) == {"a01", "a02", "b01", "b02"}
    assert "vendor/httpx/_urls.py" in freeze["baseline_files_sha256"]
