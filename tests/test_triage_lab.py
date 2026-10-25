import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest

from benchmarks import run_triage, triage_lab as lab


@pytest.fixture(scope="module")
def replayed():
    root = lab.LAB_ROOT / ("test-proof-" + uuid4().hex[:12])
    root.mkdir(parents=True)
    proof = {"suite": "decilist-triage-v1", "cases": []}
    for case in lab.load_catalog()["cases"]:
        proof["cases"].append(lab.verify_fixture(case["id"], root / case["id"]))
    (root / "proof.json").write_bytes((json.dumps(proof, indent=2) + "\n").encode("utf-8"))
    return root, proof


def fresh(case_id):
    return Path(lab.prepare_case(case_id, lab.LAB_ROOT / ("test-case-" + uuid4().hex[:12]))["case_dir"])


def test_catalog_separates_history_seeds_and_controls():
    catalog = lab.load_catalog()
    assert len(catalog["cases"]) == 10
    assert sum(case["role"] == "regression" for case in catalog["cases"]) == 8
    assert sum(case["provenance"] == "historical_revert" for case in catalog["cases"]) >= 2
    assert {case["id"] for case in catalog["cases"] if case["split"] == "dev"} == {"t01", "t05", "t07", "t08"}
    assert catalog["matched_study_pair"]["case_ids"] == ["t05", "t06"]
    for case in catalog["cases"]:
        assert "# seeded" not in json.dumps(case["mutation"])
    assert catalog["policy"] == {"local_only": True, "min_confidence": .7, "timeout_ms": 30000}


@pytest.mark.parametrize("case_id", [f"t{i:02}" for i in range(1, 11)])
def test_actual_baseline_mutation_and_reference_repair(case_id, replayed):
    root, proof = replayed
    result = next(row for row in proof["cases"] if row["case_id"] == case_id)
    assert result["verified"], result
    assert result["baseline"]["exit_code"] == 0
    assert "3 passed" in result["baseline"]["stdout"]
    assert result["reference_repair"]["passed"]
    assert "3 passed" in result["reference_repair"]["run"]["stdout"]
    if case_id == "t09":
        assert result["mutation"]["exit_code"] == 0
    else:
        assert result["mutation"]["exit_code"] in (1, 2)
        if case_id == "t08":
            assert "ModuleNotFoundError" in result["mutation"]["stdout"]
        else:
            assert "failed" in result["mutation"]["stdout"]
    assert result["mutation"]["source_stable"]
    assert not any("C:\\Users\\" in run["stdout"] for run in (result["baseline"], result["mutation"]))
    assert (root / case_id / result["mutation"]["log_path"]).is_file()


def test_neutral_manifest_contains_no_oracle_and_uses_isolated_source(replayed):
    root, _ = replayed
    manifest = json.loads((root / "t05" / lab.MANIFEST_PATH).read_text())
    assert set(manifest) == {"schema_version", "case_id", "base_revision", "editable_paths", "protected_sha256", "test_command"}
    assert manifest["editable_paths"] == ["packages/python/src/decilist/engine.py"]
    assert lab.TEST_PATH in manifest["protected_sha256"]
    assert "fixture must import isolated source" in (root / "t05" / lab.TEST_PATH).read_text()
    assert not (root / "t05" / "benchmarks/triage_cases.json").exists()
    assert not (root / "t05" / ".git").exists()


def test_preparation_rejects_checkout_and_existing_paths(replayed):
    with pytest.raises(ValueError, match="outside the checkout|strictly inside"):
        lab.prepare_case("t05", lab.REPO_ROOT / "forbidden-fixture")
    with pytest.raises(ValueError, match="already exists"):
        lab.prepare_case("t05", replayed[0] / "t05")
    with pytest.raises(ValueError, match="unknown case"):
        lab.prepare_case("unknown", lab.LAB_ROOT / ("unused-" + uuid4().hex))


def test_test_or_manifest_edits_cannot_redefine_protected_hashes():
    directory = fresh("t05")
    (directory / lab.TEST_PATH).write_text("def test_fake(): assert True\n")
    manifest_path = directory / lab.MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text())
    manifest["protected_sha256"][lab.TEST_PATH] = lab.digest((directory / lab.TEST_PATH).read_bytes())
    manifest_path.write_text(json.dumps(manifest))
    result = lab.validate_repair(directory)
    assert not result["passed"] and not result["integrity_ok"]
    assert result["run"]["exit_code"] is None
    assert "case manifest changed" in result["run"]["integrity_issues"]


