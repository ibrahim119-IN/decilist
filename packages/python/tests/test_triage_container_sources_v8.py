"""Synthetic metadata-only regressions for observed container RHS source bundles."""
import ast
import json

import pytest

from decilist.triage_context_v6 import build_context
from decilist.triage_hypothesis_v6 import _json_data
from decilist.triage_line_edits_v6 import project_context


@pytest.fixture
def project(tmp_path):
    target = tmp_path / "project"
    target.mkdir()
    return target


def make_trace(project, source, test, edge_specs):
    rows, names = [], {}
    for path, text in (("src/engine.py", source), ("tests/test_flow.py", test)):
        target = project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="")

        def walk(node, prefix=""):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbol = prefix + node.name
                row = {"id": f"{path}:{node.lineno}:{symbol}", "path": path, "symbol": symbol,
                       "first_line": node.lineno, "last_line": node.end_lineno,
                       "executed_lines": list(range(node.lineno + 1, node.end_lineno + 1)),
                       "test_ids": ["tests/test_flow.py::test_flow"], "phases": ["call"]}
                assert symbol not in names
                rows.append(row)
                names[symbol] = row
                for child in node.body:
                    walk(child, symbol + ".<locals>.")
            elif isinstance(node, ast.ClassDef):
                for child in node.body:
                    walk(child, prefix + node.name + ".")
            else:
                for child in ast.iter_child_nodes(node):
                    walk(child, prefix)

        walk(ast.parse(text))
    edges = [{"caller": names[caller]["id"], "callee": names[callee]["id"],
              "line": names[caller]["first_line"] + offset, "count": 1}
             for caller, callee, offset in edge_specs]
    root = names["test_flow"]
    trace = {"schema_version": 5, "functions": rows, "edges": edges, "events": [],
             "failures": [{"nodeid": "tests/test_flow.py::test_flow", "phase": "call",
                           "exception_type": "AssertionError", "longrepr": "",
                           "traceback": [{"path": root["path"], "line": root["last_line"], "symbol": "test_flow"}]}]}
    return trace, names


def fixture(project, *, assignment=None, filler=60, flood=30, secret=False, other_setter=False):
    assignment = assignment or "fields['measure'] = self.compute_value(b'body')"
    source = ("def normalize_field(key):\n    return key.lower()\n\n"
              "class FieldBag:\n"
              "    def __init__(self):\n        self.storage = {}\n"
              "    def __getitem__(self, key):\n        return self.storage[normalize_field(key)]\n"
              "    def __setitem__(self, key, value):\n        self.storage[normalize_field(key)] = value\n\n"
              "class OtherBag:\n"
              "    def __setitem__(self, key, value):\n        pass\n\n"
              "class Serializer:\n"
              "    def prepare(self, fields):\n        " + assignment + "\n"
              "    def other_value(self):\n        return 7\n"
              "    def compute_value(self, payload):\n")
    if secret:
        source += "        token = 'sk-abcdefghijklmnopqrst'\n"
    source += "".join(f"        length_part_{index:03} = len(payload)\n" for index in range(filler))
    source += "        return len(payload) + 1\n\n"
    for index in range(flood):
        source += (f"def read_measure{index}(fields):\n"
                   + "".join(f"    measure_{number} = fields['measure']\n" for number in range(5))
                   + "    return fields['measure']\n\n")
    source += "WARMUP = [" + ",".join(f"read_measure{index}" for index in range(flood)) + "]\n"
    source += "def unobserved_decoy():\n    raise RuntimeError('UNEXECUTED_DECOY')\n"
    test = ("def test_flow():\n    fields = FieldBag()\n    serializer = Serializer()\n"
            "    serializer.prepare(fields)\n    for read in WARMUP:\n        read(fields)\n"
            "    assert fields['measure'] == 4\n")
    edges = [("test_flow", "FieldBag.__init__", 1), ("test_flow", "Serializer.prepare", 3),
             ("test_flow", "FieldBag.__getitem__", 6),
             ("Serializer.prepare", "OtherBag.__setitem__" if other_setter else "FieldBag.__setitem__", 1),
             ("Serializer.prepare", "Serializer.compute_value", 1),
             ("FieldBag.__getitem__", "normalize_field", 1)]
    edges += [("test_flow", f"read_measure{index}", 5) for index in range(flood)]
    trace, names = make_trace(project, source, test, edges)
    trace["functions"] = [row for row in trace["functions"] if row["symbol"] != "unobserved_decoy"]
    return trace, names


