"""Synthetic private policy tests only; no target-project import or execution."""
import ast
import copy
import hashlib

import pytest

from decilist import triage_module_literals_v8 as draft


def supplied(source, *, path="src/made_up.py"):
    lines = draft._lines(source)
    nodes = ast.parse(source, type_comments=True).body
    return {"evidence_id": "m1", "path": path, "complete": False, "editable": False,
        "snippets": [{"start_line": node.lineno, "end_line": node.end_lineno,
            "source": "".join(lines[node.lineno - 1:node.end_lineno]).replace("\r\n", "\n")}
            for node in nodes if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign))]}


def rows(source, module=None):
    return draft.qualify_module_literals(source.encode("utf-8"), supplied(source) if module is None else module)


def change(source, replacement, row=None, module=None):
    module = supplied(source) if module is None else module
    row = rows(source, module)[0] if row is None else row
    return draft.reconstruct_literal(source.encode("utf-8"), module, row, replacement,
        expected_file_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(), cited_ids=[row["evidence_id"]])


def test_direct_literal_reconstruction_preserves_all_surrounding_source_and_inputs():
    source = '# module commentary\nTOKEN = (r"old[0-9]")  # retained inline note\n\ndef render():\n    return TOKEN\n'
    module = supplied(source)
    raw = source.encode()
    original = copy.deepcopy(module)
    row = rows(source, module)[0]
    result = change(source, 'r"new[0-9]"', row, module)
    assert result == {"path": "src/made_up.py", "old": 'TOKEN = (r"old[0-9]")  # retained inline note\n',
        "new": 'TOKEN = (r"new[0-9]")  # retained inline note\n'}
    assert module == original and source.encode() == raw
    assert row["roles"] == ["module_literal"] and row["runtime_observed"] is False
    assert row["file_sha256"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("flags", ["", ", 0", ", re.IGNORECASE", ", flags=re.MULTILINE | re.VERBOSE"])
def test_existing_canonical_regex_wrapper_changes_only_pattern(flags):
    source = f'import re\nPATTERN = re.compile(r"old\\w+"{flags})\n'
    row = rows(source)[0]
    assert row["literal_kind"] == "canonical_re_compile_literal"
    result = change(source, 'r"new\\w+"')
    assert result["new"] == result["old"].replace('r"old\\w+"', 'r"new\\w+"')
    assert flags in result["new"]


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_complete_multiline_token_preserves_canonical_endings(ending):
    source = 'TEXT = """old\n  interior\n tail"""\n'.replace("\n", ending)
    result = change(source, '"""new\n  λ interior\n tail"""')
    assert result["old"] == source
    assert result["new"] == 'TEXT = """new\n  λ interior\n tail"""\n'.replace("\n", ending)


def test_unicode_byte_offsets_and_duplicate_literal_values_do_not_guess_token_location():
    source = 'PATTERN = re.compile(r"old", re.IGNORECASE)  # λ\n'
    source = "import re\n" + source
    result = change(source, 'r"new"')
    assert result["new"].endswith("  # λ\n")
    # The same value in another binding cannot grant a wider string match.
    source = 'LABEL = "old"\nOTHER = "old"\n'
    first = rows(source)[0]
    assert change(source, '"new"', first)["old"] == 'LABEL = "old"\n'
    assert change('Δ = "old"\n', '"new"')["new"] == 'Δ = "new"\n'
    source = 'import re\nPATTERN = re.compile(("{first}{second}").format(first="λ", second="old"))\n'
    second = rows(source)[1]
    assert change(source, '"new"', second)["new"].endswith('first="λ", second="new"))\n')


@pytest.mark.parametrize("source", [
    'VALUE: str = "old"\n', 'VALUE = "old"  # type: str\n', 'LEFT = RIGHT = "old"\n',
    'LEFT, RIGHT = "old", "other"\n',
    'VALUE = "a" "b"\n', 'VALUE = f"old"\n', 'VALUE = "old" + "tail"\n',
    'VALUE = ["old"]\n', 'VALUE = str("old")\n', 'VALUE = unknown("old")\n',
    'VALUE = "old"\nVALUE = "again"\n', 'VALUE = "old"\ndel VALUE\n',
    'VALUE = "old"\ndef mutate():\n    global VALUE\n    VALUE = "again"\n',
    'VALUE = "old"\ndef unrelated(VALUE):\n    return VALUE\n',
    'from unrelated import *\nVALUE = "old"\n', 'VALUE = "old"\nnamespace = globals\n',
    'VALUE = "old"\nlocals()\n', '__all__ = "old"\n',
])
def test_ambiguous_annotated_dynamic_and_nonliteral_assignments_gain_no_authority(source):
    assert rows(source) == []


@pytest.mark.parametrize("prefix,expression", [
    ("import re as regex\n", 'regex.compile("old")'),
    ("from re import compile\n", 'compile("old")'),
    ("import re, other\n", 're.compile("old")'),
    ("import re\nre = other\n", 're.compile("old")'),
    ("import re\nre.compile = other\n", 're.compile("old")'),
    ("import re\nalias = re\n", 're.compile("old")'),
    ("import re\nalias = re.compile\n", 're.compile("old")'),
    ("import re\nimport re as alias\nalias.compile = other\n", 're.compile("old")'),
    ("import re\nfrom re import compile as alias\n", 're.compile("old")'),
    ("import re\n", 're.compile("old", re.DEBUG)'),
    ("import re\n", 're.compile("old", re.LOCALE)'),
    ("import re\n", 're.compile("old", re.I)'),
    ("import re\n", 're.compile("old", 2)'),
    ("import re\n", 're.compile("old", unknown_flags)'),
    ("import re\n", 're.compile("old", flags=unknown())'),
    ("import re\n", 're.compile("old", re.IGNORECASE, flags=re.MULTILINE)'),
    ("import re\n", 're.compile(pattern="old")'),
    ("import re\n", 're.compile("old", extra="other")'),
    ("import re\n", 're.compile(("old{field}").format("value"))'),
])
def test_regex_aliases_rebinding_dynamic_flags_and_format_wrapper_are_explicitly_unsupported(prefix, expression):
    assert rows(prefix + "PATTERN = " + expression + "\n") == []


def test_mixed_physical_statements_are_rejected_as_ambiguous():
    with pytest.raises(ValueError, match="Duplicate"):
        rows('VALUE = "old"; OTHER = "same line"\n')


def test_static_format_wrapper_edits_only_one_keyword_literal_and_keeps_receiver_flags_and_template():
    source = 'import re\nPATTERN = re.compile((r"^{piece}{tail}$").format(piece=r"old\\w+", tail="end"), re.IGNORECASE)\n'
    options = rows(source)
    assert [row["literal_selector"] for row in options] == ["format_keyword:piece", "format_keyword:tail"]
    first = change(source, 'r"new\\w+"', options[0])
    assert first["new"] == first["old"].replace('piece=r"old\\w+"', 'piece=r"new\\w+"')
    callees = lambda text: [ast.dump(node.func, include_attributes=False) for node in ast.walk(ast.parse(text))
                            if isinstance(node, ast.Call)]
    assert callees(first["old"]) == callees(first["new"])
    second = change(source, '"finish"', options[1])
    assert second["new"] == second["old"].replace('tail="end"', 'tail="finish"')
    assert all(row["literal_kind"] == "canonical_re_compile_format_keyword" for row in options)


@pytest.mark.parametrize("expression", [
    '("{field}").format("value")', '("{field}").format(field=b"value")',
    '("{field}").format(field=unknown())', '("{field}").format(**values)',
    '("{field}").format(field="one", extra="two")', '("{field}").format(other="one")',
    '("{field}{field}").format(field="one")', '("{field.name}").format(field="one")',
    '("{field[0]}").format(field="one")', '("{field!r}").format(field="one")',
    '("{field:>2}").format(field="one")', '("{field:}").format(field="one")',
    '("{field:{width}}").format(field="one", width="2")',
    '("{0}").format(field="one")', '("{}").format(field="one")',
    '("{field}").format_map(field="one")', 'template.format(field="one")',
    'make_template().format(field="one")', '(b"{field}").format(field="one")',
    '("{field}").format(field="a" "b")', '("{field}").format(field=f"value")',
    '("{field}").format(field="one", field="two")',
    '("{field}").format(field=eval("value"))',
    '("{field").format(field="one")',
])
def test_all_other_static_format_shapes_are_rejected(expression):
    assert rows("import re\nPATTERN = re.compile(" + expression + ")\n") == []


def test_escaped_format_braces_are_data_and_template_selector_cannot_be_forged():
    source = 'import re\nPATTERN = re.compile((r"^{{{field}}}$").format(field="old"))\n'
    row = rows(source)[0]
    assert change(source, '"new"', row)["new"].endswith('r"^{{{field}}}$").format(field="new"))\n')
    forged = {**row, "literal_selector": "pattern", "literal_source": 'r"^{{{field}}}$"'}
    with pytest.raises(ValueError, match="authority"):
        change(source, '"new"', forged)


def test_static_format_does_not_admit_replacement_template_receiver_or_keyword_syntax():
    source = 'import re\nPATTERN = re.compile((r"^{field}$").format(field="old"), flags=re.VERBOSE)\n'
    for replacement in ['"new".format()', '"new", other="injected"', 'b"new"']:
        with pytest.raises(ValueError):
            change(source, replacement)


@pytest.mark.parametrize("template", ['r"^" r"{field}$"', 'r"^" r"{field}" r"$"',
    'r"^"\n    r"{field}"\n    r"$"'])
def test_unchanged_format_template_accepts_bounded_adjacent_literal_tokens(template):
    source = "import re\nPATTERN = re.compile((" + template + ').format(field="old"))\n'
    result = change(source, '"new"')
    assert result["new"] == result["old"].replace('field="old"', 'field="new"')


def test_unchanged_template_concat_policy_does_not_expand_editable_literal_policy():
    assert rows('VALUE = "one" "two"\n') == []
    assert rows('import re\nPATTERN = re.compile("one" "two")\n') == []
    for template in ['f"{field}"', '"{field}" + "tail"', ' '.join(['"{field}"'] + ['"tail"'] * 8)]:
        assert rows('import re\nPATTERN = re.compile((' + template + ').format(field="old"))\n') == []
    malformed = 'import re\nPATTERN = re.compile(("{field}" b"tail").format(field="old"))\n'
    with pytest.raises(ValueError, match="Malformed"):
        draft.qualify_module_literals(malformed.encode(), {"evidence_id": "m1", "path": "src/made_up.py",
            "complete": False, "editable": False, "snippets": []})


@pytest.mark.parametrize("replacement", [
    '"old"', "'old'", '"\\x6fld"', 'r"old"', 'f"new"', '"new" "suffix"',
    '("new")', '"new"; dangerous()', 'str("new")', 'b"new"', '"new"\n',
])
def test_noop_expression_type_changes_and_token_boundary_escapes_are_rejected(replacement):
    with pytest.raises(ValueError):
        change('VALUE = "old"\n', replacement)


def test_bytes_pattern_stays_bytes():
    result = change('import re\nPATTERN = re.compile(rb"old", re.ASCII)\n', 'rb"new"')
    assert result["new"] == 'PATTERN = re.compile(rb"new", re.ASCII)\n'


def test_redacted_missing_partial_duplicate_and_unsupplied_source_has_no_authority():
    source = 'VISIBLE = "old"\nHIDDEN = "hidden"\n'
    module = supplied(source)
    module["snippets"] = module["snippets"][:1]
    assert [row["symbol"] for row in rows(source, module)] == ["VISIBLE"]
    module["snippets"][0]["source"] = 'VISIBLE = "<redacted>"\n'
    assert rows(source, module) == []
    module = supplied(source)
    module["snippets"] *= 2
    with pytest.raises(ValueError, match="Duplicate"):
        rows(source, module)
    multiline = 'TEXT = """old\n  interior\n tail"""\n'
    module = supplied(multiline)
    module["snippets"][0].update(start_line=2, end_line=2, source="  interior\n")
    assert rows(multiline, module) == []


def test_stale_hash_forged_metadata_missing_citation_and_protected_path_fail_closed():
    source = 'VALUE = "old"\n'
    module, raw = supplied(source), source.encode()
    row = rows(source)[0]
    with pytest.raises(ValueError, match="hash"):
        draft.reconstruct_literal(raw, module, row, '"new"', expected_file_sha256="0" * 64, cited_ids=[row["evidence_id"]])
    for field, value in [("symbol", "OTHER"), ("literal_start_offset", 0), ("source_sha256", "0" * 64),
                         ("editable", False), ("literal_source", '"forged"')]:
        forged = {**row, field: value}
        with pytest.raises(ValueError, match="authority"):
            change(source, '"new"', forged)
    with pytest.raises(ValueError, match="citation"):
        draft.reconstruct_literal(raw, module, row, '"new"', expected_file_sha256=row["file_sha256"], cited_ids=[])
    for path in ("tests/example.py", "config.py", "src/settings.py", "../outside.py"):
        with pytest.raises(ValueError, match="module context"):
            rows(source, supplied(source, path=path))


def test_two_rows_and_bounded_assignment_limits_are_enforced():
    source = 'FIRST = "a"\nSECOND = "b"\nTHIRD = "c"\n'
    assert [row["symbol"] for row in rows(source)] == ["FIRST", "SECOND"]
    source = 'TEXT = """start\n' + '  line\n' * 16 + 'end"""\n'
    assert rows(source) == []
    assert rows('TEXT = "' + "x" * 1200 + '"\n') == []


def test_existing_call_screen_is_still_invoked_without_exemptions(monkeypatch):
    from decilist import triage_verify_v6
    observed = []
    def screen(before, after, filename):
        observed.append((before, after, filename))
        raise ValueError("synthetic existing-screen rejection")
    monkeypatch.setattr(triage_verify_v6, "_screen_ast_changes", screen)
    with pytest.raises(ValueError, match="existing-screen"):
        change('VALUE = "old"\n', '"new"')
    assert observed == [('VALUE = "old"\n', 'VALUE = "new"\n', "src/made_up.py")]


def runtime_context(source='VALUE = "old"\n\ndef compute(value):\n    return value + 1\n'):
    module = supplied(source)
    literal_rows = rows(source, module)
    function = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef))
    function_row = {"id": "f1", "evidence_id": "s1", "path": "src/made_up.py", "symbol": function.name,
        "source": "".join(draft._lines(source)[function.lineno - 1:function.end_lineno]),
        "start_line": function.lineno, "end_line": function.end_lineno, "complete": True, "editable": True,
        "roles": ["implementation"], "guard_reasons": [], "runtime_observed": True}
    return {"schema_version": 6, "kind": "runtime_python_context", "functions": [function_row],
        "module_context": [module], "module_literal_sources": literal_rows, "events": [], "call_graph": [],
        "failures": [{"evidence_id": "failure1", "nodeid": "tests/test.py::test", "phase": "call", "longrepr": "assert value"}],
        "evidence_ids": ["s1", "m1", "failure1", *[row["evidence_id"] for row in literal_rows]],
        "edit_allowlist": [{"function_id": "f1", **{key: function_row[key] for key in
            ("path", "evidence_id", "start_line", "end_line")}}]}


