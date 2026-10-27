"""Real-worker checks of the bounded literal context/application boundary."""
import copy
import json
from pathlib import Path

import pytest

from decilist import triage_verify_v6 as runner
from decilist.triage_context_v6 import build_context
from decilist.triage_module_literals_v8 import candidate_replacement


@pytest.fixture(params=[False, True], ids=["ordinary-anchor", "redacted-read-only-anchor"])
def observed(tmp_path, request):
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "tests").mkdir()
    extra = '    password = "synthetic-placeholder-value"\n' if request.param else ''
    (project / "src/matcher.py").write_text(
        'import re\nUNUSED = "do not select this"\nPATTERN = re.compile(r"^a$")\n'
        'def check(value):\n' + extra + '    return bool(PATTERN.fullmatch(value))\n', encoding="utf-8")
    (project / "tests/test_matcher.py").write_text(
        'from matcher import check\ndef test_expected():\n    assert check("b")\n'
        'def test_unrelated():\n    assert not check("c")\n', encoding="utf-8")
    snapshot = runner.snapshot_project(project, tmp_path / "copy")
    failed = runner.run_pytest(snapshot, ["tests/test_matcher.py::test_expected"], capture_failure_text=True)
    assert failed["state"] == "failed" and failed["snapshot_unchanged"]
    trace = json.loads(Path(failed["trace_path"]).read_text())
    context = build_context(snapshot.root, trace, include_module_literals=True)
    rows = context["module_literal_sources"]
    assert len(rows) == 1 and rows[0]["symbol"] == "PATTERN"
    assert not rows[0]["runtime_observed"] and rows[0]["roles"] == ["module_literal"]
    if request.param:
        anchor = next(row for row in context["functions"] if row["symbol"] == "check")
        assert not anchor["editable"] and "source_redacted" in anchor["guard_reasons"]
    assert "raw_bytes" not in json.dumps(context)
    return snapshot, context, rows[0], trace


def test_literal_candidate_is_requalified_then_passes_unchanged_tests(observed):
    snapshot, context, row, _ = observed
    replacement = candidate_replacement(row, 'r"^b$"')
    patch = runner.apply_replacements(snapshot, [replacement], context=context, evidence_ids=[row["evidence_id"]])
    assert patch["admitted_module_literals"] == [row["evidence_id"]]
    passed = runner.run_pytest(snapshot, ["tests/test_matcher.py"], expected_hashes=patch["hashes_after"])
    assert passed["state"] == "passed" and passed["original_unchanged"]
    assert len(passed["collected"]) == 2


@pytest.mark.parametrize("corruption", ["hash", "citation", "wrapper", "span"])
def test_literal_authority_rejected_before_any_write(observed, corruption):
    snapshot, context, row, _ = observed
    context = copy.deepcopy(context)
    replacement = candidate_replacement(row, 'r"^b$"')
    cited = [row["evidence_id"]]
    if corruption == "hash":
        context["module_literal_sources"][0]["file_sha256"] = "0" * 64
    elif corruption == "citation":
        cited = []
    elif corruption == "span":
        context["module_literal_sources"][0]["start_line"] += 1
    else:
        replacement["new"] = replacement["new"].replace("re.compile", "re.search")
    target = snapshot.root / row["path"]
    before = target.read_bytes()
    with pytest.raises(ValueError):
        runner.apply_replacements(snapshot, [replacement], context=context, evidence_ids=cited)
    assert target.read_bytes() == before


def test_default_context_retains_function_only_authority(observed):
    snapshot, _, _, trace = observed
    context = build_context(snapshot.root, trace)
    assert "module_literal_sources" not in context


def test_tight_context_pruning_keeps_literal_parent_and_ids_synchronized(observed):
    snapshot, _, _, trace = observed
    from decilist.triage_line_edits_v6 import project_context
    from decilist.triage_hypothesis_v6 import _json_data
    for budget in (4000, 6000, 12000, 24000):
        context = build_context(snapshot.root, trace, include_module_literals=True, max_chars=budget)
        assert len(json.dumps(context)) <= budget
        projection = project_context(context, max_chars=budget)
        assert len(_json_data({"runtime_context": projection.model})) <= budget
        parents = {row["evidence_id"] for row in projection.model.get("module_context", [])}
        for row in projection.model.get("module_literal_sources", []):
            assert row["evidence_id"] in projection.model["evidence_ids"]
            assert row["provenance"]["module_evidence_id"] in parents
