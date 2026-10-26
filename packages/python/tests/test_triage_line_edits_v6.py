"""Portable whole-line projection, reconstruction and canonical guard tests."""
import copy
import hashlib
import json
import sys
import tokenize

import pytest

from decilist import triage_hypothesis_v6 as legacy
from decilist import triage_verify_v6 as protected_runner
from decilist.triage_line_hypothesis_v6 import specification
from decilist.triage_line_edits_v6 import generation_schema, physical_lines, project_context, reconstruct


def context(source="def compute(value):\n    return value + 1\n", *, start=10, path="src/sample.py", roles=None):
    row = {"id": "f1", "evidence_id": "s1", "path": path, "symbol": "compute", "start_line": start,
        "end_line": start + len(physical_lines(source)) - 1, "source": source, "complete": True, "editable": True,
        "guard_reasons": [], "roles": roles or ["implementation"], "runtime_observed": True}
    return {"schema_version": 6, "kind": "runtime_python_context", "functions": [row], "events": [], "call_graph": [],
        "failures": [{"evidence_id": "failure1", "nodeid": "tests/test.py::test", "phase": "call", "longrepr": "assert1==2"}],
        "evidence_ids": ["s1", "failure1"], "edit_allowlist": [{"function_id": "f1", **{key: row[key] for key in ("path", "evidence_id", "start_line", "end_line")}}]}


def candidate(start=11, end=None, replacement="    return value", source_id="s1"):
    return {"hypothesis": "The calculation increases the amount before returning it.", "failure_evidence_id":"failure1", "evidence_ids": [],
        "expected_test_behavior": "The visible assertion would receive the unmodified amount.",
        "edits": [{"source_evidence_id": source_id, "start_line": start, "end_line": start if end is None else end, "replacement": replacement}]}


def test_numbered_projection_private_canonical_and_reconstruction():
    supplied = context()
    original = copy.deepcopy(supplied)
    projection = project_context(supplied)
    assert "11 |     return value + 1" in projection.model["functions"][0]["editable_lines"]
    assert "source" not in projection.model["functions"][0]
    result = reconstruct(candidate(), projection)
    assert result.replacements[0].old == "    return value + 1\n"
    assert result.replacements[0].new == "    return value\n"
    assert supplied == original and projection.canonical["functions"][0]["source"] == original["functions"][0]["source"]
    assert result.evidence_ids == ["failure1","s1"]


def test_verbatim_indentation_can_cross_an_existing_block_boundary_without_guessing():
    supplied=context("def compute(value):\n    try:\n        item = value + 1\n    except ValueError:\n        return 0\n    return item\n")
    replacement="        item = value + 2\n    except ValueError:\n        return 1"
    result=reconstruct(candidate(12,14,replacement),project_context(supplied))
    assert result.replacements[0].new == replacement+"\n"
    # An omitted handler is rejected, never reconstructed or semantically fixed.
    with pytest.raises(ValueError):
        reconstruct(candidate(12,14,"        item = value + 2\n        return 1"),project_context(supplied))


def test_explicit_failure_and_edit_references_construct_bounded_canonical_citations():
    supplied=context()
    second=copy.deepcopy(supplied["functions"][0])
    second.update(id="f2",evidence_id="s2",symbol="other",source="def other(value):\n    return value + 2\n",start_line=20,end_line=21)
    supplied["functions"].append(second)
    supplied["edit_allowlist"].append({"function_id":"f2",**{key:second[key] for key in ("path","evidence_id","start_line","end_line")}})
    events=["e"+str(i) for i in range(5)]
    supplied["events"]=[{"evidence_id":key,"kind":"line"} for key in events]
    supplied["evidence_ids"] += ["s2",*events]
    raw=candidate();raw["evidence_ids"]=events
    raw["edits"].append({"source_evidence_id":"s2","start_line":21,"end_line":21,"replacement":"    return value"})
    result=reconstruct(raw,project_context(supplied))
    assert result.evidence_ids == ["failure1",*events,"s1","s2"] and len(result.evidence_ids)==8
    raw["evidence_ids"]=["failure1","s1","s1"]
    assert reconstruct(raw,project_context(supplied)).evidence_ids == ["failure1","s1","s2"]
    raw["evidence_ids"]=events+["s1"]
    with pytest.raises(ValueError):reconstruct(raw,project_context(supplied))


