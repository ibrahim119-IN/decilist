"""Synthetic routing/provenance tests only; no natural fixtures or models."""
from copy import deepcopy

import pytest

from benchmarks import natural_candidate as candidate
from benchmarks import natural_trial
from benchmarks import run_triage_v6 as matrix
from benchmarks import triage_v6_trial as synthetic_trial
from decilist import triage_lines_v6 as selected
from decilist import triage_line_hypothesis_v6 as line_generator
from decilist import triage_v6 as legacy
from decilist import triage_hypothesis_v6 as legacy_generator
from test_natural_measurement import fixture as natural_fixture, prepare as prepare_natural, Provider as NaturalProvider, abstain as natural_abstain
from test_v6_measurement_support import make_fixture, Provider, abstain
from test_triage_v6_trial import prepare as prepare_synthetic


def selected_report():
    return {"schema_version": 6, "state": "unverified", "context_policy": "v6", "attempts": [],
        "workflow_version": selected.VERSION, "edit_protocol": line_generator.VERSION,
        "generation_module": line_generator.__name__, "generation_specification": line_generator.specification(),
        "apply": {"requested": True, "applied": False}}


def test_natural_and_matrix_select_the_same_clean_line_workflow_and_actual_specification(natural_fixture):
    assert candidate._workflow_module() is selected
    assert candidate._generator_module() is line_generator
    assert matrix._workflow_module() is selected
    assert matrix._generator_module() is line_generator
    frozen = candidate.candidate_snapshot(natural_fixture["profiles"])
    specification = frozen["specification"]
    assert specification["workflow_version"] == selected.VERSION
    assert specification["generator"] == line_generator.specification()
    assert specification["generator"] != legacy_generator.specification()
    assert specification["edit_protocol"] == line_generator.VERSION
    assert specification["generation_module"] == "decilist.triage_line_hypothesis_v6"
    assert natural_fixture["bridge"].prepared == natural_fixture["bridge"].runs == []


async def test_natural_dispatches_selected_workflow_with_unchanged_protocol_arguments(natural_fixture, monkeypatch):
    calls = []
    async def selected_stub(project, tests, regressions, work_dir, config_path, **kwargs):
        calls.append((project, tests, regressions, work_dir, config_path, kwargs))
        return selected_report()
    async def legacy_must_not_be_directly_selected(*args, **kwargs):
        pytest.fail("natural measurement selected the legacy workflow directly")
    monkeypatch.setattr(selected, "run_triage_v6", selected_stub)
    monkeypatch.setattr(legacy, "run_triage_v6", legacy_must_not_be_directly_selected)
    manifest = {"failing_tests": ["-q", "tests/test_stub.py::test_observed"],
        "regression_tests": ["-q", "tests/test_stub.py::test_existing"]}
    provider = object()
    result = await candidate.workflow_bridge({"truncated": True}, project=natural_fixture["repo"] / "unused-project",
        manifest=manifest, work_dir=natural_fixture["repo"] / "unused-copy", config_path=natural_fixture["config"],
        endpoint="http://127.0.0.1:8792", provider=provider, apply=True, context_policy="v6")
    assert len(calls) == 1
    assert calls[0][1:3] == (manifest["failing_tests"], manifest["regression_tests"])
    kwargs = calls[0][-1]
    assert kwargs == {"capture_failure_text": True, "apply": True, "provider": provider,
        "endpoint": "http://127.0.0.1:8792", "max_context_chars": 24000,
        "allowed_production_roots": ["src"], "context_policy": "v6"}
    assert result["generation_specification"] == line_generator.specification()
    assert natural_fixture["bridge"].prepared == []


@pytest.mark.parametrize("field", ["workflow_version", "edit_protocol", "generation_module", "generation_specification"])
async def test_natural_refuses_legacy_or_mismatched_generation_provenance(natural_fixture, monkeypatch, field):
    value = selected_report()
    value[field] = {"workflow_version": legacy.VERSION, "edit_protocol": legacy_generator.VERSION,
        "generation_module": legacy_generator.__name__, "generation_specification": legacy_generator.specification()}[field]
    async def stub(*args, **kwargs):
        return deepcopy(value)
    monkeypatch.setattr(selected, "run_triage_v6", stub)
    with pytest.raises(ValueError, match="different selected line protocol") as captured:
        await candidate.workflow_bridge({}, project=natural_fixture["repo"] / "unused-project",
            manifest={"failing_tests": ["tests/test_stub.py"], "regression_tests": ["tests/test_existing.py"]},
            work_dir=natural_fixture["repo"] / "unused-copy", config_path=natural_fixture["config"],
            endpoint="http://127.0.0.1:8792", apply=True)
    assert captured.value.workflow_report == value
    assert natural_fixture["bridge"].prepared == []


async def test_mismatched_workflow_output_is_retained_in_the_timed_hint_record(natural_fixture, monkeypatch):
    directory, _ = await prepare_natural(natural_fixture, 2)
    natural_trial.start_trial(directory, lab=natural_fixture["lab"])
    value = selected_report()
    value["generation_specification"] = legacy_generator.specification()
    value["attempts"] = [{"generation": {"raw_output": "STUB_OUTPUT_RETAINED"}}]
    async def selected_stub(*args, **kwargs):
        return deepcopy(value)
    monkeypatch.setattr(selected, "run_triage_v6", selected_stub)
    report = await natural_trial.hint_trial(directory, provider_factory=NaturalProvider, lab=natural_fixture["lab"])
    assert report["state"] == "unavailable" and report["error"]["type"] == "SelectedProtocolError"
    assert report["attempts"][0]["generation"]["raw_output"] == "STUB_OUTPUT_RETAINED"
    entry = natural_trial._read(directory, natural_fixture["lab"])["hints"][0]
    assert entry["status"] == "error" and entry["workflow_returned_report"] is True


@pytest.mark.parametrize("override", [None, "explicit-caller-label"])
async def test_natural_hint_default_records_selected_version_and_preserves_override(natural_fixture, override):
    directory, _ = await prepare_natural(natural_fixture, 2)
    natural_trial.start_trial(directory, lab=natural_fixture["lab"])
    await natural_trial.hint_trial(directory, callback=natural_abstain, callback_version=override,
        provider_factory=NaturalProvider, lab=natural_fixture["lab"])
    entry = natural_trial._read(directory, natural_fixture["lab"])["hints"][0]
    assert entry["version"] == (selected.VERSION if override is None else override)


@pytest.mark.parametrize("override", [None, "explicit-synthetic-label"])
async def test_synthetic_hint_default_records_selected_version_and_preserves_override(tmp_path, monkeypatch, override):
    fixture = make_fixture(tmp_path, monkeypatch)
    fixture.workflow.VERSION = selected.VERSION
    monkeypatch.setattr(synthetic_trial, "_workflow_module", lambda: fixture.workflow)
    directory, _ = await prepare_synthetic(fixture, 2)
    synthetic_trial.start_trial(directory, lab=fixture.lab)
    await synthetic_trial.hint_trial(directory, callback=abstain, callback_version=override,
        provider_factory=Provider, lab=fixture.lab)
    entry = synthetic_trial._read(directory, fixture.lab)["hints"][0]
    assert entry["version"] == (selected.VERSION if override is None else override)