def test_only_editable_production_files_may_change():
    directory = fresh("t06")
    lab._reference_repair(directory)
    assert lab.validate_repair(directory)["passed"]
    (directory / "packages/python/src/decilist/injected.py").write_text("pass\n")
    result = lab.validate_repair(directory)
    assert not result["passed"]
    assert any("unexpected file" in issue for issue in result["run"]["integrity_issues"])


def test_edit_during_run_cannot_be_claimed_as_current_passing_repair(monkeypatch):
    directory = fresh("t05")
    lab._reference_repair(directory)
    actual_run = lab.subprocess.run
    editable = directory / "packages/python/src/decilist/engine.py"
    def concurrent_edit(*args, **kwargs):
        completed = actual_run(*args, **kwargs)
        editable.write_bytes(editable.read_bytes() + b"\n")
        return completed
    monkeypatch.setattr(lab.subprocess, "run", concurrent_edit)
    result = lab.validate_repair(directory)
    assert result["run"]["exit_code"] == 0
    assert not result["run"]["source_stable"]
    assert not result["passed"]


def test_shared_preparation_excludes_oracles_and_stale_source(replayed):
    root, proof = replayed
    result = next(row for row in proof["cases"] if row["case_id"] == "t05")
    evidence = lab.prepare_case_evidence(root / "t05", result["mutation"])
    assert evidence["exit_code"] == 1
    assert {"acceptable_routes", "provenance", "reference_repair", "mutation"}.isdisjoint(evidence)
    assert run_triage.prepare_fixture_evidence(lab.get_case("t05"), result, root / "t05") == evidence
    editable = root / "t05/packages/python/src/decilist/engine.py"
    old = editable.read_bytes()
    try:
        editable.write_bytes(old + b"\n")
        stale = lab.prepare_case_evidence(root / "t05", result["mutation"])
        assert stale["references"] == [] and "source_context_omitted" in stale
    finally:
        editable.write_bytes(old)


def test_controls_are_separate_and_repeats_do_not_inflate_quality():
    base = {"case_id":"x", "role":"regression", "status":"suggested", "route":"application_logic",
            "acceptable_routes":["application_logic"], "assistance_wall_ms":1, "repetition":1}
    rows = [base, {**base, "repetition":2}, {**base, "case_id":"y", "route":"contract_validation"},
            {**base, "case_id":"z", "status":"needs_review", "route":"manual_review"},
            {**base, "case_id":"control", "role":"passing_control", "route":"contract_validation"}]
    metrics = run_triage.summarize_rows(rows)
    assert metrics["regression_cases"] == 3 and metrics["regression_attempts"] == 4
    assert metrics["correct_accepted_next_checks"] == 1
    assert metrics["coverage"] == pytest.approx(2/3)
    assert metrics["accuracy_all"] == pytest.approx(1/3)
    assert metrics["accuracy_accepted"] == .5
    assert [row["case_id"] for row in metrics["wrong_accepted"]] == ["y"]
    assert metrics["controls"][0]["specific_next_check_suggested"]
    assert metrics["human_minutes_saved"] is None


def test_error_pattern_baseline_is_transparent_and_can_abstain():
    assert run_triage.baseline_route({"log":"ModuleNotFoundError: No module named example", "references":[]}) == "dependency_environment"
    assert run_triage.baseline_route({"log":"AssertionError: policy threshold is inclusive", "references":[]}) == "decision_policy"
    assert run_triage.baseline_route({"log":"3 passed", "references":[]}) == "manual_review"
    assert run_triage.BASELINE_NAME == "ordered-error-patterns-v1"


def test_freeze_matches_source_and_diagnosis_contract():
    freeze = lab.verify_freeze()
    assert freeze["diagnosis_contract_sha256"] == run_triage.canonical_hash(run_triage.diagnosis_contract())
    assert "packages/python/src/decilist/triage.py" in freeze["files_sha256"]
    assert "packages/python/src/decilist/triage_evidence.py" in freeze["files_sha256"]
