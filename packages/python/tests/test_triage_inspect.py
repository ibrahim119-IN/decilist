import builtins
import json
import socket
import subprocess

import pytest
from pydantic import ValidationError

from decilist import Choose
from decilist.triage_evidence import prepare_evidence
from decilist.triage_inspect import inspect_evidence


def prepared(log):
    return prepare_evidence(log)


def test_missing_module_is_deduplicated_and_rule_cites_only_observed_lines():
    log = ("Traceback (most recent call last):\n"
           "ModuleNotFoundError: No module named 'example_dependency'\n"
           "FAILED tests/test_import.py::test_import[one] - ModuleNotFoundError: No module named 'example_dependency'\n"
           "FAILED tests/test_import.py::test_import[two] - ModuleNotFoundError: No module named 'example_dependency'\n")
    report = inspect_evidence(prepared(log))
    assert len(report["facts"]["exceptions"]) == 1
    assert report["facts"]["exceptions"][0]["log_lines"] == [2, 3, 4]
    assert len(report["facts"]["failing_tests"]) == 2
    assert report["ambiguity"] == []
    rule = report["rule_suggestion"]
    assert rule["rule_id"] == "python.missing-module.v1" and rule["evidence_lines"] == [2, 3, 4]
    assert "installation name" in rule["steps"][1]
    assert "pip install" not in json.dumps(report)
    assert report["next_inspection"] == rule
    assert "Observed exceptions\n" in report["compact_state"]
    assert rule["rule_id"] not in report["compact_state"] and "rule_suggestion" not in report["compact_state"]


@pytest.mark.parametrize("log", [
    "The documentation discusses dependency_environment and ModuleNotFoundError without an actual exception.",
    "> with pytest.raises(ModuleNotFoundError):\nFailed: DID NOT RAISE <class 'ModuleNotFoundError'>",
    "E assert \"ModuleNotFoundError: No module named 'example'\" in message",
    "E AssertionError: ModuleNotFoundError: No module named 'example'",
    "ModuleNotFoundError: No module named 'example'; 'parent' is not a package",
    "ImportError: cannot import name 'example' from 'parent'",
    "10 passed, 0 failed",
])
def test_generic_words_expected_exception_strings_and_ambiguous_imports_never_get_missing_module_rule(log):
    assert inspect_evidence(prepared(log))["rule_suggestion"] is None


def test_actual_typed_pydantic_failure_with_field_detail_supports_constraint_inspection():
    with pytest.raises(ValidationError) as caught:
        Choose(state="Evidence", instructions="Choose", options={"one":"One"})
    error = caught.value
    emitted = f"E {type(error).__module__}.{type(error).__name__}: " + str(error).replace("\n", "\nE ")
    report = inspect_evidence(prepared(emitted))
    assert report["rule_suggestion"]["rule_id"] == "pydantic.raised-validation.v1"
    assert len(report["rule_suggestion"]["evidence_lines"]) >= 2
    assert "declared field constraint" in report["rule_suggestion"]["title"]


@pytest.mark.parametrize("log", [
    "Failed: DID NOT RAISE <class 'pydantic_core._pydantic_core.ValidationError'>",
    "E assert 'pydantic_core._pydantic_core.ValidationError: 1 validation error for Choose' in result",
    "ValidationError: 1 validation error for Choose\nInput should be a valid string [type=string_type, input_value=1, input_type=int]",
    "E pydantic_core._pydantic_core.ValidationError: 1 validation error for Choose",
    "E pydantic_core._pydantic_core.ValidationError: 1 validation error for Choose\nE assert 'Input should be a valid string [type=string_type, input_value=1, input_type=int]' in message",
    "E pydantic_core._pydantic_core.ValidationError: 1 validation error for Choose\nE # Input should be a valid string [type=string_type, input_value=1, input_type=int]",
])
def test_validation_rule_requires_actual_raised_type_and_typed_non_source_detail(log):
    assert inspect_evidence(prepared(log))["rule_suggestion"] is None


