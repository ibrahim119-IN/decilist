"""Source availability and fail-closed checks for the bounded V3 resolver."""
import copy
import importlib
import json
from pathlib import Path
import socket
import subprocess

import pytest

from decilist.triage_context import enrich_evidence as enrich_v2
from decilist.triage_context_v3 import enrich_evidence
from decilist.triage_evidence import prepare_evidence
from decilist.triage_inspect import inspect_evidence


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


def symbols(result):
    return [row["symbol"] for row in result["implementation_references"]]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_lexical_context_manager_binding_follows_actual_enter_return(project, asynchronous):
    prefix, enter = ("async ", "__aenter__") if asynchronous else ("", "__enter__")
    write(project, "src/pkg/__init__.py", "from .worker import Worker\n")
    write(project, "src/pkg/worker.py", f"class Worker:\n    {prefix}def {enter}(self):\n        return self\n    def check(self):\n        return helper()\n\ndef helper():\n    return 1\n")
    evidence = evidence_for(project, f"from pkg import Worker\n{prefix}def test_failure():\n    {prefix}with Worker() as worker:\n        assert worker.check() == 2\n")
    before = copy.deepcopy(evidence)
    result = enrich_evidence(evidence, project)
    assert evidence == before
    assert {key: result[key] for key in evidence} == evidence
    assert symbols(result)[:2] == ["Worker.check", "helper"]
    assert not any(name.endswith(".__init__") for name in symbols(result))
    row = result["implementation_references"][0]
    assert row["provenance"] == {
        "reference": {"path": "tests/test_failure.py", "line": 4},
        "caller": {"path": "tests/test_failure.py", "line": 4, "symbol": "test_failure"},
        "import_chain": [
            {"path": "tests/test_failure.py", "line": 1, "module": "pkg", "symbol": "Worker"},
            {"path": "src/pkg/__init__.py", "line": 1, "module": "pkg.worker", "symbol": "Worker"},
        ],
    }
    assert row["resolution"] == "static_python_call"
    assert any("implementation" in row["origins"] for row in inspect_evidence(result)["facts"]["source_references"])


def test_with_does_not_assume_enter_returns_context_constructor(project):
    write(project, "src/worker.py", "class Value:\n    def check(self):\n        return 1\n\nclass Manager:\n    def __enter__(self):\n        return Value()\n    def check(self):\n        return 'WRONG_RECEIVER'\n")
    result = enrich_evidence(evidence_for(project, "from worker import Manager\ndef test_failure():\n    with Manager() as value:\n        assert value.check() == 2\n"), project)
    assert "Value.check" in symbols(result)
    assert "Manager.check" not in symbols(result)


@pytest.mark.parametrize("body", [
    "    with Worker() as worker:\n        pass\n    assert worker.check() == 2\n",
    "    with Worker() as worker:\n        worker = unknown\n        assert worker.check() == 2\n",
    "    with Worker() as worker:\n        import missing as worker\n        assert worker.check() == 2\n",
    "    with Worker() as worker:\n        def worker():\n            pass\n        assert worker.check() == 2\n",
    "    with Worker() as worker:\n        for worker in unknown:\n            pass\n        assert worker.check() == 2\n",
    "    if unknown:\n        with Worker() as worker:\n            assert worker.check() == 2\n",
])
def test_with_ambiguous_or_expired_binding_is_unresolved(project, body):
    write(project, "src/worker.py", "class Worker:\n    def __enter__(self):\n        return self\n    def check(self):\n        return 1\n")
    result = enrich_evidence(evidence_for(project, "from worker import Worker\ndef test_failure():\n" + body), project)
    assert "Worker.check" not in symbols(result)
    assert any(row["symbol"] == "worker.check" for row in result["context"]["unresolved"])


@pytest.mark.parametrize("enter", [
    "    def __enter__(self):\n        if unknown:\n            return self\n        return unknown\n",
    "    def __enter__(self) -> Worker:\n        return unknown\n",
    "    @unknown_decorator\n    def __enter__(self):\n        return self\n",
    "",
])
def test_context_entry_requires_body_proof(project, enter):
    write(project, "src/worker.py", "class Worker:\n" + enter + "    def check(self):\n        return 1\n")
    result = enrich_evidence(evidence_for(project, "from worker import Worker\ndef test_failure():\n    with Worker() as worker:\n        assert worker.check() == 2\n"), project)
    assert "Worker.check" not in symbols(result)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_context_protocol_requires_matching_sync_or_async_entry(project, asynchronous):
    prefix, entry_prefix, name = ("async ", "", "__aenter__") if asynchronous else ("", "async ", "__enter__")
    write(project, "src/worker.py", f"class Worker:\n    {entry_prefix}def {name}(self):\n        return self\n    def check(self):\n        return 1\n")
    result = enrich_evidence(evidence_for(project, f"from worker import Worker\n{prefix}def test_failure():\n    {prefix}with Worker() as worker:\n        assert worker.check() == 2\n"), project)
    assert "Worker.check" not in symbols(result)