def literal_wire(context, replacement='"new"', *, index=0):
    return {"hypothesis": "The retained literal selects a different value.", "failure_evidence_id": "failure1",
        "evidence_ids": [], "expected_test_behavior": "The observed consumer would receive the changed literal value.",
        "edits": [{"source_evidence_id": context["module_literal_sources"][index]["evidence_id"],
                   "replacement_literal": replacement}]}


def test_distinct_literal_wire_preserves_private_canonical_source_and_public_result_contract():
    from decilist import triage_line_edits_v6 as lines
    context = runtime_context()
    original = copy.deepcopy(context)
    projection = lines.project_context(context)
    public = projection.model["module_literal_sources"][0]
    assert "source" not in public and "file_sha256" not in public and "literal_start_offset" not in public
    assert public["literal_source"] == '"old"' and public["runtime_observed"] is False
    raw = literal_wire(context)
    result = lines.reconstruct(raw, projection)
    assert result.replacements[0].model_dump() == {"path": "src/made_up.py", "old": 'VALUE = "old"\n', "new": 'VALUE = "new"\n'}
    assert result.evidence_ids == ["failure1", public["evidence_id"]]
    assert context == original and "replacement_literal" in raw["edits"][0]
    variants = lines.generation_schema(projection)["properties"]["edits"]["items"]["anyOf"]
    variant = next(item for item in variants if "replacement_literal" in item["properties"])
    assert variant["required"] == ["source_evidence_id", "replacement_literal"]
    assert variant["additionalProperties"] is False


