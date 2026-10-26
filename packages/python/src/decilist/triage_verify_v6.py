"""V6 verification with frozen V5 execution and a narrow cited-helper screen.

No shared state is patched. The V5 worker deliberately emits schema5 runtime
traces; the V6 workflow records that provenance separately from context schema.
"""
import ast
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from . import triage_verify_v4 as _original
from . import triage_verify_v5 as _frozen

VERSION = "triage-v6-verification.1"
EXPLANATION = _frozen.EXPLANATION
Snapshot = _frozen.Snapshot
SnapshotLimits = _frozen.SnapshotLimits
snapshot_project = _frozen.snapshot_project
run_pytest = _frozen.run_pytest
validate_pytest_args = _frozen.validate_pytest_args
cleanup_snapshot = _frozen.cleanup_snapshot
check_snapshot_integrity = _frozen.check_snapshot_integrity
sanitized_environment = _frozen.sanitized_environment
_owned = _frozen._owned
_manifest = _frozen._manifest
_source_unchanged = _frozen._source_unchanged
_checked_directory = _frozen._checked_directory
_check_path = _frozen._check_path
_is_link = _frozen._is_link
_relative = _frozen._relative
_PATCH_BLOCKED_NAMES = _frozen._PATCH_BLOCKED_NAMES
_digest = _original._digest
_DEFAULT_PRODUCTION_ROOTS = _original._DEFAULT_PRODUCTION_ROOTS
_HELPER_BUILTINS = {"iter", "len"}
_HELPER_STDLIB = {("itertools", "chain")}
_DYNAMIC_BINDING_CALLS = {"globals", "locals", "setattr", "delattr", "exec", "eval", "__import__"}


def _functions(tree: ast.AST) -> dict[str, list]:
    result = {}
    def visit(node, prefix=""):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbol = prefix + node.name
            result.setdefault(symbol, []).append(node)
            for child in node.body:
                visit(child, symbol + ".<locals>.")
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                visit(child, prefix + node.name + ".")
        else:
            for child in ast.iter_child_nodes(node):
                visit(child, prefix)
    visit(tree)
    return result


def _source(text: str, node: ast.AST) -> str:
    start = min([node.lineno] + [item.lineno for item in getattr(node, "decorator_list", [])])
    return "".join(text.splitlines(keepends=True)[start - 1:node.end_lineno])


def _scoped_calls(tree: ast.AST):
    result, owners = {}, {}
    def visit(node, owner="<module>", prefix=""):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbol = prefix + node.name
            for child in [*node.decorator_list, node.args, node.returns]:
                if child is not None:
                    visit(child, owner, prefix)
            for child in node.body:
                visit(child, symbol, symbol + ".<locals>.")
            return
        if isinstance(node, ast.ClassDef):
            symbol = prefix + node.name
            for child in [*node.decorator_list, *node.bases, *node.keywords]:
                visit(child, owner, prefix)
            for child in node.body:
                visit(child, "<class:" + symbol + ">", symbol + ".")
            return
        if isinstance(node, ast.Lambda):
            for child in ast.iter_child_nodes(node):
                visit(child, "<lambda>", prefix)
            return
        if isinstance(node, ast.Call):
            result.setdefault(owner, []).append(node)
            owners[id(node)] = owner
        for child in ast.iter_child_nodes(node):
            visit(child, owner, prefix)
    visit(tree)
    return result, owners


def _unrebound(tree: ast.AST, name: str, *, definition: ast.AST | None = None, module_import=False) -> bool:
    """Conservative whole-module binding check, including nested shadowing."""
    for node in ast.walk(tree):
        if node is definition:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            return False
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)) and node.id == name:
            return False
        if isinstance(node, ast.arg) and node.arg == name:
            return False
        if isinstance(node, ast.ExceptHandler) and node.name == name:
            return False
        if isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
            return False
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == name:
            return False
        if isinstance(node, ast.MatchMapping) and node.rest == name:
            return False
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if isinstance(node, ast.ImportFrom) and alias.name == "*":
                    return False
                bound = alias.asname or alias.name.split(".")[0]
                if bound == name and not (module_import and isinstance(node, ast.Import)
                                           and len(node.names) == 1 and alias.name == name and alias.asname is None):
                    return False
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if node.attr == name or isinstance(node.value, ast.Name) and node.value.id == name:
                return False
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if isinstance(node.slice, ast.Constant) and node.slice.value == name:
                return False
    return True


