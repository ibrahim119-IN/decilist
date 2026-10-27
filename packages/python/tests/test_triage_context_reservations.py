"""Synthetic source/metadata selector regressions; never execute fixture code."""
import ast
import json

import pytest

from decilist.triage_context_v6 import build_context
from decilist.triage_line_edits_v6 import project_context
from decilist.triage_hypothesis_v6 import _json_data


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    return root


def make_trace(project, sources, edge_specs, *, exception_symbols=()):
    """An explicit bounded runtime metadata fixture, with no value collection."""
    by_name = {}
    rows = []
    for path, source in sources.items():
        target = project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8", newline="")
        tree = ast.parse(source)

        def walk(node, prefix=""):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + node.name
                identifier = f"{path}:{node.lineno}:{name}"
                assert name not in by_name
                row = {"id": identifier, "path": path, "symbol": name,
                       "first_line": node.lineno, "last_line": node.end_lineno,
                       "executed_lines": list(range(node.lineno + 1, node.end_lineno + 1)),
                       "test_ids": ["tests/test_flow.py::test_flow"], "phases": ["call"]}
                by_name[name] = row
                rows.append(row)
                for child in node.body:
                    walk(child, name + ".<locals>.")
            elif isinstance(node, ast.ClassDef):
                for child in node.body:
                    walk(child, prefix + node.name + ".")
            else:
                for child in ast.iter_child_nodes(node):
                    walk(child, prefix)

        walk(tree)
    edges = [{"caller": by_name[caller]["id"], "callee": by_name[callee]["id"],
              "line": by_name[caller]["first_line"] + offset, "count": 1}
             for caller, callee, offset in edge_specs]
    events = [{"seq": index + 1, "kind": "exception", "function_id": by_name[name]["id"],
               "line": by_name[name]["last_line"], "invocation": 7,
               "exception_type": "RuntimeError", "nodeid": "tests/test_flow.py::test_flow"}
              for index, name in enumerate(exception_symbols)]
    root = by_name["test_flow"]
    return {"schema_version": 5, "functions": rows, "edges": edges, "events": events,
            "failures": [{"nodeid": "tests/test_flow.py::test_flow", "phase": "call",
                          "exception_type": "AssertionError", "longrepr": "",
                          "traceback": [{"path": root["path"], "line": root["last_line"],
                                         "symbol": root["symbol"]}]}]}, by_name


def callback_fixture(project, *, invokers=1, secret=False, oversized=False, flood=False, padding=58):
    test = ("def test_flow():\n"
            "    observed = []\n"
            "    def handler(record):\n"
            "        observed.append(record['payload'])\n"
            "        return {'code': 201}\n"
            "    actual = execute(handler)\n"
            "    assert read_header(actual) == 'ready'\n"
            "    assert read_result(actual) == {}\n")
    source = ("def execute(handler):\n    return dispatch(handler)\n\n"
              "def dispatch(handler):\n    return route(handler)\n\n"
              "def route(handler):\n    return bridge0(handler)\n\n"
              "def read_header(actual):\n    return actual['code']\n\n"
              "def read_result(actual):\n    return actual\n\n")
    edges = [("test_flow", "execute", 5), ("test_flow", "read_header", 6),
             ("test_flow", "read_result", 7), ("execute", "dispatch", 1),
             ("dispatch", "route", 1)]
    for index in range(invokers):
        source += f"def bridge{index}(handler):\n    environment = {{'payload': b'body'}}\n"
        if secret:
            source += "    token = 'sk-abcdefghijklmnopqrst'\n"
        count = 180 if oversized else padding
        source += "".join(f"    environment['field_{number:03}'] = {number}\n" for number in range(count))
        source += "    return handler(environment)\n\n"
        edges += [("route", f"bridge{index}", 1),
                  (f"bridge{index}", "test_flow.<locals>.handler", 2 + int(secret) + count)]
    if flood:
        for index in range(35):
            source += (f"def read_payload{index}(actual):\n"
                       + "".join(f"    field_{number} = actual['payload']\n" for number in range(8))
                       + "    return actual['payload']\n\n")
            edges.append(("test_flow", f"read_payload{index}", 6))
    source += "def unobserved_decoy():\n    raise RuntimeError('UNEXECUTED_DECOY')\n"
    trace, names = make_trace(project, {"src/engine.py": source, "tests/test_flow.py": test}, edges)
    # The source-only decoy is intentionally absent from observed identities.
    trace["functions"] = [row for row in trace["functions"] if row["symbol"] != "unobserved_decoy"]
    return trace, names