def test_failure_reference_is_required_actual_and_retained_only_as_abstention_observation():
    projection=project_context(context())
    schema=generation_schema(projection)
    assert "failure_evidence_id" in schema["required"]
    assert schema["properties"]["failure_evidence_id"]["enum"] == ["failure1"]
    raw=candidate();raw.pop("failure_evidence_id")
    with pytest.raises(ValueError):reconstruct(raw,projection)
    raw=candidate();raw["failure_evidence_id"]="s1"
    with pytest.raises(ValueError,match="actual.*failure"):reconstruct(raw,projection)
    raw={"hypothesis":None,"failure_evidence_id":"failure1","evidence_ids":[],"expected_test_behavior":None,"edits":[]}
    result=reconstruct(raw,projection)
    assert result.hypothesis is None and result.evidence_ids==[] and raw["failure_evidence_id"]=="failure1"
    raw["evidence_ids"]=["s1"]
    with pytest.raises(ValueError):reconstruct(raw,projection)


@pytest.mark.parametrize("start,end", [(0,11),(9,11),(11,10),(11,12),(True,11),(10.5,11)])
def test_line_bounds_and_types_are_strict(start, end):
    with pytest.raises(ValueError):
        reconstruct(candidate(start,end), project_context(context()))


@pytest.mark.parametrize("roles,path", [(["test"],"tests/test_sample.py"),(["fixture"],"src/sample.py"),(["static_helper"],"src/sample.py")])
def test_fixture_test_and_read_only_roles_cannot_be_line_edited(roles,path):
    projection = project_context(context(roles=roles,path=path))
    assert not projection.sources
    with pytest.raises(ValueError):reconstruct(candidate(),projection)


def test_exact_canonical_indentation_tabs_crlf_and_unicode():
    source = 'class_value = "unused"\r\n'  # separate literal never used as editable function
    source = 'def compute(value):\r\n\treturn "α\\nβ" + value\r\n'
    result = reconstruct(candidate(replacement='\treturn "λ\\nμ"'), project_context(context(source)))
    assert result.replacements[0].old == '\treturn "α\\nβ" + value\r\n'
    assert result.replacements[0].new == '\treturn "λ\\nμ"\r\n'
    assert "\\n" in result.replacements[0].new
    with pytest.raises(ValueError):
        reconstruct(candidate(replacement='return "λ"'),project_context(context(source)))


def test_decorator_and_multiline_literal_values_remain_verbatim():
    decorated = "@staticmethod\ndef compute(value):\n    return value\n"
    result = reconstruct(candidate(10,replacement="@classmethod"),project_context(context(decorated)))
    assert result.replacements[0].old == "@staticmethod\n" and result.replacements[0].new == "@classmethod\n"
    source = 'def compute(value):\n    text = """old\n  literal\n  tail"""\n    return text\n'
    replacement = '    text = """new\n  λ literal\n  tail"""'
    result = reconstruct(candidate(11,13,replacement),project_context(context(source)))
    assert result.replacements[0].new == '    text = """new\n  λ literal\n  tail"""\n'
    with pytest.raises(ValueError,match="multiline literal"):
        reconstruct(candidate(12,replacement="literal"),project_context(context(source)))


def test_python312_fstring_tokens_preserve_interiors_and_reject_partial_spans():
    source = 'def compute(value):\n    text = f"""old\n  {value}\n  tail"""\n    return text\n'
    replacement = '    text = f"""new\n  {value + 1}\n  λ tail"""'
    result = reconstruct(candidate(11,13,replacement),project_context(context(source)))
    assert result.replacements[0].new == '    text = f"""new\n  {value + 1}\n  λ tail"""\n'
    with pytest.raises(ValueError,match="multiline literal"):
        reconstruct(candidate(12,replacement="{value + 1}"),project_context(context(source)))
    if sys.version_info >= (3,12):
        assert hasattr(tokenize,"FSTRING_START")


