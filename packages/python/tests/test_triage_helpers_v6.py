import ast
import copy
import hashlib

import pytest

from decilist import triage_verify_v6 as runner


def source(body=None, extra="", leaf=""):
    body = body or "    values = iter(values)\n    for value in values:\n        if value:\n            return itertools.chain([value], values)\n    return []\n"
    return "import itertools\n\ndef normalize(values):\n" + body + "\n" + leaf + extra + "\nclass Holder:\n    def __init__(self, values):\n        self.items = iter(values)\n"


def evidence(text, names=("normalize",)):
    tree = ast.parse(text)
    constructor = next(node for node in tree.body if isinstance(node, ast.ClassDef)).body[0]
    lines = text.splitlines(keepends=True)
    complete = lambda node: "".join(lines[node.lineno - 1:node.end_lineno])
    anchor = {"id": "f1", "evidence_id": "s1", "path": "src/sample.py", "symbol": "Holder.__init__",
              "start_line": constructor.lineno, "end_line": constructor.end_lineno, "source": complete(constructor),
              "editable": True, "complete": True, "roles": ["implementation"], "guard_reasons": []}
    helpers = []
    for name in names:
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
        body = complete(node)
        helpers.append({"evidence_id": "h" + str(len(helpers) + 1), "path": "src/sample.py", "symbol": name,
            "source": body, "start_line": node.lineno, "end_line": node.end_lineno,
            "source_sha256": hashlib.sha256(body.encode()).hexdigest(), "file_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "complete": True, "editable": False, "roles": ["static_helper"], "runtime_observed": False,
            "provenance": {"kind": "static_same_module_helper", "binding": "top_level_def",
                           "observed_constructor_id": "f1", "observed_constructor_evidence_id": "s1"}})
    return {"schema_version": 6, "functions": [anchor], "read_only_helpers": helpers,
            "evidence_ids": ["failure", "s1"] + [row["evidence_id"] for row in helpers],
            "edit_allowlist": [{"function_id": anchor["id"], **{key: anchor[key] for key in
                                 ("path", "evidence_id", "start_line", "end_line")}}]}


def setup(tmp_path, text):
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src/sample.py").write_bytes(text.encode())
    return runner.snapshot_project(root, tmp_path / "copy")


def patch(snapshot, context, new="normalize(values)", *, extra=()):
    return runner.apply_replacements(snapshot, [{"path": "src/sample.py", "old": "self.items = iter(values)",
        "new": "self.items = " + new}, *extra], context=context,
        evidence_ids=context["evidence_ids"] if context else ())


def test_only_canonical_cited_read_only_helper_link_is_admitted(tmp_path):
    text = source()
    snapshot = setup(tmp_path, text)
    result = patch(snapshot, evidence(text))
    assert result["admitted_static_helpers"] == ["normalize"]
    assert runner.check_snapshot_integrity(snapshot, expected_hashes=result["hashes_after"])["snapshot_unchanged"]
    assert runner._source_unchanged(snapshot)
    assert result["screening"] == "conservative_ast_change_screen_v6_cited_helpers"


def test_canonical_helper_bytes_preserve_crlf(tmp_path):
    text = source().replace("\n", "\r\n")
    snapshot = setup(tmp_path, text)
    context = evidence(text)
    helper = context["read_only_helpers"][0]["source"].encode()
    result = patch(snapshot, context)
    candidate = (snapshot.root / "src/sample.py").read_bytes()
    assert result["admitted_static_helpers"] == ["normalize"] and helper in candidate
    assert candidate.count(b"\n") == candidate.count(b"\r\n")


@pytest.mark.parametrize("field,value", [("source", "def normalize(values):\n    return values\n"),
    ("source_sha256", "0" * 64), ("file_sha256", "0" * 64), ("path", "src/unrelated.py"),
    ("complete", False), ("editable", True), ("runtime_observed", True), ("roles", ["implementation"]),
    ("symbol", "unrelated"), ("start_line", 99), ("end_line", 99)])