def exception_fixture(project, *, extra_handlers=0):
    source = ("def annotate():\n"
              "    try:\n"
              "        return translate()\n"
              "    except RuntimeError as error:\n"
              "        error.context = 'retained'\n"
              "        raise\n\n"
              "def translate():\n"
              "    try:\n"
              "        raise ValueError('bad input')\n"
              "    except ValueError as error:\n"
              "        kinds = build_table()\n"
              "        raise kinds[type(error)]('mapped') from error\n\n"
              "def build_table():\n"
              + "".join(f"    mapping_entry_{index:03} = {index}\n" for index in range(58))
              + "    return {ValueError: RuntimeError}\n\n")
    handlers = ["annotate", "translate"]
    for index in range(extra_handlers):
        name = f"more_context{index}"
        source += (f"def {name}():\n    try:\n        raise RuntimeError('x')\n"
                   "    except RuntimeError:\n        raise\n\n")
        handlers.append(name)
    test = ("def test_flow():\n    try:\n        annotate()\n"
            "    except RuntimeError as error:\n        assert str(error) == 'expected'\n")
    edges = [("test_flow", "annotate", 2), ("annotate", "translate", 2),
             ("translate", "build_table", 4)]
    return make_trace(project, {"src/conversion.py": source, "tests/test_flow.py": test},
                      edges, exception_symbols=handlers)


def assert_budget_and_permissions(context, max_chars, max_functions=32):
    assert len(json.dumps(context)) <= max_chars
    assert len(context["functions"]) <= max_functions
    sources = {row["id"]: row for row in context["functions"]}
    assert all(row["runtime_observed"] for row in sources.values())
    for permission in context["edit_allowlist"]:
        row = sources[permission["function_id"]]
        assert row["complete"] and row["editable"] and row["source"]
        assert permission["evidence_id"] == row["evidence_id"]


def test_two_handler_scopes_with_identical_failed_test_and_invocation_are_reserved(project):
    trace, _ = exception_fixture(project)
    context = build_context(project, trace)
    rows = {row["symbol"]: row for row in context["functions"]}
    assert context["coverage"]["selected_exception_anchors"] == 2
    for name in ("annotate", "translate", "build_table"):
        assert rows[name]["complete"] and rows[name]["editable"]
    edge = next(edge for edge in context["call_graph"]
                if edge["caller"] == rows["translate"]["id"] and edge["callee"] == rows["build_table"]["id"])
    assert edge["exception_context"] is True
    assert "not proved exception propagation" in context["note"]
    assert_budget_and_permissions(context, 24000)


def test_exception_diversity_keeps_existing_anchor_and_source_caps(project):
    trace, _ = exception_fixture(project, extra_handlers=4)
    context = build_context(project, trace)
    assert context["coverage"]["selected_exception_anchors"] == context["limits"]["max_exception_anchors"] == 2
    assert context["coverage"]["selected_exception_sources"] <= context["limits"]["max_exception_sources"] == 8
    assert_budget_and_permissions(context, 24000)


