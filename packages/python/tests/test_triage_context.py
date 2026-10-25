import copy
import importlib
import json
from pathlib import Path
import socket
import subprocess

import pytest

from decilist.triage_context import enrich_evidence
from decilist.triage_evidence import prepare_evidence


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    return root


def write(project, path, text):
    target = project / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def evidence_for(project, text, marker="assert"):
    path = write(project, "tests/test_failure.py", text)
    line = next(index for index, value in enumerate(text.splitlines(), 1) if marker in value)
    return prepare_evidence(f'  File "{path}", line {line}, in test_failure\nAssertionError: wrong result', project)


def test_follows_classmethod_through_real_reexport_with_precise_provenance(project):
    write(project, "src/pkg/__init__.py", "from .impl import Worker\n")
    write(project, "src/pkg/impl.py", "class Worker:\n    @classmethod\n    def check(cls, value):\n        return value + 1\n")
    evidence = evidence_for(project, "from pkg import Worker\n\ndef test_failure():\n    assert Worker.check(2) == 4\n")
    original = copy.deepcopy(evidence)
    result = enrich_evidence(evidence, project)
    assert evidence == original
    assert {key: result[key] for key in evidence} == evidence
    rows = result["implementation_references"]
    assert len(rows) == 1
    row = rows[0]
    assert (row["path"], row["line"], row["end_line"], row["symbol"]) == ("src/pkg/impl.py", 2, 4, "Worker.check")
    assert row["excerpt"] == "2 |     @classmethod\n3 |     def check(cls, value):\n4 |         return value + 1"
    assert row["resolution"] == "static_python_call"
    assert row["provenance"] == {
        "reference": {"path": "tests/test_failure.py", "line": 4},
        "caller": {"path": "tests/test_failure.py", "line": 4, "symbol": "test_failure"},
        "import_chain": [
            {"path": "tests/test_failure.py", "line": 1, "module": "pkg", "symbol": "Worker"},
            {"path": "src/pkg/__init__.py", "line": 1, "module": "pkg.impl", "symbol": "Worker"},
        ],
    }
    assert str(project) not in json.dumps(result)


@pytest.mark.parametrize("root", ["", "src/", "packages/python/src/", "vendor/"])
def test_known_local_roots_and_constructor_bound_instance_methods(project, root):
    write(project, root + "worker.py", "class Worker:\n    def check(self, value):\n        return value + 1\n")
    evidence = evidence_for(project, "from worker import Worker\n\ndef test_failure():\n    worker = Worker()\n    assert worker.check(2) == 4\n")
    rows = enrich_evidence(evidence, project)["implementation_references"]
    assert any(row["path"] == root + "worker.py" and row["symbol"] == "Worker.check" for row in rows)


def test_inline_constructor_method_and_same_module_helper_following(project):
    write(project, "src/worker.py", "def normalize(value):\n    return value + 1\n\nclass Worker:\n    def check(self, value):\n        return normalize(value)\n")
    evidence = evidence_for(project, "from worker import Worker\n\ndef test_failure():\n    assert Worker().check(2) == 4\n")
    rows = enrich_evidence(evidence, project)["implementation_references"]
    assert rows[0]["symbol"] == "Worker.check"
    assert any(row["symbol"] == "normalize" and row["provenance"]["caller"]["symbol"] == "Worker.check" for row in rows)


def test_reexports_do_not_exhaust_separately_bounded_call_hops(project):
    write(project, "src/pkg/__init__.py", "from .worker import Worker\n")
    write(project, "src/pkg/worker.py", "from .checks import validate\n\nclass Worker:\n    def __init__(self):\n        self.ready = True\n    def check(self, value):\n        return validate(value)\n")
    write(project, "src/pkg/checks.py", "class CheckError(Exception):\n    pass\n\ndef validate(value):\n    if value < 0:\n        raise CheckError('bad value')\n    return value\n")
    result = enrich_evidence(evidence_for(project, "from pkg import Worker\ndef test_failure():\n    assert Worker().check(-1) == 0\n"), project)
    symbols = [row["symbol"] for row in result["implementation_references"]]
    assert symbols[:2] == ["Worker.check", "validate"]
    assert "Worker.__init__" not in symbols
    assert "CheckError" not in symbols
    assert result["context"]["files_examined"] <= 8