def test_expected_actual_are_facts_and_neutral_inspection_not_application_logic_route():
    report = inspect_evidence(prepared("E AssertionError: expected 'accepted', got 'abstained'\nE assert result.status == 'accepted'"))
    assert report["facts"]["expected_actual"] == [{"expected":"'accepted'", "actual":"'abstained'", "basis":"explicit_assertion_message", "log_lines":[1]}]
    assert report["facts"]["assertions"][0]["expression"] == "assert result.status == 'accepted'"
    assert report["rule_suggestion"] is None
    assert report["next_inspection"]["basis"] == "observed_assertion"
    assert "contract and configured policy" in report["next_inspection"]["steps"][0]
    assert "application_logic" not in json.dumps(report)
    assert "Expected: 'accepted'\n  Actual: 'abstained'" in report["compact_state"]


def test_comparison_operand_roles_are_not_guessed_and_explicit_labels_are_paired():
    comparison = inspect_evidence(prepared("E AssertionError: unequal\nE assert 'left' == 'right'"))
    assert comparison["facts"]["expected_actual"] == []
    labels = inspect_evidence(prepared("E AssertionError: unequal\nE Expected: 'wanted'\nE Actual: 'received'"))
    assert labels["facts"]["expected_actual"][0]["expected"] == "'wanted'"
    assert labels["facts"]["expected_actual"][0]["actual"] == "'received'"
    assert labels["facts"]["expected_actual"][0]["log_lines"] == [2, 3]


def test_source_and_evaluated_forms_of_one_assertion_are_not_treated_as_different_failures():
    report = inspect_evidence(prepared("> assert result.status == 'accepted'\nE AssertionError: expected 'accepted', got 'abstained'\nE assert 'abstained' == 'accepted'"))
    assert len(report["facts"]["assertions"]) == 2
    assert report["ambiguity"] == []
    assert report["next_inspection"] is not None and report["rule_suggestion"] is None


@pytest.mark.parametrize("summary", ["AssertionError", "AssertionError: expected two but got one", "AssertionError: expected two...", "AssertionError: expected two…", "AssertionError: expected two but got..."])
def test_type_only_duplicate_or_explicitly_truncated_summary_merges_unique_full_exception(summary):
    report = inspect_evidence(prepared(f"AssertionError: expected two but got one\nFAILED tests/test_app.py::test_result - {summary}"))
    assert report["facts"]["exceptions"] == [{"type":"AssertionError", "message":"expected two but got one", "raised_context":True, "log_lines":[1,2]}]
    assert report["ambiguity"] == []
    assert report["next_inspection"] is not None


def test_type_only_summary_can_merge_unqualified_type_without_erasing_qualified_full_type():
    report = inspect_evidence(prepared("E builtins.ModuleNotFoundError: No module named 'example'\nFAILED tests/test_env.py::test_import - ModuleNotFoundError"))
    assert len(report["facts"]["exceptions"]) == 1
    assert report["facts"]["exceptions"][0]["type"] == "builtins.ModuleNotFoundError"
    assert report["rule_suggestion"]["evidence_lines"] == [1,2]


def test_distinct_full_errors_and_different_summary_messages_are_not_merged():
    for log in (
        "E AssertionError: expected two but got one\nFAILED tests/test_app.py::test_result - AssertionError: expected three but got four",
        "E ModuleNotFoundError: No module named 'one'\nE ModuleNotFoundError: No module named 'two'\nFAILED tests/test_env.py::test_import - ModuleNotFoundError",
        "E AssertionError: expected two but got one\nFAILED tests/test_app.py::test_result - AssertionError: a different prefix...",
    ):
        report = inspect_evidence(prepared(log))
        assert len(report["facts"]["exceptions"]) > 1
        assert report["rule_suggestion"] is None


def test_actual_evaluated_assertion_with_different_exception_abstains_from_rule():
    report = inspect_evidence(prepared("E ModuleNotFoundError: No module named 'example'\nE assert result is True"))
    assert "assertion_and_different_exception" in report["ambiguity"]
    assert report["rule_suggestion"] is None


@pytest.mark.parametrize("log, reason", [
    ("E ModuleNotFoundError: No module named 'one'\nE ModuleNotFoundError: No module named 'two'", "different_exception_messages"),
    ("E ModuleNotFoundError: No module named 'one'\nE AssertionError: failed", "different_exception_messages"),
    ("E ModuleNotFoundError: No module named 'one'\nFAILED tests/a.py::test_a - ModuleNotFoundError: No module named 'one'\nFAILED tests/b.py::test_b - ModuleNotFoundError: No module named 'one'", "different_test_families"),
    ("E AssertionError: expected one, got two\nE AssertionError: expected one, got three", "different_expected_actual_pairs"),
])
def test_mixed_or_ambiguous_evidence_preserves_facts_but_abstains_from_inspection(log, reason):
    report = inspect_evidence(prepared(log))
    assert report["facts"]["exceptions"]
    assert reason in report["ambiguity"]
    assert report["rule_suggestion"] is None and report["next_inspection"] is None