def test_second_exception_helper_precedes_redundant_first_anchor_wrappers(project):
    exception_fixture(project)
    source = (project / "src/conversion.py").read_text(encoding="utf-8")
    for name in ("wrapper_alpha", "wrapper_beta"):
        source += (f"\ndef {name}():\n"
                   + "".join(f"    padding_entry_{index:03} = {index}\n" for index in range(60))
                   + "    return annotate()\n")
    test = ("def test_flow():\n    for entry in (wrapper_alpha, wrapper_beta):\n"
            "        try:\n            entry()\n"
            "        except RuntimeError as error:\n            assert str(error) == 'expected'\n")
    edges = [("test_flow", "wrapper_alpha", 3), ("test_flow", "wrapper_beta", 3),
             ("wrapper_alpha", "annotate", 61), ("wrapper_beta", "annotate", 61),
             ("annotate", "translate", 2), ("translate", "build_table", 4)]
    trace, _ = make_trace(project, {"src/conversion.py": source, "tests/test_flow.py": test},
                          edges, exception_symbols=("annotate", "translate"))
    context = build_context(project, trace)
    rows = {row["symbol"]: row for row in context["functions"]}
    assert context["coverage"]["selected_exception_anchors"] == 2
    assert rows["build_table"]["complete"] and rows["build_table"]["editable"]
    assert_budget_and_permissions(context, 24000)


def test_exception_slot_prefers_uncovered_failed_test_before_same_coverage_fallback(project):
    trace, names = exception_fixture(project, extra_handlers=3)
    alternate = "tests/test_flow.py::test_flow[alternate]"
    trace["failures"].append({**trace["failures"][0], "nodeid": alternate})
    names["test_flow"]["test_ids"].append(alternate)
    names["more_context2"]["test_ids"] = [alternate]
    for event in trace["events"]:
        if event["function_id"] == names["more_context2"]["id"]:
            event["nodeid"] = alternate
    context = build_context(project, trace, max_functions=3)
    selected = {row["symbol"] for row in context["functions"]}
    assert "more_context2" in selected
    assert selected & {"more_context0", "more_context1"}
    assert context["coverage"]["selected_exception_anchors"] == 2
    assert_budget_and_permissions(context, 24000, 3)


def test_unexecuted_handler_scope_is_not_promoted_by_exception_event_alone(project):
    trace, names = exception_fixture(project)
    names["translate"]["executed_lines"] = [names["translate"]["first_line"] + 2]
    context = build_context(project, trace)
    assert context["coverage"]["selected_exception_anchors"] == 1
    assert_budget_and_permissions(context, 24000)


def test_long_recorded_callback_invoker_is_reserved_before_two_assertion_readers(project):
    trace, _ = callback_fixture(project)
    context = build_context(project, trace, max_functions=4)
    rows = {row["symbol"]: row for row in context["functions"]}
    invoker = rows["bridge0"]
    assert len(invoker["source"]) > 1800
    assert invoker["complete"] and invoker["editable"]
    assert invoker["observed_call_depth"] == 4
    provenance = invoker["selection_provenance"]
    assert provenance["relation"] == "observed_call_to_root_contained_callback"
    assert provenance["callback_symbol"] == "test_flow.<locals>.handler"
    assert provenance["callback_source_container"] == rows["test_flow"]["id"]
    assert provenance["causality_established"] is False
    assert not any(row["symbol"] == "test_flow.<locals>.handler" for row in context["functions"])
    assert "def handler(record):" in rows["test_flow"]["source"]
    assert all(edge["callee"] != provenance["callback_function_id"] for edge in context["call_graph"])
    assert context["coverage"]["selected_callback_invokers"] == 1
    assert_budget_and_permissions(context, 24000, 4)


def test_callback_reservation_never_invents_execution_or_dispatch(project):
    trace, names = callback_fixture(project)
    callback = names["test_flow.<locals>.handler"]["id"]
    trace["edges"] = [edge for edge in trace["edges"] if edge["callee"] != callback]
    context = build_context(project, trace)
    assert context["coverage"]["selected_callback_invokers"] == 0
    assert not any(row.get("selection_provenance", {}).get("relation") ==
                   "observed_call_to_root_contained_callback" for row in context["functions"])
    assert "UNEXECUTED_DECOY" not in json.dumps(context)
    assert_budget_and_permissions(context, 24000)


def test_callback_reservation_preserves_existing_depth_limit(project):
    trace, _ = callback_fixture(project)
    context = build_context(project, trace, max_depth=0)
    assert context["coverage"]["selected_callback_invokers"] == 0
    assert not any(row["symbol"] == "bridge0" for row in context["functions"])
    assert_budget_and_permissions(context, 24000)