def _screen_ast_changes(original: str, candidate: str, filename: str, *, context=None, evidence_ids=()) -> list[str]:
    """Admit only canonical cited static helper links; retain all old screens.

    This bounds additional callable shapes. Even iter/chain can dispatch user
    code; trusted project tests still run with the caller's OS permissions.
    """
    before, after = ast.parse(original, filename), ast.parse(candidate, filename)
    if sum(1 for _ in ast.walk(before)) > 50000 or sum(1 for _ in ast.walk(after)) > 50000:
        raise ValueError("Candidate AST exceeds the V6 screening budget")
    old_shapes = {ast.dump(node.func, include_attributes=False) for node in ast.walk(before) if isinstance(node, ast.Call)}
    unknown = [node for node in ast.walk(after) if isinstance(node, ast.Call)
               and ast.dump(node.func, include_attributes=False) not in old_shapes
               and not (isinstance(node.func, ast.Name) and node.func.id in _original._PURE_NEW_CALLS)]
    _, call_owners = _scoped_calls(after)
    if not unknown:
        _original._screen_ast_changes(original, candidate, filename)
        return []
    if not isinstance(context, dict) or context.get("schema_version") not in {5, 6}:
        raise ValueError("A new helper call needs explicit schema5/6 source evidence")
    rows = context.get("read_only_helpers", [])
    if not isinstance(rows, list) or len(rows) > 8 or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Static helper evidence exceeds bounded row limits")
    known_ids = context.get("evidence_ids", [])
    if (not isinstance(evidence_ids, (list, tuple)) or len(evidence_ids) > 8
            or any(not isinstance(item, str) for item in evidence_ids)
            or not isinstance(known_ids, list) or len(known_ids) > 512
            or any(not isinstance(item, str) for item in known_ids)):
        raise ValueError("Static helper citations must be bounded evidence IDs")
    helper_ids = [row.get("evidence_id") for row in rows]
    if (len(set(known_ids)) != len(known_ids) or any(not isinstance(item, str) for item in helper_ids)
            or len(set(helper_ids)) != len(helper_ids)):
        raise ValueError("Static helper evidence IDs must be unique")
    cited, known = set(evidence_ids), set(known_ids)
    before_functions, after_functions = _functions(before), _functions(after)
    imports = lambda tree: [ast.dump(node, include_attributes=False) for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    if imports(before) != imports(after):
        raise ValueError("Static helper admission does not allow import changes")
    if any(isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in _DYNAMIC_BINDING_CALLS
           for node in ast.walk(before)):
        raise ValueError("Static helper binding is ambiguous in a dynamically rebound module")
    file_hash = _digest(original.encode("utf-8"))
    checked, active = set(), set()
    node_budget = [0]

    def canonical(name: str):
        if name in _original._DANGEROUS_CALLS or name.startswith("__"):
            raise ValueError("Dangerous or dunder helper names are not admitted")
        matching = [row for row in rows if row.get("path") == filename and row.get("symbol") == name]
        definitions = [node for node in before.body if isinstance(node, ast.FunctionDef) and node.name == name]
        if len(matching) != 1 or len(definitions) != 1 or len(before_functions.get(name, [])) != 1:
            raise ValueError("Static helper must be a unique supplied top-level definition")
        row, node = matching[0], definitions[0]
        source = _source(original, node)
        if (row.get("evidence_id") not in cited & known or row.get("complete") is not True
                or row.get("editable") is not False or row.get("runtime_observed") is not False
                or row.get("roles") != ["static_helper"] or node.decorator_list
                or node.args.defaults or any(item is not None for item in node.args.kw_defaults)
                or row.get("source") != source or row.get("source_sha256") != _digest(source.encode("utf-8"))
                or row.get("file_sha256") != file_hash or row.get("start_line") != node.lineno
                or row.get("end_line") != node.end_lineno
                or type(row.get("start_line")) is not int or type(row.get("end_line")) is not int
                or len(source.encode("utf-8")) > 2400
                or not _unrebound(before, name, definition=node)):
            raise ValueError("Static helper evidence is uncited, changed, shadowed, or incomplete")
        actual_after = after_functions.get(name, [])
        if len(actual_after) != 1 or _source(candidate, actual_after[0]) != source or not _unrebound(after, name, definition=actual_after[0]):
            raise ValueError("A read-only helper or its binding changed in the candidate")
        provenance = row.get("provenance")
        if not isinstance(provenance, dict) or provenance.get("kind") != "static_same_module_helper" or provenance.get("binding") != "top_level_def":
            raise ValueError("Static helper needs its explicit same-module selection link")
        return row, node

    def check(name: str, depth=0):
        if name in active:
            raise ValueError("Static helper transitive cycle is not admitted")
        if name in checked:
            return
        if depth > 3 or len(checked | active) >= 8:
            raise ValueError("Static helper transitive budget exceeded")
        row, node = canonical(name)
        active.add(name)
        forbidden = (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal, ast.ClassDef, ast.AsyncFunctionDef,
                     ast.Lambda, ast.Await, ast.Yield, ast.YieldFrom, ast.With, ast.AsyncWith, ast.Delete)
        for item in ast.walk(node):
            node_budget[0] += 1
            if node_budget[0] > 1200:
                raise ValueError("Static helper AST budget exceeded")
            if item is not node and isinstance(item, (ast.FunctionDef, *forbidden)):
                raise ValueError("Static helper contains a dynamic or unsupported construct")
            if isinstance(item, (ast.Attribute, ast.Subscript)) and isinstance(item.ctx, (ast.Store, ast.Del)):
                raise ValueError("Static helper mutation through an object is not admitted")
            value = item.id if isinstance(item, ast.Name) else item.attr if isinstance(item, ast.Attribute) else ""
            if value.startswith("__") and value.endswith("__"):
                raise ValueError("Static helper dunder access is not admitted")
            if not isinstance(item, ast.Call):
                continue
            callee = item.func
            if isinstance(callee, ast.Name):
                if callee.id in _HELPER_BUILTINS:
                    if not _unrebound(before, callee.id) or not _unrebound(after, callee.id):
                        raise ValueError("A helper builtin is shadowed")
                else:
                    check(callee.id, depth + 1)
            elif isinstance(callee, ast.Attribute) and isinstance(callee.value, ast.Name) and (callee.value.id, callee.attr) in _HELPER_STDLIB:
                module = callee.value.id
                imported = [entry for entry in before.body if isinstance(entry, ast.Import)
                            and len(entry.names) == 1 and entry.names[0].name == module and entry.names[0].asname is None]
                if len(imported) != 1 or not _unrebound(before, module, module_import=True) or not _unrebound(after, module, module_import=True):
                    raise ValueError("A helper standard-library binding is not canonical")
            else:
                raise ValueError("Static helper contains an unknown or indirect callable")
        active.remove(name)
        checked.add(name)

    for call in unknown:
        if not isinstance(call.func, ast.Name):
            raise ValueError("Only a statically bound bare-name helper call may be admitted")
        name = call.func.id
        row, _ = canonical(name)
        provenance = row["provenance"]
        discoveries = [value for value in context.get("functions", []) if isinstance(value, dict)
                       and value.get("id") == provenance.get("observed_constructor_id")]
        if (len(discoveries) != 1 or discoveries[0].get("path") != filename
                or discoveries[0].get("evidence_id") != provenance.get("observed_constructor_evidence_id")
                or discoveries[0].get("evidence_id") not in known):
            raise ValueError("Static helper has no unique same-module discovery anchor")
        # Discovery provenance is not a runtime edge. The proposed call must
        # separately belong to a complete cited observed constructor source.
        anchors = [value for value in context.get("functions", []) if isinstance(value, dict)
                   and value.get("symbol") == call_owners.get(id(call)) and value.get("path") == filename]
        if len(anchors) != 1:
            raise ValueError("The proposed helper link has no unique observed caller")
        anchor = anchors[0]
        owners = after_functions.get(anchor.get("symbol"), [])
        original_owners = before_functions.get(anchor.get("symbol"), [])
        roles = anchor.get("roles", [])
        allowlist = context.get("edit_allowlist", [])
        if (anchor.get("path") != filename or anchor.get("complete") is not True or anchor.get("editable") is not True
                or not isinstance(roles, list) or "implementation" not in roles
                or set(roles) & {"test", "fixture", "conftest", "configuration"} or anchor.get("guard_reasons")
                or not anchor.get("symbol", "").endswith(".__init__")
                or anchor.get("evidence_id") not in cited & known or len(owners) != 1 or len(original_owners) != 1
                or anchor.get("source") != _source(original, original_owners[0])
                or not isinstance(allowlist, list) or not any(isinstance(item, dict)
                    and item.get("function_id") == anchor.get("id")
                    and all(item.get(key) == anchor.get(key) for key in ("path", "evidence_id", "start_line", "end_line"))
                    for item in allowlist)
                or call_owners.get(id(call)) != anchor.get("symbol")
                or call not in ast.walk(owners[0])):
            raise ValueError("The helper call is outside its cited observed implementation anchor")
        check(name)
    # Mask only the approved callee identities for the frozen AST filter. All
    # other imports, dunders, dangerous calls, and callable shapes are preserved.
    for call in unknown:
        call.func = ast.copy_location(ast.Name(id="str", ctx=ast.Load()), call.func)
    _original._screen_ast_changes(original, ast.unparse(after), filename)
    return sorted(checked)


def apply_replacements(snapshot: Snapshot, replacements: Sequence[Mapping[str, str]], *, allowed_production_roots: Sequence[str] = _DEFAULT_PRODUCTION_ROOTS, context: dict | None = None, evidence_ids: Sequence[str] = ()) -> dict[str, Any]:
    """Apply exact single-match, compiling production-Python edits only in the copy."""
    _owned(snapshot)
    if not isinstance(replacements, (list, tuple)) or not 1 <= len(replacements) <= 8:
        raise ValueError("A candidate needs one to eight structured replacements")
    if not isinstance(allowed_production_roots, (list, tuple)) or not allowed_production_roots:
        raise ValueError("Explicit permitted production roots are required")
    if any(not isinstance(root, str) for root in allowed_production_roots):
        raise ValueError("Permitted production roots must be relative project paths")
    # Flat-module projects can explicitly permit the project root. It is never
    # part of the default policy, and protected tests/configuration stay denied.
    roots = ["" if root in {".", "./"} else _relative(root.rstrip("/\\")) for root in allowed_production_roots]
    if _manifest(snapshot.root, snapshot.limits) != snapshot.source_hashes or not _source_unchanged(snapshot):
        raise ValueError("Project or copy changed before candidate application")
    plans, prepared, changed = {}, {}, []
    admitted_helpers = set()
    for row in replacements:
        if not isinstance(row, Mapping) or set(row) != {"path", "old", "new"}:
            raise ValueError("Replacements contain only path, old, and new")
        relative = _relative(row["path"])
        pure = PurePosixPath(relative)
        lowered = pure.name.casefold()
        blocked = any(part.casefold() in {"test", "tests", "testing", "config", "configs", "configuration", "manifest", "manifests"} or part.startswith(".") for part in pure.parts)
        if pure.suffix != ".py" or lowered.startswith("test_") or lowered.endswith(("_test.py", "_config.py", "_settings.py", "_manifest.py")) or lowered in _PATCH_BLOCKED_NAMES or blocked or not any(not root or relative.startswith(root + "/") for root in roots):
            raise ValueError("Candidate patches may change only permitted production Python files")
        if relative not in snapshot.source_hashes:
            raise ValueError("Candidate files must be copied existing files")
        old, new = row["old"], row["new"]
        if not isinstance(old, str) or not old or not isinstance(new, str) or old == new or len(old.encode("utf-8")) + len(new.encode("utf-8")) > 128 * 1024:
            raise ValueError("Candidate replacement strings must be nonempty, different, and bounded")
        path = snapshot.root / relative
        if _is_link(path):
            raise ValueError("Candidate file became a symbolic link or reparse point")
        data = path.read_bytes()
        if _digest(data) != snapshot.source_hashes[relative]:
            raise ValueError("Candidate source was modified before application")
        text = data.decode("utf-8")
        if text.count(old) != 1:
            raise ValueError("Each candidate old string must match exactly once")
        start = text.index(old)
        end = start + len(old)
        plan = plans.setdefault(relative, {"text": text, "data": data, "edits": []})
        if any(start < previous_end and previous_start < end for previous_start, previous_end, _ in plan["edits"]):
            raise ValueError("Candidate replacements in one file must not overlap")
        plan["edits"].append((start, end, new))
        changed.append({"path": relative, "sha256_before": _digest(data), "old_sha256": _digest(old.encode()), "new_sha256": _digest(new.encode())})
    for relative, plan in plans.items():
        candidate = plan["text"]
        for start, end, replacement in sorted(plan["edits"], reverse=True):
            candidate = candidate[:start] + replacement + candidate[end:]
        compile(candidate, relative, "exec", dont_inherit=True)
        try:
            admitted_helpers.update(_screen_ast_changes(plan["text"], candidate, relative, context=context, evidence_ids=evidence_ids))
        except (RecursionError, MemoryError) as exc:
            raise ValueError("V6 helper AST screening budget exceeded") from exc
        encoded = candidate.encode("utf-8")
        if len(encoded) > snapshot.limits.max_file_bytes:
            raise ValueError("Patched file exceeds the snapshot file limit")
        prepared[relative] = encoded
    for row in changed:
        row["sha256_after"] = _digest(prepared[row["path"]])
    for relative, data in prepared.items():
        (snapshot.root / relative).write_bytes(data)
    return {"replacements": changed, "hashes_after": _manifest(snapshot.root, snapshot.limits), "original_unchanged": _source_unchanged(snapshot), "screening": "conservative_ast_change_screen_v6_cited_helpers", "admitted_static_helpers": sorted(admitted_helpers), "screening_limitation": "Existing callable shapes can still have effects; this is not a security sandbox."}