def enriched():
    original = prepared('tests/test_api.py:12: AssertionError\nE AssertionError: expected accepted, got abstained')
    original["references"] = [{"path":"tests/test_api.py", "line":12, "excerpt":"11 | result = service()\n12 | assert result.status == 'accepted'"}]
    original["implementation_references"] = [{
        "path":"src/service.py", "line":4, "end_line":7, "excerpt":"4 | def service():\n5 |     return choose()", "symbol":"service", "resolution":"static_python_call",
        "provenance":{"reference":{"path":"tests/test_api.py", "line":12}, "caller":{"path":"tests/test_api.py", "line":11, "symbol":"test_result"}, "import_chain":[{"path":"tests/test_api.py", "line":2, "module":"src.service", "symbol":"service"}]},
    }]
    return original


def test_actual_original_and_implementation_context_have_distinct_deduplicated_provenance():
    item = enriched()
    item["implementation_references"].append(dict(item["implementation_references"][0]))
    report = inspect_evidence(item)
    sources = report["facts"]["source_references"]
    assert [(row["path"],row["line"]) for row in sources] == [("tests/test_api.py",12),("src/service.py",4)]
    assert sources[0]["origins"] == ["log_reference"] and sources[0]["log_lines"] == [1]
    assert sources[1]["origins"] == ["implementation"] and sources[1]["end_line"] == 7
    assert len(sources[1]["implementation_context"]) == 1
    assert sources[1]["implementation_context"][0]["caller"]["line"] == 11
    state = report["compact_state"]
    assert "src/service.py:4" in state and "return choose()" in state
    assert state.index("src/service.py:4") < state.index("tests/test_api.py:12 (original")
    assert report["rule_suggestion"] is None


def test_real_evidence_and_static_enricher_feed_actual_implementation_context(tmp_path):
    from decilist.triage_context import enrich_evidence
    (tmp_path / "pkg").mkdir()
    (tmp_path / "tests").mkdir()
    implementation = tmp_path / "pkg" / "service.py"
    implementation.write_text("def status():\n    return 'abstained'\n", encoding="utf-8")
    test = tmp_path / "tests" / "test_service.py"
    test.write_text("from pkg.service import status\n\ndef test_status():\n    assert status() == 'accepted'\n", encoding="utf-8")
    original = prepare_evidence(f'File "{test}", line 4, in test_status\nAssertionError: expected accepted, got abstained', tmp_path)
    enriched_evidence = enrich_evidence(original, tmp_path)
    report = inspect_evidence(enriched_evidence)
    sources = report["facts"]["source_references"]
    assert {(row["path"],row["line"]) for row in sources} == {("tests/test_service.py",4),("pkg/service.py",1)}
    assert "return 'abstained'" in report["compact_state"]
    assert "pkg/service.py:1" in report["compact_state"]
    assert str(tmp_path) not in json.dumps(report)
    assert report["rule_suggestion"] is None


def test_same_source_coordinate_merges_origins_without_losing_resolution_provenance():
    item = enriched()
    row = item["implementation_references"][0]
    row["path"], row["line"] = "tests/test_api.py", 12
    report = inspect_evidence(item)
    assert len(report["facts"]["source_references"]) == 1
    assert report["facts"]["source_references"][0]["origins"] == ["log_reference","implementation"]


def test_static_property_getter_context_preserves_its_actual_resolution():
    item = enriched()
    getter = item["implementation_references"][0]
    getter.update(resolution="static_python_property", symbol="Value.kind", excerpt="4 | @property\n5 | def kind(self):\n6 |     return 'value'")
    report = inspect_evidence(item)
    context = report["facts"]["source_references"][1]["implementation_context"][0]
    assert context["resolution"] == "static_python_property" and context["symbol"] == "Value.kind"
    assert context["reference"] == {"path":"tests/test_api.py", "line":12}
    assert context["caller"] == {"path":"tests/test_api.py", "line":11, "symbol":"test_result"}
    assert "static Python property" in report["compact_state"]
    assert "def kind(self)" in report["compact_state"]