def test_changed_incomplete_or_unrelated_helper_evidence_is_rejected(tmp_path, field, value):
    text = source()
    snapshot = setup(tmp_path, text)
    context = evidence(text)
    context["read_only_helpers"][0][field] = value
    with pytest.raises(ValueError):
        patch(snapshot, context)
    assert runner.check_snapshot_integrity(snapshot)["snapshot_unchanged"]


def test_missing_or_uncited_helper_cannot_enable_exemption(tmp_path):
    text = source()
    snapshot = setup(tmp_path, text)
    with pytest.raises(ValueError):
        patch(snapshot, None)
    context = evidence(text)
    with pytest.raises(ValueError):
        runner.apply_replacements(snapshot, [{"path": "src/sample.py", "old": "self.items = iter(values)", "new": "self.items = normalize(values)"}],
            context=context, evidence_ids=["failure", "s1"])
    context["schema_version"] = 5
    context.pop("read_only_helpers")
    with pytest.raises(ValueError):
        patch(snapshot, context)


def test_duplicate_helper_evidence_ids_are_rejected(tmp_path):
    text = source("    return leaf(values)\n", leaf="def leaf(values):\n    return values\n")
    snapshot = setup(tmp_path, text)
    context = evidence(text, ("normalize", "leaf"))
    context["read_only_helpers"][1]["evidence_id"] = "h1"
    with pytest.raises(ValueError, match="unique"):
        patch(snapshot, context)


@pytest.mark.parametrize("extra", ["normalize = list\n", "iter = list\n", "itertools.chain = list\n",
    "itertools = None\n", "def other(normalize):\n    return normalize\n",
    "def other():\n    global normalize\n    normalize = list\n", "globals()['normalize'] = list\n",
    "def other():\n    global normalize\n    return normalize\n", "namespace = globals\n",
    "try:\n    pass\nexcept Exception as normalize:\n    pass\n",
    "match None:\n    case normalize:\n        pass\n",
    "match []:\n    case [*normalize]:\n        pass\n",
    "match {}:\n    case {**normalize}:\n        pass\n", "from unknown import *\n"])
def test_shadowed_rebound_or_dynamic_module_bindings_are_rejected(tmp_path, extra):
    text = source(extra=extra)
    snapshot = setup(tmp_path, text)
    with pytest.raises(ValueError):
        patch(snapshot, evidence(text))


@pytest.mark.parametrize("body", ["    return open('file')\n", "    return eval(values)\n",
    "    return values.execute()\n", "    return getattr(values, 'method')()\n",
    "    values[0] = 1\n    return values\n", "    values.attr = 1\n    return values\n",
    "    return values.__class__\n", "    return (lambda: values)()\n"])
def test_malicious_or_unknown_helper_effects_are_rejected(tmp_path, body):
    text = source(body)
    snapshot = setup(tmp_path, text)
    with pytest.raises(ValueError):
        patch(snapshot, evidence(text))
    assert runner.check_snapshot_integrity(snapshot)["snapshot_unchanged"]


@pytest.mark.parametrize("expression", ["alias(values)", "normalize(values.__class__)",
    "getattr(values, 'normalize')(values)", "normalize(eval(values))"])
def test_indirect_calls_and_other_existing_ast_guards_are_not_bypassed(tmp_path, expression):
    text = source(extra="alias = normalize\n")
    snapshot = setup(tmp_path, text)
    with pytest.raises(ValueError):
        patch(snapshot, evidence(text), expression)


def test_helper_edits_import_changes_and_unrelated_caller_are_rejected_atomically(tmp_path):
    text = source()
    snapshot = setup(tmp_path, text)
    for extra in ([{"path": "src/sample.py", "old": "return []", "new": "return [1]"}],
                  [{"path": "src/sample.py", "old": "import itertools", "new": "import itertools\nimport os"}]):
        with pytest.raises(ValueError):
            patch(snapshot, evidence(text), extra=extra)
    context = evidence(text)
    context["functions"][0]["symbol"] = "Unrelated.__init__"
    with pytest.raises(ValueError):
        patch(snapshot, context)
    assert runner.check_snapshot_integrity(snapshot)["snapshot_unchanged"]


