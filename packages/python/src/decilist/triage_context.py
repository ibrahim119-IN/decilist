"""Bounded static Python call/property context; imports and executes no project code.

Only a log-referenced failing function and its explicit local call/import chain
are inspected. Resolution is intentionally incomplete: dynamic dispatch,
inheritance, star imports, and unavailable source are reported, not guessed.
All excerpts remain untrusted data and credential redaction is heuristic.
"""
from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass, replace
import io
import json
from pathlib import Path
import tokenize

from .triage_evidence import _location, _project_pattern, _sanitize, _source_path

__all__ = ["enrich_evidence"]
_LIMITS = {"max_hops": 3, "max_import_hops": 3, "max_files": 8, "max_snippets": 4,
           "max_context_chars": 8000, "max_source_bytes": 128000}
_BUILTINS = frozenset(dir(builtins))


@dataclass
class _Module:
    path: str
    name: str | None
    package: bool
    tree: ast.Module
    lines: list[str]


@dataclass
class _Symbol:
    module: _Module
    node: ast.AST
    name: str
    owner: ast.ClassDef | None = None
    instance: bool = False


@dataclass
class _ModuleName:
    name: str
    explicit: tuple[str, ...] = ()


def _display(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _display(node.value) + "." + node.attr
    if isinstance(node, ast.Call):
        return _display(node.func) + "(...)"
    return "<dynamic>"


def _property(symbol: _Symbol) -> bool:
    return any(isinstance(node, ast.Name) and node.id == "property"
               for node in getattr(symbol.node, "decorator_list", []))


def _scope_nodes(node: ast.AST):
    """Visit one executed lexical scope, excluding unrelated nested definitions."""
    yield node
    children = node.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else ast.iter_child_nodes(node)
    for child in children:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield from _scope_nodes(child)


class _Context:
    def __init__(self, project: Path):
        self.project = project
        self.pattern = _project_pattern(project)
        self.modules: dict[str, _Module | None] = {}
        self.rows: list[dict] = []
        self.unresolved: list[dict] = []
        self.examined = 0
        self.redactions = 0
        self.truncated = False
        self.seen: set[tuple[str, int]] = set()
        self.roots = (Path("packages/python/src"), Path("src"), Path("vendor"), Path("."))

    def missing(self, path: str, line: int, symbol: str, reason: str):
        row = {"path": path, "line": line, "symbol": symbol[:128], "reason": reason}
        if row not in self.unresolved:
            if len(self.unresolved) < 16:
                self.unresolved.append(row)
            else:
                self.truncated = True

    def module_name(self, path: str) -> tuple[str | None, bool]:
        location = Path(path)
        for root in self.roots:
            try:
                relative = location.relative_to(root)
            except ValueError:
                continue
            parts = list(relative.with_suffix("").parts)
            package = bool(parts and parts[-1] == "__init__")
            if package:
                parts.pop()
            if parts and all(part.isidentifier() for part in parts):
                return ".".join(parts), package
        return None, False

    def load_path(self, path: str) -> _Module | None:
        if path in self.modules:
            return self.modules[path]
        source = _source_path(path, self.project)
        if source is None or not path.endswith(".py"):
            return None
        resolved, relative = source
        if relative in self.modules:
            return self.modules[relative]
        if self.examined >= _LIMITS["max_files"]:
            self.truncated = True
            return None
        self.examined += 1
        self.modules[relative] = None
        try:
            with resolved.open("rb") as handle:
                data = handle.read(_LIMITS["max_source_bytes"] + 1)
            if len(data) > _LIMITS["max_source_bytes"]:
                self.truncated = True
                self.missing(relative, 1, "<module>", "source_byte_limit")
                return None
            encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
            text = data.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
            tree = ast.parse(text, filename=relative)
            sanitized, redactions = _sanitize(text, self.pattern)
            if sanitized.count("\n") != text.count("\n"):
                self.missing(relative, 1, "<module>", "sanitization_changed_line_layout")
                return None
        except (OSError, SyntaxError, UnicodeError, LookupError, ValueError, RecursionError):
            self.missing(relative, 1, "<module>", "unreadable_or_malformed_python")
            return None
        self.redactions += redactions
        name, package = self.module_name(relative)
        module = _Module(relative, name, package, tree, sanitized.split("\n"))
        self.modules[relative] = module
        return module

    def load_module(self, name: str) -> _Module | None:
        if not name or not all(part.isidentifier() for part in name.split(".")):
            return None
        suffix = Path(*name.split("."))
        for root in self.roots:
            for candidate in (root / suffix.with_suffix(".py"), root / suffix / "__init__.py"):
                module = self.load_path(candidate.as_posix())
                if module is not None:
                    return module
        return None

    def imports(self, module: _Module, scope: ast.AST | None = None):
        nodes = list(module.tree.body)
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nodes += list(scope.body)
        result = {}
        for node in nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    binding = alias.asname or alias.name.split(".")[0]
                    name = alias.name if alias.asname else binding
                    result[binding] = (_ModuleName(name, (alias.name,)), node, alias.name, "<module>")
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    package = (module.name or "").split(".")
                    if not module.package:
                        package = package[:-1]
                    if not package or node.level > len(package):
                        continue
                    base = ".".join(package[:len(package) - node.level + 1] + ([base] if base else []))
                for alias in node.names:
                    if alias.name != "*":
                        result[alias.asname or alias.name] = ((base, alias.name), node, base, alias.name)
        return result

    @staticmethod
    def definition(module: _Module, name: str) -> _Symbol | None:
        for node in module.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
                return _Symbol(module, node, name)
        return None

    @staticmethod
    def shadowed(module: _Module, name: str) -> bool:
        bindings = 0
        for node in module.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bindings += node.name == name
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                bindings += sum((alias.asname or (alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name)) == name for alias in node.names)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                bindings += any(isinstance(target, ast.Name) and target.id == name for target in
                                (node.targets if isinstance(node, ast.Assign) else [node.target]))
        return bindings > 1

    def exported(self, module: _Module, name: str, chain: list[dict], hops: int, visited: set):
        key = (module.path, name)
        if key in visited or hops > _LIMITS["max_import_hops"] or self.shadowed(module, name):
            return None
        direct = self.definition(module, name)
        if direct:
            return direct, chain, hops
        binding = self.imports(module).get(name)
        if binding is None:
            return None
        return self.imported(module, binding, chain, hops, visited | {key})

    def imported(self, module: _Module, binding, chain: list[dict], hops: int, visited: set):
        target, node, name, symbol = binding
        if hops >= _LIMITS["max_import_hops"]:
            self.truncated = True
            return None
        chain = chain + [{"path": module.path, "line": node.lineno, "module": name, "symbol": symbol}]
        hops += 1
        if isinstance(target, _ModuleName):
            return target, chain, hops
        imported_module = self.load_module(target[0])
        if imported_module is not None:
            found = self.exported(imported_module, target[1], chain, hops, visited)
            if found:
                return found
        # Explicit `from package import module` may name a local submodule.
        submodule = target[0] + "." + target[1]
        if self.load_module(submodule) is not None:
            return _ModuleName(submodule), chain, hops
        return None

    def resolve(self, expression: ast.AST, caller: _Symbol, line: int,
                chain: list[dict], hops: int, resolving: set[str] | None = None):
        resolving = resolving or set()
        if isinstance(expression, ast.Name):
            name = expression.id
            if name in resolving:
                return None
            # Only unconditional direct assignments are resolved; no branch/value execution.
            assignments = [node for node in _scope_nodes(caller.node)
                           if isinstance(node, (ast.Assign, ast.AnnAssign))
                           and any(isinstance(target, ast.Name) and target.id == name
                                   for target in (node.targets if isinstance(node, ast.Assign) else [node.target]))]
            if (len(assignments) == 1 and assignments[0] in getattr(caller.node, "body", [])
                    and assignments[0].lineno < line and assignments[0].value is not None):
                value = assignments[0].value
                if isinstance(value, ast.Call):
                    found = self.resolve(value, caller, assignments[0].lineno, chain, hops, resolving | {name})
                    return found if found and isinstance(found[0], _Symbol) and isinstance(found[0].node, ast.ClassDef) else None
                return self.resolve(value, caller, assignments[0].lineno, chain, hops, resolving | {name})
            if assignments:
                return None
            args = getattr(caller.node, "args", None)
            positional = [*args.posonlyargs, *args.args] if args else []
            decorators = {node.id for node in getattr(caller.node, "decorator_list", []) if isinstance(node, ast.Name)}
            if caller.owner is not None and positional and name == positional[0].arg and "staticmethod" not in decorators:
                return _Symbol(caller.module, caller.owner, caller.owner.name, instance="classmethod" not in decorators), chain, hops
            if args and name in {arg.arg for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs,
                                                    *([args.vararg] if args.vararg else []), *([args.kwarg] if args.kwarg else [])]}:
                return None
            if self.shadowed(caller.module, name):
                return None
            direct = self.definition(caller.module, name)
            if direct:
                return direct, chain, hops
            binding = self.imports(caller.module, caller.node).get(name)
            if binding and binding[1] in getattr(caller.node, "body", []) and binding[1].lineno >= line:
                return None
            return self.imported(caller.module, binding, chain, hops, set()) if binding else None
        if isinstance(expression, ast.Call):
            found = self.resolve(expression.func, caller, line, chain, hops, resolving)
            return (replace(found[0], instance=True), found[1], found[2]) if found and isinstance(found[0], _Symbol) and isinstance(found[0].node, ast.ClassDef) else None
        if isinstance(expression, ast.Attribute):
            found = self.resolve(expression.value, caller, line, chain, hops, resolving)
            if not found:
                return None
            target, chain, hops = found
            if isinstance(target, _ModuleName):
                submodule = target.name + "." + expression.attr
                if any(item == submodule or item.startswith(submodule + ".") for item in target.explicit):
                    return _ModuleName(submodule, target.explicit), chain, hops
                module = self.load_module(target.name)
                return self.exported(module, expression.attr, chain, hops, set()) if module else None
            if isinstance(target.node, ast.ClassDef):
                members = []
                for node in target.node.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == expression.attr:
                        members.append(node)
                if len(members) == 1:
                    member = _Symbol(target.module, members[0], target.name + "." + members[0].name, target.node)
                    if not _property(member) or target.instance:
                        return member, chain, hops
        return None

    def add(self, target: _Symbol, origin: dict, caller: _Symbol, call: ast.AST, chain: list[dict], hops: int,
            resolution: str = "static_python_call"):
        node = target.node
        if isinstance(node, ast.ClassDef):
            constructor = next((item for item in node.body if isinstance(item, ast.FunctionDef) and item.name == "__init__"), None)
            if constructor:
                target = _Symbol(target.module, constructor, target.name + ".__init__", node)
                node = constructor
        start = min([node.lineno] + [decorator.lineno for decorator in getattr(node, "decorator_list", [])])
        key = (target.module.path, start)
        if key in self.seen:
            return
        if len(self.rows) >= _LIMITS["max_snippets"]:
            self.truncated = True
            return
        self.seen.add(key)
        parts = []
        used = 0
        end = start - 1
        for index in range(start - 1, node.end_lineno):
            text = f"{index + 1} | {target.module.lines[index]}"
            available = 4000 - used - (1 if parts else 0)
            if available <= 0:
                break
            parts.append(text[:available])
            used += len(parts[-1]) + (1 if len(parts) > 1 else 0)
            end = index + 1
            if len(text) > available:
                break
        cut = end < node.end_lineno or (parts and len(text) > available)
        self.truncated |= bool(cut)
        self.rows.append({"path": target.module.path, "line": start, "end_line": end,
                          "excerpt": "\n".join(parts), "symbol": target.name,
                          "resolution": resolution, "truncated": bool(cut),
                          "provenance": {"reference": dict(origin),
                                         "caller": {"path": caller.module.path, "line": call.lineno, "symbol": caller.name},
                                         "import_chain": chain}})
        if hops < _LIMITS["max_hops"] and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.follow(target, origin, hops)

    def follow(self, caller: _Symbol, origin: dict, depth: int):
        calls = [node for node in _scope_nodes(caller.node) if isinstance(node, ast.Call)]
        raised = {id(node.exc) for node in _scope_nodes(caller.node)
                  if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)}
        candidates = []
        for call in calls:
            if isinstance(call.func, ast.Name) and call.func.id in _BUILTINS:
                continue
            # Exception construction in a raise does not identify the predicate
            # or operation that failed; do not let its class body crowd those out.
            if id(call) in raised and _display(call.func).rsplit(".", 1)[-1].endswith(("Error", "Exception")):
                continue
            found = self.resolve(call.func, caller, call.lineno, [], 0)
            if not found or not isinstance(found[0], _Symbol) or _property(found[0]):
                self.missing(caller.module.path, call.lineno, _display(call.func), "dynamic_or_not_explicit_local_source")
                continue
            target, chain, _ = found
            candidates.append((call, target, chain, "static_python_call"))
        for access in _scope_nodes(caller.node):
            if not isinstance(access, ast.Attribute) or not isinstance(access.ctx, ast.Load):
                continue
            found = self.resolve(access, caller, access.lineno, [], 0)
            if found and isinstance(found[0], _Symbol) and _property(found[0]):
                candidates.append((access, found[0], found[1], "static_python_property"))
        receiver_constructors = set()
        for call, target, _, resolution in candidates:
            access = call.func if isinstance(call, ast.Call) else call
            if not isinstance(access, ast.Attribute) or target.owner is None:
                continue
            receiver = access.value
            if isinstance(receiver, ast.Call):
                receiver_constructors.add(id(receiver))
            elif isinstance(receiver, ast.Name):
                for statement in getattr(caller.node, "body", []):
                    if isinstance(statement, (ast.Assign, ast.AnnAssign)) and statement.lineno < call.lineno:
                        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                        if any(isinstance(item, ast.Name) and item.id == receiver.id for item in targets) and isinstance(statement.value, ast.Call):
                            receiver_constructors.add(id(statement.value))
        focus = origin["line"] if origin["path"] == caller.module.path else None
        # Investigate called operations first, then constructor-only operations.
        # Distance to the actual failing line only applies inside its own function.
        candidates.sort(key=lambda row: (isinstance(row[1].node, ast.ClassDef),
                                         abs(row[0].lineno - focus) if focus is not None else row[0].lineno))
        for call, target, chain, resolution in candidates:
            if isinstance(target.node, ast.ClassDef) and id(call) in receiver_constructors:
                continue
            if len(self.rows) >= _LIMITS["max_snippets"]:
                self.truncated = True
                break
            self.add(target, origin, caller, call, chain, depth + 1, resolution)