def bounded(context, limit=24000, count=32):
    assert len(json.dumps(context)) <= limit
    assert len(context["functions"]) <= count
    sources = {row["id"]: row for row in context["functions"]}
    assert all(row["runtime_observed"] for row in sources.values())
    for allowed in context["edit_allowlist"]:
        row = sources[allowed["function_id"]]
        assert row["complete"] and row["editable"] and row["source"] and not row["guard_reasons"]
    assert context["coverage"]["selected_container_rhs_producers"] <= 2
    assert context["coverage"]["selected_container_rhs_sources"] <= 4
    assert context["coverage"]["container_rhs_source_chars"] <= limit // 3


def reserved(context):
    return [row for row in context["functions"] if row.get("selection_provenance", {}).get("relation") ==
            "observed_rhs_call_at_subscript_assignment"]


@pytest.mark.parametrize("assignment", [None, "fields['measure']: int = self.compute_value(b'body')"])
def test_long_rhs_producer_replaces_accessor_packing_with_exact_observed_source(project, assignment):
    trace, names = fixture(project, assignment=assignment)
    context = build_context(project, trace, max_functions=4)
    rows = {row["symbol"]: row for row in context["functions"]}
    assert set(rows) == {"test_flow", "Serializer.compute_value", "Serializer.prepare", "FieldBag.__getitem__"}
    producer, caller = rows["Serializer.compute_value"], rows["Serializer.prepare"]
    original = (project / "src/engine.py").read_text(encoding="utf-8").splitlines(keepends=True)
    assert producer["source"] == "".join(original[producer["start_line"] - 1:producer["end_line"]])
    assert len(producer["source"]) > 1800 and producer["complete"] and producer["editable"]
    provenance = producer["selection_provenance"]
    assert provenance["observed_caller"] == caller["id"]
    assert provenance["runtime_object_identity_observed"] is False
    assert provenance["same_invocation_observed"] is False and provenance["causality_established"] is False
    edge = next(edge for edge in context["call_graph"] if edge["caller"] == caller["id"] and edge["callee"] == producer["id"])
    assert edge["value_flow"] == "observed_rhs_call_at_subscript_assignment"
    assert edge["line"] == names["Serializer.prepare"]["first_line"] + 1
    assert context["coverage"]["selected_container_rhs_producers"] == 1
    bounded(context, count=4)


@pytest.mark.parametrize("assignment", [
    "fields[self.compute_value(b'body')] = 1",
    "fields['measure'] = 1; self.compute_value(b'body')",
    "fields['measure'] = self.compute_value(b'body'); fields['other'] = self.other_value()",
    "fields['measure'] = fields['other'] = self.compute_value(b'body')",
    "fields['measure'] += self.compute_value(b'body')",
    "fields['measure'] = lambda: self.compute_value(b'body')",
    "fields[self.compute_value(b'key')] = self.compute_value(b'body')",
    "fields['measure'] = callback(b'body')",
])
def test_ambiguous_key_deferred_aliased_and_non_rhs_calls_never_form_reservation(project, assignment):
    trace, _ = fixture(project, assignment=assignment)
    context = build_context(project, trace)
    assert context["coverage"]["container_rhs_candidates"] == 0
    assert not reserved(context)
    bounded(context)


def test_unrelated_same_line_callee_is_not_promoted_with_real_rhs(project):
    trace, names = fixture(project, assignment="fields['measure'] = self.compute_value(b'body'); self.other_value()")
    trace["edges"].append({"caller": names["Serializer.prepare"]["id"], "callee": names["Serializer.other_value"]["id"],
                           "line": names["Serializer.prepare"]["first_line"] + 1, "count": 1})
    context = build_context(project, trace)
    assert {row["symbol"] for row in reserved(context)} == {"Serializer.compute_value"}
    bounded(context)


@pytest.mark.parametrize("missing", ["setter", "producer", "reader", "failure_location"])
def test_each_observed_edge_and_failure_site_is_required(project, missing):
    trace, names = fixture(project)
    if missing == "failure_location":
        trace["failures"][0]["traceback"] = []
    else:
        target = {"setter": "FieldBag.__setitem__", "producer": "Serializer.compute_value", "reader": "FieldBag.__getitem__"}[missing]
        trace["edges"] = [edge for edge in trace["edges"] if edge["callee"] != names[target]["id"]]
    context = build_context(project, trace)
    assert not reserved(context) and context["coverage"]["container_rhs_candidates"] == 0
    bounded(context)


def test_different_lexical_container_class_does_not_connect_to_failure_read(project):
    trace, _ = fixture(project, other_setter=True)
    context = build_context(project, trace)
    assert not reserved(context) and context["coverage"]["container_rhs_candidates"] == 0
    bounded(context)