def test_callback_reservation_is_bounded_across_many_observed_invokers(project):
    trace, _ = callback_fixture(project, invokers=4, padding=20)
    context = build_context(project, trace)
    assert context["coverage"]["selected_callback_invokers"] == context["limits"]["max_callback_invokers"] == 2
    assert context["coverage"]["callback_source_chars"] <= context["limits"]["max_callback_source_chars"]
    reserved = [row for row in context["functions"] if row.get("selection_provenance", {}).get("relation") ==
                "observed_call_to_root_contained_callback"]
    assert 0 < len(reserved) <= 2
    assert_budget_and_permissions(context, 24000)


def test_oversized_callback_invoker_does_not_bypass_body_reservation_limit(project):
    trace, _ = callback_fixture(project, oversized=True)
    context = build_context(project, trace)
    assert context["coverage"]["selected_callback_invokers"] == 0
    assert_budget_and_permissions(context, 24000)


@pytest.mark.parametrize("max_chars", [6000, 9000, 12000, 24000])
def test_reservations_obey_actual_serialized_budget_and_function_count(project, max_chars):
    trace, _ = callback_fixture(project, invokers=3)
    context = build_context(project, trace, max_chars=max_chars, max_functions=5)
    assert_budget_and_permissions(context, max_chars, 5)
    assert context["coverage"]["callback_source_chars"] <= max_chars // 4


def test_reserved_source_redaction_and_trace_unknown_values_remain_guarded(project):
    trace, names = callback_fixture(project, secret=True)
    names["bridge0"]["locals"] = {"password": "DYNAMIC_PRIVATE_VALUE"}
    names["bridge0"]["return_value"] = "DYNAMIC_PRIVATE_VALUE"
    trace["events"] = [{"seq": 1, "kind": "call", "invocation": 1,
                        "function_id": names["bridge0"]["id"], "line": names["bridge0"]["first_line"],
                        "nodeid": "tests/test_flow.py::test_flow", "locals": {"token": "DYNAMIC_PRIVATE_VALUE"}}]
    context = build_context(project, trace)
    row = next(row for row in context["functions"] if row["symbol"] == "bridge0")
    assert row["complete"] and not row["editable"] and "source_redacted" in row["guard_reasons"]
    assert row["id"] not in {entry["function_id"] for entry in context["edit_allowlist"]}
    serialized = json.dumps(context)
    assert "sk-abcdefghijklmnopqrst" not in serialized and "DYNAMIC_PRIVATE_VALUE" not in serialized
    assert "UNEXECUTED_DECOY" not in serialized
    assert_budget_and_permissions(context, 24000)


def test_callback_reservation_survives_packed_numbered_24k_model_projection(project):
    trace, _ = callback_fixture(project, flood=True)
    context = build_context(project, trace)
    projection = project_context(context)
    row = next(row for row in context["functions"] if row["symbol"] == "bridge0")
    visible = next(row for row in projection.model["functions"] if row["symbol"] == "bridge0")
    assert "editable_lines" in visible and "return handler(environment)" in visible["editable_lines"]
    assert row["evidence_id"] in projection.sources and row["evidence_id"] in projection.model["evidence_ids"]
    assert len(_json_data({"runtime_context": projection.model})) <= 24000
    assert_budget_and_permissions(context, 24000)


def test_both_exception_scopes_and_helper_survive_numbered_model_projection(project):
    trace, _ = exception_fixture(project)
    context = build_context(project, trace)
    projection = project_context(context)
    visible = {row["symbol"]: row for row in projection.model["functions"]}
    for symbol in ("annotate", "translate", "build_table"):
        assert "editable_lines" in visible[symbol]
        assert visible[symbol]["evidence_id"] in projection.sources
    assert len(_json_data({"runtime_context": projection.model})) <= 24000


def test_synthetic_replay_is_deterministic(project):
    trace, _ = callback_fixture(project)
    assert build_context(project, trace) == build_context(project, trace)
