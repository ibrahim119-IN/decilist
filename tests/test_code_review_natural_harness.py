"""Measurement integrity and source-edit boundaries, without model inference."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("natural_review", Path(__file__).resolve().parents[1]/"benchmarks/run_code_review_natural.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


def proposal(path="index.js", old="return 1", new="return 2"):
    return {"issue":True,"summary":"A precise change", "edits":[{"path":path,"old":old,"new":new}]}


def test_packet_has_only_participant_inputs():
    payload = h.packet({"index.js":"return 1;"}, [], "generic purpose")
    assert set(payload) == {"task","files","lint_diagnostics"}
    assert payload["files"] == [{"path":"index.js","numbered_source":"1: return 1;"}]
    assert "fixed" not in json.dumps(payload)


def test_transaction_does_not_mutate_source(tmp_path):
    path=tmp_path/"index.js"
    path.write_text("return 1;")
    assert h.validate_edits(tmp_path,["index.js"],proposal()) == {"index.js":"return 2;"}
    assert path.read_text() == "return 1;"


@pytest.mark.parametrize("edit", [proposal("../index.js"),proposal("package.json"),proposal(old=""),proposal(old="missing"),proposal(new="return process.exit(0)"),proposal(new="return eval('2')"),proposal(new="return 1")])
def test_invalid_edits_are_rejected(tmp_path, edit):
    (tmp_path/"index.js").write_text("return 1;")
    with pytest.raises(ValueError): h.validate_edits(tmp_path,["index.js"],edit)


def test_ambiguous_exact_match_is_rejected(tmp_path):
    (tmp_path/"index.js").write_text("return 1; return 1;")
    with pytest.raises(ValueError): h.validate_edits(tmp_path,["index.js"],proposal())


def test_partial_transaction_is_not_written(tmp_path):
    path=tmp_path/"index.js"
    path.write_text("return 1;")
    item=proposal()
    item["edits"].append({"path":"../bad.js","old":"x","new":"y"})
    with pytest.raises(ValueError): h.validate_edits(tmp_path,["index.js"],item)
    assert path.read_text()=="return 1;"


def test_no_issue_cannot_hide_edits(tmp_path):
    (tmp_path/"index.js").write_text("return 1;")
    item=proposal();item["issue"]=False
    with pytest.raises(ValueError): h.validate_edits(tmp_path,["index.js"],item)