@pytest.mark.parametrize("declaration", [
    "@unknown_decorator\nclass Worker:",
    "class Worker(metaclass=unknown):",
    "class Worker:\n    def __new__(cls):\n        return unknown",
])
def test_dynamic_constructor_cannot_prove_chained_receiver(project, declaration):
    write(project, "src/worker.py", declaration + "\n    def check(self):\n        return 1\n")
    result = enrich_evidence(evidence_for(project, "from worker import Worker\ndef test_failure():\n    assert Worker().check() == 2\n"), project)
    assert "Worker.check" not in symbols(result)


def make_pipeline(project):
    write(project, "vendor/pkg/__init__.py", "from .unused import *\nfrom .values import *\n__all__ = ['URL', 'Params']\n")
    write(project, "vendor/pkg/unused.py", "__all__ = ['unused']\ndef unused():\n    return 0\n")
    write(project, "vendor/pkg/values.py", "__all__ = ['URL', 'Params']\nclass Params:\n    def get_list(self, key):\n        return []\n    def add(self, key, value) -> 'Params':\n        result = Params()\n        result.value = value\n        return result\n    def merge(self, value) -> 'Params':\n        result = Params()\n        result.value = value\n        return result\n\nclass URL:\n    @property\n    def params(self) -> Params:\n        return Params()\n    def copy_with(self, **kwargs) -> 'URL':\n        return URL()\n    def copy_add(self, key, value) -> 'URL':\n        return self.copy_with(params=self.params.add(key, value))\n    def copy_merge(self, value) -> 'URL':\n        return self.copy_with(params=self.params.merge(value))\n")


def test_declared_vendor_exports_and_chained_local_returns_add_implementation(project):
    make_pipeline(project)
    evidence = evidence_for(project, "import pkg\ndef test_failure():\n    original = pkg.URL()\n    transformed = original.copy_merge({'key': 'new'}).copy_add('key', 'tail')\n    assert transformed.params.get_list('key') == ['new', 'tail']\n")
    assert enrich_v2(evidence, project)["implementation_references"] == []
    result = enrich_evidence(evidence, project)
    assert symbols(result)[:4] == ["Params.get_list", "URL.params", "URL.copy_add", "URL.copy_merge"]
    assert {"Params.add", "Params.merge", "URL.copy_with"}.issubset(symbols(result))
    first = result["implementation_references"][0]
    assert first["path"] == "vendor/pkg/values.py"
    assert first["provenance"]["import_chain"] == [
        {"path": "tests/test_failure.py", "line": 1, "module": "pkg", "symbol": "<module>"},
        {"path": "vendor/pkg/__init__.py", "line": 2, "module": "pkg.values", "symbol": "URL"},
    ]
    property_rows = [row for row in result["implementation_references"] if row["resolution"] == "static_python_property"]
    assert [row["symbol"] for row in property_rows] == ["URL.params"]


@pytest.mark.parametrize("mutation", [
    "__all__.append('other')\n",
    "exports = __all__\nexports.append('other')\n",
    "__all__[0] = 'other'\n",
    "__all__[:] = ['other']\n",
    "modify(__all__)\n",
    "__all__ += ['other']\n",
])
def test_mutated_or_escaped_literal_export_list_is_unresolved(project, mutation):
    write(project, "src/pkg/__init__.py", "from .values import *\n__all__ = ['Value']\n" + mutation)
    write(project, "src/pkg/values.py", "__all__ = ['Value']\nclass Value:\n    def check(self):\n        return 1\n")
    result = enrich_evidence(evidence_for(project, "import pkg\ndef test_failure():\n    assert pkg.Value().check() == 2\n"), project)
    assert result["implementation_references"] == []


def test_chained_assignment_cannot_hide_export_list_alias_mutation(project):
    write(project, "src/pkg/__init__.py", "from .values import *\nexports = __all__ = ['Value']\nexports[:] = []\n")
    write(project, "src/pkg/values.py", "__all__ = ['Value']\nclass Value:\n    def check(self):\n        return 1\n")
    result = enrich_evidence(evidence_for(project, "import pkg\ndef test_failure():\n    assert pkg.Value().check() == 2\n"), project)
    assert result["implementation_references"] == []