def test_new_module_level_helper_call_has_no_observed_constructor_link(tmp_path):
    text = source()
    snapshot = setup(tmp_path, text)
    with pytest.raises(ValueError):
        patch(snapshot, evidence(text), extra=[{"path": "src/sample.py", "old": "class Holder:",
            "new": "unrelated = normalize([])\n\nclass Holder:"}])
    assert runner.check_snapshot_integrity(snapshot)["snapshot_unchanged"]


def test_discovery_anchor_is_not_relabelled_as_the_proposed_call_edge(tmp_path):
    text = source() + "\nclass Other:\n    def __init__(self, values):\n        self.other = iter(values)\n"
    snapshot = setup(tmp_path, text)
    context = evidence(text)
    node = next(node for node in ast.parse(text).body if isinstance(node, ast.ClassDef) and node.name == "Other").body[0]
    anchor = {**context["functions"][0], "id": "f2", "evidence_id": "s2", "symbol": "Other.__init__",
              "start_line": node.lineno, "end_line": node.end_lineno,
              "source": "".join(text.splitlines(keepends=True)[node.lineno - 1:node.end_lineno])}
    context["functions"].append(anchor)
    context["evidence_ids"].append("s2")
    context["edit_allowlist"].append({"function_id": "f2", **{key: anchor[key] for key in
        ("path", "evidence_id", "start_line", "end_line")}})
    result = runner.apply_replacements(snapshot, [{"path": "src/sample.py", "old": "self.other = iter(values)",
        "new": "self.other = normalize(values)"}], context=context, evidence_ids=context["evidence_ids"])
    assert result["admitted_static_helpers"] == ["normalize"]


def test_decorated_helper_is_rejected(tmp_path):
    text = source().replace("def normalize", "@staticmethod\ndef normalize")
    snapshot = setup(tmp_path, text)
    with pytest.raises(ValueError):
        patch(snapshot, evidence(text))


def test_named_helper_does_not_bypass_existing_dangerous_builtin_screen(tmp_path):
    text = source().replace("def normalize(", "def open(")
    snapshot = setup(tmp_path, text)
    with pytest.raises(ValueError, match="Dangerous"):
        patch(snapshot, evidence(text, ("open",)), "open(values)")


def test_bounded_transitive_helper_chain_requires_each_canonical_citation(tmp_path):
    text = source("    return leaf(values)\n", leaf="def leaf(values):\n    return values\n")
    snapshot = setup(tmp_path, text)
    context = evidence(text, ("normalize", "leaf"))
    bad = copy.deepcopy(context)
    bad["evidence_ids"].remove("h2")
    with pytest.raises(ValueError):
        patch(snapshot, bad)
    result = patch(snapshot, context)
    assert result["admitted_static_helpers"] == ["leaf", "normalize"]


def test_transitive_cycle_is_rejected_before_new_entry_helper_is_admitted(tmp_path):
    text = source("    return leaf(values)\n", leaf="def leaf(values):\n    return other(values)\n\ndef other(values):\n    return leaf(values)\n")
    snapshot = setup(tmp_path, text)
    with pytest.raises(ValueError, match="cycle"):
        patch(snapshot, evidence(text, ("normalize", "leaf", "other")))


def test_frozen_existing_callable_shape_behavior_is_preserved(tmp_path):
    text = source(extra="def existing(values):\n    return normalize(values)\n")
    snapshot = setup(tmp_path, text)
    result = patch(snapshot, None)
    assert result["admitted_static_helpers"] == []


def test_transitive_depth_budget_is_rejected(tmp_path):
    names = ("normalize", "a", "b", "c", "d")
    leaf = "".join("def " + name + "(values):\n    return " + (names[index + 1] + "(values)" if index + 1 < len(names) else "values") + "\n\n"
                   for index, name in enumerate(names) if name != "normalize")
    text = source("    return a(values)\n", leaf=leaf)
    snapshot = setup(tmp_path, text)
    with pytest.raises(ValueError, match="budget"):
        patch(snapshot, evidence(text, names))