def test_constructor_only_failure_retains_actual_constructor(project):
    write(project, "src/schema.py", "class Schema:\n    def __init__(self, value):\n        self.value = value\n")
    result = enrich_evidence(evidence_for(project, "from schema import Schema\ndef test_failure():\n    assert Schema(1).value == 2\n"), project)
    assert [row["symbol"] for row in result["implementation_references"]] == ["Schema.__init__"]


def test_unrelated_same_class_method_does_not_hide_constructor_operation(project):
    write(project, "src/schema.py", "class Schema:\n    def __init__(self, value):\n        self.value = value\n    @classmethod\n    def valid(cls):\n        return True\n")
    result = enrich_evidence(evidence_for(project, "from schema import Schema\ndef test_failure():\n    Schema.valid()\n    assert Schema(1).value == 2\n"), project)
    assert "Schema.__init__" in [row["symbol"] for row in result["implementation_references"]]


def test_direct_operations_near_failure_precede_earlier_setup_constructors(project):
    write(project, "src/worker.py", "class Setup:\n    pass\n\ndef first():\n    return 1\n\ndef last():\n    return 2\n")
    result = enrich_evidence(evidence_for(project, "from worker import Setup, first, last\ndef test_failure():\n    config = Setup()\n    first()\n    assert last() == 3\n"), project)
    assert [row["symbol"] for row in result["implementation_references"]][:2] == ["last", "first"]


def test_property_read_uses_static_getter_and_suppresses_receiver_constructor(project):
    write(project, "vendor/pkg/__init__.py", "from .api import Value\n")
    write(project, "vendor/pkg/api.py", "class Value:\n    def __init__(self, value):\n        self._value = value\n    @property\n    def content(self):\n        return self._value\n")
    evidence = evidence_for(project, "from pkg import Value\ndef test_failure():\n    value = Value(1)\n    assert value.content == 2\n")
    result = enrich_evidence(evidence, project)
    rows = result["implementation_references"]
    assert [(row["symbol"], row["resolution"]) for row in rows] == [("Value.content", "static_python_property")]
    assert (rows[0]["path"], rows[0]["line"], rows[0]["end_line"]) == ("vendor/pkg/api.py", 4, 6)
    assert rows[0]["provenance"]["caller"] == {"path": "tests/test_failure.py", "line": 4, "symbol": "test_failure"}
    assert [row["module"] for row in rows[0]["provenance"]["import_chain"]] == ["pkg", "pkg.api"]
    assert "return self._value" in rows[0]["excerpt"]


def test_inline_constructed_property_and_getter_self_property_reads(project):
    write(project, "src/value.py", "class Value:\n    @property\n    def inner(self):\n        return 1\n    @property\n    def outer(self):\n        return self.inner\n")
    result = enrich_evidence(evidence_for(project, "from value import Value\ndef test_failure():\n    assert Value().outer == 2\n"), project)
    assert [(row["symbol"], row["resolution"]) for row in result["implementation_references"]] == [
        ("Value.outer", "static_python_property"), ("Value.inner", "static_python_property")]


def test_class_property_object_and_dynamic_descriptor_are_not_getter_execution(project):
    write(project, "src/value.py", "class Value:\n    @property\n    def content(self):\n        return 'GETTER_NOT_USED_ON_CLASS'\n    @unknown_descriptor\n    def other(self):\n        return 'DYNAMIC_DESCRIPTOR'\n")
    result = enrich_evidence(evidence_for(project, "from value import Value\ndef test_failure():\n    assert Value.content == 2\n"), project)
    assert result["implementation_references"] == []
    assert "GETTER_NOT_USED_ON_CLASS" not in json.dumps(result)
    dynamic = enrich_evidence(evidence_for(project, "from value import Value\ndef test_failure():\n    item = Value()\n    assert item.other == 2\n"), project)
    assert not any(row["resolution"] == "static_python_property" for row in dynamic["implementation_references"])