def test_function_and_literal_edits_share_budget_and_both_enter_novelty_signature():
    from decilist import triage_line_edits_v6 as lines
    context = runtime_context()
    projection = lines.project_context(context)
    raw = literal_wire(context)
    function_edit = {"source_evidence_id": "s1", "start_line": 4, "line_count": 1, "replacement": "    return value"}
    first = lines.reconstruct(raw, projection)
    raw["edits"].append(function_edit)
    mixed = lines.reconstruct(raw, projection)
    assert len(mixed.replacements) == 2
    assert draft.intervention_signature(first, context) != draft.intervention_signature(mixed, context)
    raw["edits"].append(function_edit)
    with pytest.raises(ValueError, match="edits"):
        lines.reconstruct(raw, projection)


def test_module_revision_signature_rejects_same_intervention_and_accepts_distinct_value():
    from decilist import triage_line_edits_v6 as lines
    context = runtime_context()
    projection = lines.project_context(context)
    first = lines.reconstruct(literal_wire(context), projection)
    feedback = {"attempt": 1, "previous_result": first.model_dump(),
        "rejection": {"kind": "test_rejection", "exit_code": 1, "output": "Observed mismatch"}}
    checked = draft.checked_feedback(feedback, context)
    with pytest.raises(draft.legacy.GenerationValidationError, match="same resulting"):
        draft.validate_revision(first, context, checked)
    same_value = lines.reconstruct(literal_wire(context, 'u"new"'), projection)
    with pytest.raises(draft.legacy.GenerationValidationError, match="same resulting"):
        draft.validate_revision(same_value, context, checked)
    second = lines.reconstruct(literal_wire(context, '"different"'), projection)
    assert draft.validate_revision(second, context, checked) != draft.intervention_signature(first, context)