def test_unicode_line_separator_is_data_not_an_absolute_source_line():
    source = 'def compute(value):\n    return "α\u2028β"\n'
    assert len(physical_lines(source)) == 2
    result = reconstruct(candidate(replacement='    return "λ\u2028μ"'),project_context(context(source)))
    assert result.replacements[0].new == '    return "λ\u2028μ"\n'


def test_independent_edits_preserve_old_new_literals_and_reject_overlap(tmp_path):
    supplied = context('def compute(value):\n    first = "old-a"\n    second = "old-b"\n    return first + second\n')
    raw = candidate(11,replacement='    first = "new-b"')
    raw["edits"].append({"source_evidence_id":"s1","start_line":12,"end_line":12,"replacement":'    second = "old-a"'})
    result = reconstruct(raw,project_context(supplied))
    assert result.replacements[0].old == '    first = "old-a"\n'
    assert result.replacements[0].new == '    first = "new-b"\n'
    assert result.replacements[1].old == '    second = "old-b"\n'
    assert result.replacements[1].new == '    second = "old-a"\n'
    root=tmp_path/"original";(root/"src").mkdir(parents=True)
    original="\n"*9+supplied["functions"][0]["source"]
    (root/"src/sample.py").write_bytes(original.encode())
    snapshot=protected_runner.snapshot_project(root,tmp_path/"copy")
    protected_runner.apply_replacements(snapshot,[row.model_dump() for row in result.replacements],
        context=supplied,evidence_ids=result.evidence_ids)
    assert (snapshot.root/"src/sample.py").read_bytes() == original.replace('first = "old-a"','first = "new-b"').replace('second = "old-b"','second = "old-a"').encode()
    assert (root/"src/sample.py").read_bytes() == original.encode()
    raw["edits"][1]["start_line"] = 11
    with pytest.raises(ValueError,match="overlap"):reconstruct(raw,project_context(supplied))


def test_existing_runner_still_blocks_new_dynamic_callable_before_writing(tmp_path):
    supplied=context(start=1)
    result=reconstruct(candidate(2,replacement='    return eval("1")'),project_context(supplied))
    root=tmp_path/"original";(root/"src").mkdir(parents=True)
    original=supplied["functions"][0]["source"].encode()
    (root/"src/sample.py").write_bytes(original)
    snapshot=protected_runner.snapshot_project(root,tmp_path/"copy")
    with pytest.raises(ValueError):
        protected_runner.apply_replacements(snapshot,[row.model_dump() for row in result.replacements],
            context=supplied,evidence_ids=result.evidence_ids)
    assert (snapshot.root/"src/sample.py").read_bytes() == original
    assert (root/"src/sample.py").read_bytes() == original


def test_legacy_noop_ast_and_1200_character_limits_remain_authoritative():
    with pytest.raises(ValueError):reconstruct(candidate(replacement="    return value + 1"),project_context(context()))
    with pytest.raises(ValueError):reconstruct(candidate(replacement="    return value + 1 #onlycomment"),project_context(context()))
    huge = "def compute(value):\n    return " + repr("x"*1190) + "\n"
    with pytest.raises(ValueError):reconstruct(candidate(replacement="    return value"),project_context(context(huge)))
    source = "def compute(value):\n    if value:\n        return None\n    else:\n        return None\n"
    with pytest.raises(ValueError,match="one exact"):
        reconstruct(candidate(12,replacement="        return value"),project_context(context(source)))