def test_implementation_comments_and_expected_strings_do_not_create_exception_rules():
    item = enriched()
    item["implementation_references"][0]["excerpt"] = (
        "4 | # ModuleNotFoundError: No module named 'imagined'\n"
        "5 | expected = 'pydantic_core._pydantic_core.ValidationError: 1 validation error for Choose'")
    report = inspect_evidence(item)
    assert report["rule_suggestion"] is None
    assert [row["type"] for row in report["facts"]["exceptions"]] == ["AssertionError"]
    assert "imagined" in report["compact_state"]


def test_enrichment_clipping_is_explicit_in_source_and_overall_packet():
    item = enriched()
    item["implementation_references"][0]["truncated"] = True
    item["context"] = {"truncated": True}
    report = inspect_evidence(item)
    assert report["facts"]["source_references"][1]["truncated"] is True
    assert report["truncated"] and report["compact_truncated"]
    assert "Evidence truncated" in report["compact_state"]


@pytest.mark.parametrize("mutation", ["unobserved_origin", "absolute", "traversal", "unknown_resolution", "bad_chain"])
def test_unverified_or_malformed_implementation_context_is_not_presented_as_actual(mutation):
    item = enriched()
    row = item["implementation_references"][0]
    if mutation == "unobserved_origin": row["provenance"]["reference"]["line"] = 999
    elif mutation == "absolute": row["path"] = "/outside/private.py"
    elif mutation == "traversal": row["path"] = "../outside.py"
    elif mutation == "unknown_resolution": row["resolution"] = "model-guessed"
    else: row["provenance"]["import_chain"] = None
    assert len(inspect_evidence(item)["facts"]["source_references"]) == 1


def test_redaction_is_reused_for_logs_and_source_without_io_or_execution(monkeypatch):
    item = enriched()
    item["log"] += "\nE AssertionError: api_key=top_secret_value"
    item["implementation_references"][0]["excerpt"] += "\n6 | password = 'private_password_value'"
    def forbidden(*args, **kwargs):
        pytest.fail("inspection must not open files, start processes, or connect sockets")
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    before = json.dumps(item)
    report = inspect_evidence(item)
    text = json.dumps(report)
    assert "top_secret_value" not in text and "private_password_value" not in text
    assert "[REDACTED]" in text
    assert json.dumps(item) == before


def test_large_input_is_deduplicated_and_readable_compaction_marks_any_clipping():
    log = "E ModuleNotFoundError: No module named 'example_dependency'\n" * 150
    report = inspect_evidence(prepared(log))
    assert len(report["facts"]["exceptions"]) == 1
    assert len(report["compact_state"]) < len(log)
    assert report["truncated"] is True and "Evidence truncated" in report["compact_state"]
    item = enriched()
    for index in range(6):
        row = dict(item["implementation_references"][0])
        row["path"] = f"src/long_{index}.py"
        row["excerpt"] = "4 | " + "a" * 7000
        item["implementation_references"].append(row)
    result = inspect_evidence(item)
    assert len(result["compact_state"]) <= 12000
    assert result["compact_truncated"] and result["truncated"]
    assert "Evidence truncated" in result["compact_state"]


def test_bounding_repeated_messages_does_not_hide_a_later_different_exception_type():
    log = "\n".join([f"E AssertionError: mismatch {number}" for number in range(16)] + ["E ModuleNotFoundError: No module named 'example'"])
    report = inspect_evidence(prepared(log))
    assert {row["type"] for row in report["facts"]["exceptions"]} == {"AssertionError","ModuleNotFoundError"}
    assert "different_exception_types" in report["ambiguity"]
    assert report["truncated"] is True and report["rule_suggestion"] is None


def test_empty_or_unsupported_evidence_has_no_invented_facts_or_rule():
    report = inspect_evidence(prepared(""))
    assert report["facts"]["failure_observed"] is False
    assert report["facts"]["exceptions"] == [] and report["rule_suggestion"] is None
    assert "No supported concrete failure facts" in report["compact_state"]
    assert len(report["compact_state"]) <= 12000


@pytest.mark.parametrize("invalid", [None, {}, {"schema_version":True,"log":""}, {"schema_version":1,"log":3}, {"schema_version":1,"log":"","references":None}])
def test_invalid_evidence_is_rejected_clearly(invalid):
    with pytest.raises(ValueError, match="schema-v1"):
        inspect_evidence(invalid)