def test_same_assignment_leaf_choices_still_reject_overlap():
    from decilist import triage_line_edits_v6 as lines
    source = 'import re\nPATTERN = re.compile(("{first}{second}").format(first="old", second="tail"))\n\ndef compute(value):\n    return value + 1\n'
    context = runtime_context(source)
    raw = literal_wire(context)
    raw["edits"].append(literal_wire(context, '"finish"', index=1)["edits"][0])
    with pytest.raises(ValueError, match="overlap"):
        lines.reconstruct(raw, lines.project_context(context))


@pytest.mark.parametrize("change", ["orphan", "fake_role", "changed_hash", "changed_parent", "missing_citation"])
def test_literal_metadata_provenance_and_citation_guards_remain_distinct(change):
    from decilist import triage_line_edits_v6 as lines
    context = runtime_context()
    if change == "orphan":
        context["module_context"] = []
    elif change == "fake_role":
        context["module_literal_sources"][0]["roles"] = ["implementation"]
    elif change == "changed_hash":
        context["module_literal_sources"][0]["source_sha256"] = "0" * 64
    elif change == "changed_parent":
        context["module_context"][0]["editable"] = True
    else:
        result = lines.reconstruct(literal_wire(context), lines.project_context(context)).model_dump()
        result["evidence_ids"] = ["failure1"]
        with pytest.raises(ValueError, match="cited"):
            draft.validate_generated(result, context)
        return
    with pytest.raises(ValueError):
        lines.project_context(context)