def test_nested_function_assignment_is_not_attributed_to_outer_recorded_caller(project):
    trace, names = fixture(project, assignment="def deferred():\n            fields['measure'] = self.compute_value(b'body')")
    for edge in trace["edges"]:
        if edge["caller"] == names["Serializer.prepare"]["id"]:
            edge["line"] = names["Serializer.prepare"]["first_line"] + 2
    context = build_context(project, trace)
    assert not reserved(context) and context["coverage"]["container_rhs_candidates"] == 0
    bounded(context)


def test_unavailable_failing_root_source_does_not_supply_a_read_site(project):
    trace, _ = fixture(project)
    (project / "tests/test_flow.py").unlink()
    context = build_context(project, trace)
    assert not reserved(context) and context["coverage"]["container_rhs_candidates"] == 0
    bounded(context)


@pytest.mark.parametrize("mismatch", ["line", "caller", "failed_test", "unexecuted_line"])
def test_sibling_caller_line_test_and_executed_write_must_agree(project, mismatch):
    trace, names = fixture(project)
    producer = next(edge for edge in trace["edges"] if edge["callee"] == names["Serializer.compute_value"]["id"])
    if mismatch == "line":
        producer["line"] += 1
    elif mismatch == "caller":
        producer["caller"] = names["FieldBag.__getitem__"]["id"]
    elif mismatch == "failed_test":
        names["Serializer.compute_value"]["test_ids"] = ["tests/test_other.py::test_other"]
    else:
        names["Serializer.prepare"]["executed_lines"] = []
    context = build_context(project, trace)
    assert not reserved(context) and context["coverage"]["container_rhs_candidates"] == 0
    bounded(context)


def test_depth_limit_is_not_extended_for_container_rhs_sources(project):
    trace, _ = fixture(project)
    context = build_context(project, trace, max_depth=0)
    assert not reserved(context)
    assert context["coverage"]["container_rhs_candidates"] == 0
    bounded(context)


@pytest.mark.parametrize("limit", [6000, 9000, 12000, 24000])
def test_actual_budget_and_function_caps_include_bundles_and_metadata(project, limit):
    trace, _ = fixture(project)
    context = build_context(project, trace, max_chars=limit, max_functions=5)
    bounded(context, limit, 5)


def test_atomic_bundle_rejection_does_not_leave_provenance_or_partial_reservation(project):
    trace, _ = fixture(project)
    context = build_context(project, trace, max_functions=2)
    assert not reserved(context)
    assert context["coverage"]["selected_container_rhs_producers"] == 0
    assert context["coverage"]["selected_container_rhs_sources"] == 0
    bounded(context, count=2)


def test_many_rhs_candidates_obey_distinct_producer_and_total_source_reservations(project):
    source = ("class FieldBag:\n    def __getitem__(self, key):\n        return 0\n"
              "    def __setitem__(self, key, value):\n        pass\n\n"
              "class Writer:\n    def prepare(self, fields):\n"
              "        fields['measure'] = self.compute_first()\n"
              "        fields['other'] = self.compute_second()\n"
              "        fields['last'] = self.compute_third()\n")
    for name in ("compute_first", "compute_second", "compute_third"):
        source += f"    def {name}(self):\n" + "".join(f"        padding_{index} = {index}\n" for index in range(20)) + "        return 1\n"
    test = "def test_flow():\n    fields = FieldBag()\n    Writer().prepare(fields)\n    assert fields['measure'] == 1\n"
    edges = [("test_flow", "Writer.prepare", 2), ("test_flow", "FieldBag.__getitem__", 3)]
    for offset, name in enumerate(("compute_first", "compute_second", "compute_third"), 1):
        edges.extend([("Writer.prepare", "FieldBag.__setitem__", offset), ("Writer.prepare", "Writer." + name, offset)])
    trace, _ = make_trace(project, source, test, edges)
    context = build_context(project, trace)
    assert context["coverage"]["container_rhs_candidates"] == 3
    assert context["coverage"]["selected_container_rhs_producers"] == 2
    assert len(reserved(context)) == 2
    bounded(context)


def test_oversized_rhs_body_cannot_bypass_reservation_limit(project):
    trace, _ = fixture(project, filler=150)
    context = build_context(project, trace)
    assert context["coverage"]["container_rhs_candidates"] == 1
    assert not reserved(context)
    bounded(context)