@pytest.mark.parametrize("exports", ["", "__all__ = unknown\n", "__all__ = ['Other']\n"])
def test_star_reexport_requires_literal_name_on_both_sides(project, exports):
    write(project, "src/pkg/__init__.py", "from .values import *\n__all__ = ['Value']\n")
    write(project, "src/pkg/values.py", exports + "class Value:\n    def check(self):\n        return 1\n")
    result = enrich_evidence(evidence_for(project, "import pkg\ndef test_failure():\n    assert pkg.Value().check() == 2\n"), project)
    assert result["implementation_references"] == []


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("star_first", [False, True])
def test_overlapping_star_export_cannot_select_one_definition(project, direct, star_first):
    star = "from .one import *\n"
    other = "class Value:\n    def check(self):\n        return 2\n" if direct else "from .two import *\n"
    write(project, "src/pkg/__init__.py", (star + other if star_first else other + star) + "__all__ = ['Value']\n")
    for name in ("one", "two"):
        write(project, f"src/pkg/{name}.py", "__all__ = ['Value']\nclass Value:\n    def check(self):\n        return 1\n")
    result = enrich_evidence(evidence_for(project, "import pkg\ndef test_failure():\n    assert pkg.Value().check() == 2\n"), project)
    assert result["implementation_references"] == []


@pytest.mark.parametrize("factory", [
    "def make(value) -> Value:\n    return value\n",
    "def make(value) -> Value:\n    if value:\n        return Value()\n    return Other()\n",
    "def make(value) -> Value:\n    return Other()\n",
    "def make(value) -> Value | Other:\n    return Value()\n",
    "def make(value) -> Value:\n    return unknown_mock()\n",
    "@unknown_decorator\ndef make(value) -> Value:\n    return Value()\n",
    "def make(value) -> Value:\n    return make(value)\n",
])
def test_return_annotations_do_not_invent_runtime_types(project, factory):
    write(project, "src/values.py", "class Value:\n    def check(self):\n        return 1\nclass Other:\n    pass\n" + factory)
    result = enrich_evidence(evidence_for(project, "from values import make\ndef test_failure(value):\n    assert make(value).check() == 2\n"), project)
    assert "Value.check" not in symbols(result)
    assert result["context"]["unresolved"]


def test_conditional_module_rebinding_is_not_a_return_class_proof(project):
    write(project, "src/values.py", "class Value:\n    def check(self):\n        return 1\nif unknown:\n    Value = unknown_mock\ndef make() -> Value:\n    return Value()\n")
    result = enrich_evidence(evidence_for(project, "from values import make\ndef test_failure():\n    assert make().check() == 2\n"), project)
    assert "Value.check" not in symbols(result)


def test_async_factory_requires_explicit_await_before_instance_resolution(project):
    write(project, "src/values.py", "class Value:\n    def check(self):\n        return 1\nasync def make() -> Value:\n    return Value()\n")
    bad = enrich_evidence(evidence_for(project, "from values import make\ndef test_failure():\n    assert make().check() == 2\n"), project)
    assert "Value.check" not in symbols(bad)
    good = enrich_evidence(evidence_for(project, "from values import make\nasync def test_failure():\n    value = await make()\n    assert value.check() == 2\n"), project)
    assert "Value.check" in symbols(good)


def test_explicit_import_property_and_helper_behavior_is_preserved(project):
    write(project, "src/pkg/__init__.py", "from .values import Value\n")
    write(project, "src/pkg/values.py", "def helper():\n    return 1\nclass Value:\n    @property\n    def content(self):\n        return helper()\n")
    evidence = evidence_for(project, "from pkg import Value\ndef test_failure():\n    value = Value()\n    assert value.content == 2\n")
    before = enrich_v2(evidence, project)
    after = enrich_evidence(evidence, project)
    assert after["implementation_references"] == before["implementation_references"]