def test_projection_removes_leaf_authority_and_ids_when_parent_must_be_omitted():
    from decilist import triage_line_edits_v6 as lines
    source = 'VALUE = "' + "x" * 900 + '"\n\ndef compute(value):\n    return value + 1\n'
    context = runtime_context(source)
    context["functions"].append({"id": "f9", "evidence_id": "s9", "path": "tests/test.py", "symbol": "test_long",
        "source": 'def test_long():\n    text = "' + "p" * 2300 + '"\n', "start_line": 1, "end_line": 2,
        "complete": True, "editable": False, "roles": ["test"], "guard_reasons": ["test_or_fixture_source"], "runtime_observed": True})
    context["evidence_ids"].append("s9")
    projection = lines.project_context(context, max_chars=4000)
    assert len(draft.legacy._json_data({"runtime_context": projection.model})) <= 4000
    assert not projection.literal_sources and not projection.model["module_literal_sources"]
    assert "m1" not in projection.model["evidence_ids"]
    with pytest.raises(ValueError, match="visible module literal"):
        lines.reconstruct(literal_wire(context, '"short"'), projection)


def test_optional_omission_accounting_never_overflows_a_tight_serialized_budget():
    from decilist import triage_line_edits_v6 as lines
    context = runtime_context()
    context["events"] = [{"evidence_id": "e1", "kind": "line", "padding": "x" * 6000}]
    context["evidence_ids"].append("e1")
    for index in range(12):
        row = {**context["functions"][0], "id": f"f{index+10}", "evidence_id": f"s{index+10}",
            "symbol": f"other{index}", "start_line": 10 + index * 3, "end_line": 11 + index * 3,
            "source": f"def other{index}(value):\n    return value + {index + 2}\n"}
        context["functions"].append(row)
        context["edit_allowlist"].append({"function_id": row["id"], **{key: row[key] for key in
            ("path", "evidence_id", "start_line", "end_line")}})
        context["evidence_ids"].append(row["evidence_id"])
    for budget in range(4000, 4041):
        projection = lines.project_context(context, max_chars=budget)
        assert len(draft.legacy._json_data({"runtime_context": projection.model})) <= budget
        visible = {row["evidence_id"] for key in ("functions", "module_context", "module_literal_sources", "failures", "events", "read_only_helpers")
                   for row in projection.model.get(key, [])}
        assert set(projection.model["evidence_ids"]) <= visible


def test_legacy_default_validation_stays_function_only_without_explicit_module_contract():
    from decilist import triage_line_edits_v6 as lines
    context = runtime_context()
    canonical = lines.reconstruct(literal_wire(context), lines.project_context(context))
    legacy_context = draft.legacy._checked_context(context)
    assert "module_literal_sources" not in legacy_context
    with pytest.raises(ValueError, match="exact editable source"):
        draft.legacy.validate_generated(canonical.model_dump(), legacy_context)
    assert draft.validate_generated(canonical.model_dump(), draft.checked_context(context)) == canonical