def test_source_and_dynamic_value_privacy_guards_apply_to_reserved_producer(project):
    trace, names = fixture(project, secret=True)
    names["Serializer.compute_value"]["locals"] = {"password": "DYNAMIC_PRIVATE_VALUE"}
    names["Serializer.compute_value"]["return_value"] = "DYNAMIC_PRIVATE_VALUE"
    context = build_context(project, trace)
    row = next(row for row in reserved(context) if row["symbol"] == "Serializer.compute_value")
    assert row["complete"] and not row["editable"] and "source_redacted" in row["guard_reasons"]
    assert row["id"] not in {allowed["function_id"] for allowed in context["edit_allowlist"]}
    encoded = json.dumps(context)
    assert "sk-abcdefghijklmnopqrst" not in encoded and "DYNAMIC_PRIVATE_VALUE" not in encoded
    assert all(row["symbol"] != "unobserved_decoy" for row in context["functions"])
    assert all(not row["editable"] and not row["runtime_observed"]
               for row in context["read_only_helpers"] if row["symbol"] == "unobserved_decoy")
    bounded(context)


def test_rhs_bundle_has_priority_in_packed_numbered_24k_projection(project):
    trace, _ = fixture(project, flood=45)
    context = build_context(project, trace)
    projection = project_context(context)
    rows = {row["symbol"]: row for row in projection.model["functions"]}
    for name in ("Serializer.compute_value", "Serializer.prepare"):
        assert "editable_lines" in rows[name]
        assert rows[name]["evidence_id"] in projection.sources
        assert rows[name]["evidence_id"] in projection.model["evidence_ids"]
    assert len(_json_data({"runtime_context": projection.model})) <= 24000
    bounded(context)


def test_multiline_failing_assert_read_and_multiline_rhs_keep_observed_line_evidence(project):
    trace, names = fixture(project, assignment="fields['measure'] = self.compute_value(\n            b'body'\n        )")
    test_path = project / "tests/test_flow.py"
    test_path.write_text(test_path.read_text(encoding="utf-8").replace(
        "    assert fields['measure'] == 4\n", "    assert (\n        fields['measure']\n        == 4\n    )\n"), encoding="utf-8")
    root = names["test_flow"]
    root["last_line"] += 3
    root["executed_lines"] = list(range(root["first_line"] + 1, root["last_line"] + 1))
    # Pytest reports the assert start; the recorded getitem happens on its
    # continuation. The setter and producer retain their original identical
    # observed caller/line at the multiline assignment's first physical line.
    trace["failures"][0]["traceback"][0]["line"] = root["last_line"] - 3
    getter = next(edge for edge in trace["edges"] if edge["caller"] == root["id"]
                  and edge["callee"] == names["FieldBag.__getitem__"]["id"])
    getter["line"] += 1
    context = build_context(project, trace)
    assert context["coverage"]["container_rhs_candidates"] == 1
    assert {row["symbol"] for row in reserved(context)} == {"Serializer.compute_value"}
    projection = project_context(context)
    assert any(row["symbol"] == "Serializer.compute_value" and "editable_lines" in row
               for row in projection.model["functions"])
    bounded(context)


def test_getitem_from_an_earlier_assert_is_not_associated_with_later_failure(project):
    trace, names = fixture(project)
    test_path = project / "tests/test_flow.py"
    test_path.write_text(test_path.read_text(encoding="utf-8") + "    assert unrelated_condition\n", encoding="utf-8")
    root = names["test_flow"]
    root["last_line"] += 1
    root["executed_lines"].append(root["last_line"])
    trace["failures"][0]["traceback"][0]["line"] = root["last_line"]
    context = build_context(project, trace)
    assert context["coverage"]["container_rhs_candidates"] == 0
    assert not reserved(context)
    bounded(context)


def test_source_only_decoy_never_gets_execution_or_edit_evidence_and_edges_are_exact(project):
    trace, names = fixture(project)
    context = build_context(project, trace)
    reverse = {row["id"]: identifier for identifier in (row["id"] for row in trace["functions"])
               for row in context["functions"] if names[row["symbol"]]["id"] == identifier}
    observed = {(edge["caller"], edge["callee"], edge["line"]) for edge in trace["edges"]}
    assert all((reverse[edge["caller"]], reverse[edge["callee"]], edge["line"]) in observed for edge in context["call_graph"])
    assert all(row["symbol"] != "unobserved_decoy" for row in context["functions"])
    for row in context["read_only_helpers"]:
        if row["symbol"] == "unobserved_decoy":
            assert row["runtime_observed"] is False and row["editable"] is False
            assert row["evidence_id"] not in {allowed["evidence_id"] for allowed in context["edit_allowlist"]}
    bounded(context)


def test_identical_input_is_deterministic(project):
    trace, _ = fixture(project)
    assert build_context(project, trace) == build_context(project, trace)