def test_only_explicit_local_python_is_read_without_execution_or_scanning(project, monkeypatch):
    write(project, "src/pkg/__init__.py", "from .values import *\n__all__ = ['Value']\n")
    write(project, "src/pkg/values.py", "raise RuntimeError('DO_NOT_EXECUTE')\n__all__ = ['Value']\nclass Value:\n    def check(self):\n        password = 'implementation-private-value'\n        return 1\n")
    write(project, "src/unrelated.py", "raise RuntimeError('UNRELATED')\n")
    evidence = evidence_for(project, "import pkg\ndef test_failure():\n    assert pkg.Value().check() == 2\n")
    original_open = Path.open
    opened = []

    def guarded(path, *args, **kwargs):
        opened.append(path.relative_to(project).as_posix())
        assert path.resolve().is_relative_to(project)
        assert path.suffix == ".py"
        assert path.name != "unrelated.py"
        return original_open(path, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("Import, execution, network or directory scanning is forbidden")

    monkeypatch.setattr(Path, "open", guarded)
    monkeypatch.setattr(Path, "glob", forbidden)
    monkeypatch.setattr(Path, "rglob", forbidden)
    monkeypatch.setattr(Path, "iterdir", forbidden)
    monkeypatch.setattr(importlib, "import_module", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    result = enrich_evidence(evidence, project)
    assert set(opened) == {"tests/test_failure.py", "src/pkg/__init__.py", "src/pkg/values.py"}
    assert "Value.check" in symbols(result)
    assert "implementation-private-value" not in json.dumps(result)
    assert "DO_NOT_EXECUTE" not in json.dumps(result)
    assert result["implementation_references"][0]["excerpt"].startswith("4 |     def check(self):")
    assert result["context"]["redaction_count"] >= 1


@pytest.mark.parametrize("name", ["secrets", "credentials", "private_key"])
def test_star_edge_does_not_bypass_secret_path_protection(project, name):
    write(project, "src/pkg/__init__.py", f"from .{name} import *\n__all__ = ['Value']\n")
    write(project, f"src/pkg/{name}.py", "__all__ = ['Value']\nclass Value:\n    pass\n")
    result = enrich_evidence(evidence_for(project, "import pkg\ndef test_failure():\n    assert pkg.Value() == 2\n"), project)
    assert result["implementation_references"] == []
    assert result["context"]["files_examined"] == 2


def test_explicit_star_source_symlink_escape_is_rejected(project, tmp_path):
    outside = write(tmp_path, "outside.py", "__all__ = ['Value']\nclass Value:\n    pass\n")
    write(project, "src/pkg/__init__.py", "from .values import *\n__all__ = ['Value']\n")
    try:
        (project / "src/pkg/values.py").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    result = enrich_evidence(evidence_for(project, "import pkg\ndef test_failure():\n    assert pkg.Value() == 2\n"), project)
    assert result["implementation_references"] == []
    assert result["context"]["files_examined"] == 2


def test_explicit_star_edges_are_bounded_and_partial_search_is_not_a_proof(project):
    imports = [f"from .part{index} import *" for index in range(17)]
    write(project, "src/pkg/__init__.py", "\n".join(imports) + "\n__all__ = ['Value']\n")
    write(project, "src/pkg/part0.py", "__all__ = ['Value']\nclass Value:\n    pass\n")
    result = enrich_evidence(evidence_for(project, "import pkg\ndef test_failure():\n    assert pkg.Value() == 2\n"), project)
    assert result["implementation_references"] == []
    assert result["context"]["files_examined"] == 2
    assert result["context"]["truncated"]


def test_v3_limits_are_reported_and_enforced(project):
    imports, calls = [], []
    for index in range(24):
        write(project, f"src/mod{index}.py", "def check():\n" + "    value = '界'\n" * 120 + "    return 1\n")
        imports.append(f"from mod{index} import check as call{index}")
        calls.append(f"    call{index}()")
    result = enrich_evidence(evidence_for(project, "\n".join(imports) + "\ndef test_failure():\n" + "\n".join(calls) + "\n    assert False\n"), project)
    limits = result["context"]["limits"]
    assert limits == {"max_hops": 3, "max_import_hops": 3, "max_files": 20,
                      "max_snippets": 8, "max_context_chars": 8000,
                      "max_source_bytes": 128000, "max_star_imports": 16,
                      "max_resolution_steps": 1024, "max_type_hops": 6}
    assert result["context"]["files_examined"] <= limits["max_files"]
    assert len(result["implementation_references"]) <= limits["max_snippets"]
    assert len(json.dumps({key: result[key] for key in ("implementation_references", "context")})) <= 8000
    assert result["context"]["truncated"]


def test_forged_reference_cannot_trigger_unobserved_source_read(project):
    write(project, "src/unobserved.py", "def check():\n    return 1\n")
    evidence = prepare_evidence("AssertionError: wrong", project)
    evidence["references"] = [{"path": "src/unobserved.py", "line": 2, "excerpt": "forged"}]
    result = enrich_evidence(evidence, project)
    assert result["implementation_references"] == []
    assert result["context"]["files_examined"] == 0


def test_pathologically_deep_expression_fails_closed(project):
    expression = "unknown" + ".member" * 1500
    result = enrich_evidence(evidence_for(project, f"def test_failure():\n    assert {expression}() == 2\n"), project)
    assert result["implementation_references"] == []
    assert result["context"]["unresolved"]


def test_source_byte_cap_remains_unchanged(project):
    write(project, "src/values.py", "# oversized\n" * 12000 + "def check():\n    return 1\n")
    result = enrich_evidence(evidence_for(project, "from values import check\ndef test_failure():\n    assert check() == 2\n"), project)
    assert result["implementation_references"] == []
    assert any(row["reason"] == "source_byte_limit" for row in result["context"]["unresolved"])