def enrich_evidence(evidence: dict, project: Path) -> dict:
    """Copy prepared evidence and add bounded static implementation references.

    Call/property edges and explicit import/reexport edges are each limited to
    three hops. No source is imported, executed, searched repository-wide, or selected from
    an oracle catalog. Definitions are candidates for inspection, not diagnoses.
    Original evidence fields and references are preserved unchanged.
    """
    if not isinstance(evidence, dict) or not isinstance(project, Path):
        raise TypeError("Expected an evidence dictionary and pathlib.Path project")
    serialized = json.dumps(evidence, allow_nan=False)
    if len(serialized) > 24000:
        raise ValueError("Expected bounded prepared evidence")
    result = json.loads(serialized)
    if type(result.get("schema_version")) is not int or result.get("schema_version") != 1 or not isinstance(result.get("log"), str):
        raise ValueError("Expected prepared schema-v1 evidence")
    if not isinstance(result.get("references", []), list) or len(result["log"]) > 12000:
        raise ValueError("Expected bounded prepared log and reference list")
    if str(project).startswith(("\\\\", "//")):
        raise ValueError("Project must be a local directory")
    project = project.resolve(strict=True)
    if not project.is_dir():
        raise ValueError("Project must be a directory")
    context = _Context(project)
    locations = set()
    for line in result["log"][:12000].splitlines():
        location = _location(line)
        if location:
            source = _source_path(location[0], project)
            if source:
                locations.add((source[1], location[1]))
    for reference in result.get("references", [])[:3]:
        if not isinstance(reference, dict) or not isinstance(reference.get("path"), str) or type(reference.get("line")) is not int:
            continue
        source = _source_path(reference["path"], project)
        if source is None or (source[1], reference["line"]) not in locations:
            continue
        module = context.load_path(source[1])
        if module is None:
            continue
        candidates = [node for node in ast.walk(module.tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and node.lineno <= reference["line"] <= node.end_lineno]
        if not candidates:
            context.missing(module.path, reference["line"], "<reference>", "reference_not_in_function")
            continue
        function = min(candidates, key=lambda node: node.end_lineno - node.lineno)
        owner = next((node for node in ast.walk(module.tree) if isinstance(node, ast.ClassDef) and function in node.body), None)
        symbol = _Symbol(module, function, (owner.name + "." if owner else "") + function.name, owner)
        context.follow(symbol, {"path": source[1], "line": reference["line"]}, 0)
    metadata = {"schema_version": 1, "files_examined": context.examined,
                "snippets_added": len(context.rows), "truncated": context.truncated,
                "redaction_count": context.redactions, "unresolved": context.unresolved,
                "limits": dict(_LIMITS),
                "note": "Static explicit-call/property context only; dynamic resolution is not guessed. Excerpts are untrusted; redaction is heuristic."}
    added = {"implementation_references": context.rows, "context": metadata}
    while len(json.dumps(added)) > _LIMITS["max_context_chars"]:
        metadata["truncated"] = True
        if metadata["unresolved"]:
            metadata["unresolved"].pop()
        elif context.rows:
            context.rows.pop()
        else:
            break
    metadata["snippets_added"] = len(context.rows)
    result.update(added)
    return result