def test_projection_prunes_orphan_ids_after_budgeting():
    supplied = context()
    supplied["events"] = [{"evidence_id":"event1","kind":"line","padding":"x"*5000}]
    supplied["module_context"] = [{"evidence_id":"module1","source":"x"*3000}]
    supplied["evidence_ids"] += ["event1","module1"]
    projection = project_context(supplied,max_chars=4000)
    assert len(legacy._json_data({"runtime_context":projection.model})) <= 4000
    assert "event1" not in projection.model["evidence_ids"] and "module1" not in projection.model["evidence_ids"]
    assert set(projection.model["evidence_ids"]) == {"s1","failure1"}
    raw=candidate();raw["evidence_ids"].append("event1")
    with pytest.raises(ValueError,match="visible"):reconstruct(raw,projection)


def test_schema_uses_finite_per_source_absolute_line_enums():
    schema = generation_schema(project_context(context()))
    variant=schema["properties"]["edits"]["items"]["anyOf"][0]
    assert variant["properties"]["source_evidence_id"]["const"] == "s1"
    assert variant["properties"]["start_line"]["enum"] == [10,11]
    assert variant["properties"]["end_line"]["enum"] == [10,11]
    assert "old" not in json.dumps(variant) and "path" not in variant["properties"]


def test_removed_constructor_also_removes_helper_payload_and_citation_permission():
    supplied = context()
    source = "    def __init__(self, value):\n        self.flag = False\n" + "        # padding\n" * 300
    constructor = copy.deepcopy(supplied["functions"][0])
    constructor.update(id="f2",evidence_id="s2",symbol="Flag.__init__",source=source,start_line=100,
                       end_line=100+len(physical_lines(source))-1)
    supplied["functions"].append(constructor)
    supplied["edit_allowlist"].append({"function_id":"f2",**{k:constructor[k] for k in ("path","evidence_id","start_line","end_line")}})
    helper_source = "def prime(value):\n    return value + 1\n"
    helper = {"evidence_id":"h1","path":constructor["path"],"symbol":"prime","start_line":500,"end_line":501,
        "source":helper_source,"source_sha256":hashlib.sha256(helper_source.encode()).hexdigest(),
        "file_sha256":hashlib.sha256((source+helper_source).encode()).hexdigest(),
        "complete":True,"editable":False,"roles":["static_helper"],"runtime_observed":False,
        "provenance":{"kind":"static_same_module_helper","observed_constructor_id":"f2",
            "observed_constructor_evidence_id":"s2","trace_observation":"not_present_in_retained_trace","binding":"top_level_def"}}
    supplied["read_only_helpers"]=[helper];supplied["evidence_ids"] += ["s2","h1"]
    original=project_context(supplied)
    assert original.model["read_only_helpers"] == [helper] and "h1" not in original.sources
    with pytest.raises(ValueError,match="editable source"):
        reconstruct(candidate(500,source_id="h1"),original)
    helper_call=candidate(101,replacement="        self.flag = prime(value)",source_id="s2")
    with pytest.raises(ValueError):reconstruct(helper_call,original)
    helper_call["evidence_ids"]=["h1"]
    assert reconstruct(helper_call,original).evidence_ids == ["failure1","h1","s2"]
    packed=project_context(supplied,max_chars=4000)
    assert packed.model["read_only_helpers"] == [] and "s2" not in packed.sources
    assert set(packed.model["evidence_ids"]) == {"s1","failure1"}
    assert len(legacy._json_data({"runtime_context":packed.model})) <= 4000


def test_more_than_sixteen_lines_missing_failure_and_legacy_old_new_are_rejected():
    source="def compute(value):\n" + "    value += 1\n"*18 + "    return value\n"
    with pytest.raises(ValueError,match="at most16"):
        reconstruct(candidate(11,27,"value += 2"),project_context(context(source)))
    raw=candidate();raw["failure_evidence_id"]="s1"
    with pytest.raises(ValueError):reconstruct(raw,project_context(context()))
    raw=candidate();raw["edits"][0]["old"]="return value + 1"
    with pytest.raises(ValueError):reconstruct(raw,project_context(context()))
    assert specification()["system"] != legacy.specification()["system"]
    assert specification()["schema"]["properties"].get("edits")
