import asyncio
import json
from pathlib import Path

import pytest

from benchmarks import triage_prompt_ablation as ablation

REPO = Path(__file__).resolve().parents[1]
ARCHIVE = REPO / "reports/triage-v2-ablation"
PREVIOUS = REPO / "reports/triage-machine-2026-10-26.json"


def test_all_original_state_hashes_and_factor_constants_are_reproducible_without_inference():
    original = json.loads((ARCHIVE / "ablation-results.json").read_bytes())
    spec = json.loads((ARCHIVE / "ablation-spec.json").read_bytes())
    cases = {row["case_id"]: row for row in ablation.select_cases(json.loads(PREVIOUS.read_bytes()))}
    variants = {row["id"]: row for row in ablation.variants()}
    assert list(variants.values()) == spec["variants"]
    assert ablation.SHORT_INSTRUCTIONS == spec["short_instructions"]
    assert ablation.SHORT_ROUTES == spec["short_routes"]
    assert len(original["rows"]) == 64
    for row in original["rows"]:
        state, _, _ = ablation.model_input(cases[row["case_id"]], variants[row["variant"]])
        assert ablation.digest(state.encode("utf-8")) == row["state_sha256"]
        assert len(state) == row["state_chars"]
    case = {"evidence": {"log": "AssertionError: failed"}, "acceptable_routes": ["DO_NOT_SEND_GRADING_LABEL"]}
    assert "DO_NOT_SEND_GRADING_LABEL" not in ablation.model_input(case, ablation.variants()[0])[0]


def test_prepare_only_never_constructs_client_and_refuses_existing_directory(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("prepare-only must never create an inference client")
    monkeypatch.setattr(ablation, "Client", forbidden)
    folder = tmp_path / "new-results"
    result = asyncio.run(ablation.run(PREVIOUS, folder, url="http://127.0.0.1:8765", prepare_only=True))
    assert result == {"mode":"prepare_only", "planned_calls":64, "model_calls":0}
    spec = json.loads((folder / "ablation-spec.json").read_bytes())
    assert spec["diagnostic_policy"] == {"local_only":True, "min_confidence":0, "timeout_ms":30000}
    assert spec["production_threshold"] == .7
    before = {path.name:path.read_bytes() for path in folder.iterdir()}
    with pytest.raises(FileExistsError):
        asyncio.run(ablation.run(PREVIOUS, folder, url="http://127.0.0.1:8765", prepare_only=True))
    assert {path.name:path.read_bytes() for path in folder.iterdir()} == before


def test_archived_summaries_and_readable_transform_retain_original_semantics():
    rows = json.loads((ARCHIVE / "ablation-results.json").read_bytes())["rows"]
    summary = ablation.summarize_rows(rows)
    assert [row["top_acceptable"] for row in summary] == [6,5,7,5,7,3,7,3]
    assert [row["suggestions_at_0_7"] for row in summary] == [1,2,1,1,1,1,1,1]
    assert all(row["wrong_suggestions"] == 0 for row in summary)
    assert ablation.compact({"log":"=== separator\n\nE AssertionError: failed\nE AssertionError: failed\n", "references":[]}) == "Observed failing test log:\nE AssertionError: failed"
    assert len(ablation.compact({"log":"x" * 9000, "references":[]})) == 6500