def test_module_alias_and_explicit_dotted_import(project):
    write(project, "src/pkg/api.py", "def check(value):\n    return value + 1\n")
    for statement, call in [("import pkg.api as api", "api.check"), ("import pkg.api", "pkg.api.check")]:
        evidence = evidence_for(project, f"{statement}\n\ndef test_failure():\n    assert {call}(2) == 4\n")
        rows = enrich_evidence(evidence, project)["implementation_references"]
        assert [(row["path"], row["symbol"]) for row in rows] == [("src/pkg/api.py", "check")]


def test_only_failing_function_calls_are_inspected_and_no_oracle_is_read(project, monkeypatch):
    write(project, "src/used.py", "raise RuntimeError('NEVER_IMPORT_SOURCE')\ndef check():\n    return 1\n")
    write(project, "src/unrelated.py", "def check():\n    return 'UNRELATED_SOURCE'\n")
    write(project, "oracle_catalog.json", '{"expected_fix":"DO_NOT_READ_ORACLE"}')
    evidence = evidence_for(project, "from used import check\nfrom unrelated import check as other\n\ndef test_unrelated():\n    other()\n\ndef test_failure():\n    assert check() == 2\n")
    original_open = Path.open
    opened = []

    def guarded(path, *args, **kwargs):
        opened.append(path.relative_to(project).as_posix())
        assert path.resolve().is_relative_to(project)
        assert path.name not in {"oracle_catalog.json", "unrelated.py"}
        return original_open(path, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("No import/execution/network/scanning is allowed")

    monkeypatch.setattr(Path, "open", guarded)
    monkeypatch.setattr(Path, "glob", forbidden)
    monkeypatch.setattr(Path, "rglob", forbidden)
    monkeypatch.setattr(importlib, "import_module", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    result = enrich_evidence(evidence, project)
    assert result["implementation_references"][0]["symbol"] == "check"
    assert set(opened) == {"tests/test_failure.py", "src/used.py"}
    assert "UNRELATED_SOURCE" not in json.dumps(result)
    assert "NEVER_IMPORT_SOURCE" not in json.dumps(result)


@pytest.mark.parametrize("kind", ["parameter", "conditional", "late_binding", "module_shadow", "getattr"])
def test_dynamic_or_shadowed_resolution_is_explicit_not_guessed(project, kind):
    write(project, "src/worker.py", "def check():\n    return 'WRONG_GUESSED_CONTEXT'\n")
    texts = {
        "parameter": "from worker import check\ndef test_failure(check):\n    assert check() == 2\n",
        "conditional": "from worker import check\ndef test_failure():\n    if unknown:\n        check = unknown\n    assert check() == 2\n",
        "late_binding": "from worker import check\ndef test_failure():\n    assert check() == 2\n    check = unknown\n",
        "module_shadow": "from worker import check\ncheck = unknown\ndef test_failure():\n    assert check() == 2\n",
        "getattr": "import worker\ndef test_failure():\n    assert getattr(worker, 'check')() == 2\n",
    }
    result = enrich_evidence(evidence_for(project, texts[kind]), project)
    assert result["implementation_references"] == []
    assert result["context"]["unresolved"]
    assert "WRONG_GUESSED_CONTEXT" not in json.dumps(result)


def test_malformed_python_is_fail_closed(project):
    write(project, "src/broken.py", "def check(\n")
    result = enrich_evidence(evidence_for(project, "from broken import check\ndef test_failure():\n    assert check() == 2\n"), project)
    assert result["implementation_references"] == []
    assert any(row["reason"] == "unreadable_or_malformed_python" for row in result["context"]["unresolved"])


def test_missing_import_and_unreferenced_source_never_invent_context(project):
    write(project, "src/available.py", "def check():\n    return 'UNREFERENCED'\n")
    evidence = evidence_for(project, "from missing import check\ndef test_failure():\n    assert check() == 2\n")
    evidence["references"].append({"path": "src/available.py", "line": 2, "excerpt": "forged"})
    result = enrich_evidence(evidence, project)
    assert result["implementation_references"] == []
    assert any(row["symbol"] == "check" for row in result["context"]["unresolved"])


@pytest.mark.parametrize("name", ["secrets", "credentials", "private_key"])
def test_secret_named_implementation_files_are_never_read(project, name):
    write(project, f"src/{name}.py", "def check():\n    return 'DO_NOT_INCLUDE_SECRET_FILE'\n")
    evidence = evidence_for(project, f"from {name} import check\ndef test_failure():\n    assert check() == 2\n")
    result = enrich_evidence(evidence, project)
    assert result["implementation_references"] == []
    assert "DO_NOT_INCLUDE_SECRET_FILE" not in json.dumps(result)


def test_symlink_escape_is_rejected(project, tmp_path):
    outside = write(tmp_path, "outside.py", "def check():\n    return 'OUTSIDE_SECRET'\n")
    link = project / "worker.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    result = enrich_evidence(evidence_for(project, "from worker import check\ndef test_failure():\n    assert check() == 2\n"), project)
    assert result["implementation_references"] == []
    assert "OUTSIDE_SECRET" not in json.dumps(result)


def test_traversal_reference_cannot_trigger_source_read(project, tmp_path):
    write(tmp_path, "outside.py", "def check():\n    return 'OUTSIDE_SECRET'\n")
    evidence = prepare_evidence('  File "../outside.py", line 2\nAssertionError: wrong', project)
    evidence["references"] = [{"path": "../outside.py", "line": 2, "excerpt": "forged"}]
    result = enrich_evidence(evidence, project)
    assert result["implementation_references"] == []
    assert result["context"]["files_examined"] == 0


def test_source_excerpt_redaction_and_line_coordinates(project):
    write(project, "src/worker.py", "def check():\n    password = 'implementation-private-value'\n    return 1\n")
    result = enrich_evidence(evidence_for(project, "from worker import check\ndef test_failure():\n    assert check() == 2\n"), project)
    row = result["implementation_references"][0]
    assert (row["line"], row["end_line"]) == (1, 3)
    assert "implementation-private-value" not in row["excerpt"]
    assert "2 |     password = '[REDACTED]'" in row["excerpt"]
    assert result["context"]["redaction_count"] >= 1


def test_hop_limit_and_cycles_do_not_expand_unboundedly(project):
    write(project, "src/pkg/__init__.py", "from .one import check\n")
    write(project, "src/pkg/one.py", "from .two import check\n")
    write(project, "src/pkg/two.py", "from .three import check\n")
    write(project, "src/pkg/three.py", "def check():\n    return 1\n")
    result = enrich_evidence(evidence_for(project, "from pkg import check\ndef test_failure():\n    assert check() == 2\n"), project)
    assert result["implementation_references"] == []
    assert result["context"]["truncated"]
    assert result["context"]["files_examined"] <= 8


def test_file_snippet_and_serialized_context_budgets(project):
    imports = []
    calls = []
    for index in range(10):
        write(project, f"src/mod{index}.py", "def check():\n" + "    value = '界'\n" * 300 + "    return 1\n")
        imports.append(f"from mod{index} import check as call{index}")
        calls.append(f"    call{index}()")
    evidence = evidence_for(project, "\n".join(imports) + "\ndef test_failure():\n" + "\n".join(calls) + "\n    assert False\n")
    result = enrich_evidence(evidence, project)
    added = {key: result[key] for key in ("implementation_references", "context")}
    assert result["context"]["files_examined"] <= 8
    assert len(result["implementation_references"]) <= 4
    assert len(json.dumps(added)) <= 8000
    assert result["context"]["truncated"]


def test_large_source_fails_closed_without_reading_unbounded_data(project):
    write(project, "src/worker.py", "# large\n" * 20_000 + "def check():\n    return 1\n")
    result = enrich_evidence(evidence_for(project, "from worker import check\ndef test_failure():\n    assert check() == 2\n"), project)
    assert result["implementation_references"] == []
    assert any(row["reason"] == "source_byte_limit" for row in result["context"]["unresolved"])


def test_reference_outside_function_does_not_expand_entire_module(project):
    path = write(project, "tests/test_failure.py", "from worker import check\ndef test_failure():\n    check()\n")
    result = enrich_evidence(prepare_evidence(f'  File "{path}", line 1\nAssertionError: failed', project), project)
    assert result["implementation_references"] == []
    assert any(row["reason"] == "reference_not_in_function" for row in result["context"]["unresolved"])
