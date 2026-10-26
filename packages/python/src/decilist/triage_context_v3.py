"""V3 bounded static Python call/property context; imports no project code.

Only a log-referenced failing function and its explicit local call/import chain
are inspected. Resolution is intentionally incomplete: dynamic dispatch,
inheritance, undeclared star imports, and unavailable source are not guessed.
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
_LIMITS = {"max_hops": 3, "max_import_hops": 3, "max_files": 20, "max_snippets": 8,
           "max_context_chars": 8000, "max_source_bytes": 128000,
           "max_star_imports": 16, "max_resolution_steps": 1024, "max_type_hops": 6}
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
        self.resolution_steps = 0
        self.type_stack: set[tuple[str, int]] = set()
        self.following = False
        self.pending: list[tuple[_Symbol, dict, int]] = []
        self.constructors: list[tuple] = []
        self.export_lists: dict[str, frozenset[str] | None] = {}

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
        return len(_Context.local_bindings(module.tree, name)) > 1

    def exported(self, module: _Module, name: str, chain: list[dict], hops: int, visited: set):
        key = (module.path, name)
        if key in visited or hops > _LIMITS["max_import_hops"] or self.shadowed(module, name):
            return None
        direct = self.definition(module, name)
        binding = self.imports(module).get(name)
        stars = [node for node in module.tree.body if isinstance(node, ast.ImportFrom)
                 and any(alias.name == "*" for alias in node.names)]
        if not stars:
            if direct:
                return direct, chain, hops
            return self.imported(module, binding, chain, hops, visited | {key}) if binding else None
        # A star edge is usable only when both sides explicitly declare the
        # requested export in a literal __all__. Inspect only named import
        # targets, never directory contents, and reject overlapping exporters.
        if name not in (self.literal_exports(module) or ()):
            return None
        if len(stars) > _LIMITS["max_star_imports"]:
            self.truncated = True
            return None
        matches = []
        for node in stars:
            base = self.import_base(module, node)
            child = self.load_module(base) if base else None
            # Missing source or a non-literal export list prevents proving
            # uniqueness among the barrel's explicit star sources.
            if child is None or self.literal_exports(child) is None:
                return None
            if name in self.literal_exports(child):
                found = self.imported(module, ((base, name), node, base, name),
                                      chain, hops, visited | {key})
                if found is None:
                    return None
                matches.append(found)
        if direct or binding:
            if matches:
                return None
            return (direct, chain, hops) if direct else self.imported(module, binding, chain, hops, visited | {key})
        return matches[0] if len(matches) == 1 else None

    def literal_exports(self, module: _Module) -> frozenset[str] | None:
        if module.path in self.export_lists:
            return self.export_lists[module.path]
        self.export_lists[module.path] = None
        declarations = [node for node in module.tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))
                        and any(isinstance(target, ast.Name) and target.id == "__all__" for target in
                                (node.targets if isinstance(node, ast.Assign) else [node.target]))]
        stores = [node for node in ast.walk(module.tree) if isinstance(node, ast.Name)
                  and node.id == "__all__" and isinstance(node.ctx, (ast.Store, ast.Del))]
        changes = [node for node in ast.walk(module.tree) if isinstance(node, ast.Attribute)
                   and isinstance(node.value, ast.Name) and node.value.id == "__all__"]
        parents = {id(child): node for node in ast.walk(module.tree) for child in ast.iter_child_nodes(node)}
        # Do not follow aliases or permit subscript/slice mutation or passing
        # the export list to unknown functions. Read-only iteration is allowed.
        escaping = [node for node in ast.walk(module.tree) if isinstance(node, ast.Name)
                    and node.id == "__all__" and isinstance(node.ctx, ast.Load)
                    and not (isinstance(parents.get(id(node)), (ast.For, ast.AsyncFor))
                             and parents[id(node)].iter is node)]
        if len(declarations) != 1 or len(stores) != 1 or changes or escaping:
            return None
        if isinstance(declarations[0], ast.Assign) and len(declarations[0].targets) != 1:
            return None
        value = declarations[0].value
        if not isinstance(value, (ast.List, ast.Tuple)) or len(value.elts) > 256:
            return None
        if not all(isinstance(item, ast.Constant) and isinstance(item.value, str)
                   and item.value.isidentifier() for item in value.elts):
            return None
        result = frozenset(item.value for item in value.elts)
        self.export_lists[module.path] = result
        return result

    @staticmethod
    def import_base(module: _Module, node: ast.ImportFrom) -> str | None:
        base = node.module or ""
        if node.level:
            package = (module.name or "").split(".")
            if not module.package:
                package = package[:-1]
            if not package or node.level > len(package):
                return None
            base = ".".join(package[:len(package) - node.level + 1] + ([base] if base else []))
        return base or None

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

    @staticmethod
    def local_bindings(scope: ast.AST, name: str) -> list[ast.AST]:
        bindings = []

        def visit(node):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name == name:
                    bindings.append(node)
                return
            if isinstance(node, ast.Lambda):
                return
            if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, (ast.Store, ast.Del)):
                bindings.append(node)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                if any((item.asname or (item.name.split(".")[0] if isinstance(node, ast.Import) else item.name)) == name
                       for item in node.names):
                    bindings.append(node)
            elif isinstance(node, ast.ExceptHandler) and node.name == name:
                bindings.append(node)
            elif isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
                bindings.append(node)
            for child in ast.iter_child_nodes(node):
                visit(child)

        for child in getattr(scope, "body", []):
            visit(child)
        return bindings

    def resolve(self, expression: ast.AST, caller: _Symbol, line: int,
                chain: list[dict], hops: int, resolving: set[str] | None = None):
        self.resolution_steps += 1
        if self.resolution_steps > _LIMITS["max_resolution_steps"]:
            self.truncated = True
            return None
        resolving = resolving or set()
        if isinstance(expression, ast.Name):
            name = expression.id
            if name in resolving:
                return None
            with_bindings = [(node, item) for node in _scope_nodes(caller.node)
                             if isinstance(node, (ast.With, ast.AsyncWith)) for item in node.items
                             if isinstance(item.optional_vars, ast.Name) and item.optional_vars.id == name]
            if with_bindings:
                stores = self.local_bindings(caller.node, name)
                if len(with_bindings) != 1 or len(stores) != 1:
                    return None
                statement, item = with_bindings[0]
                # Binding is valid only inside this lexical with body. A
                # conditional/loop enclosing the with is intentionally unknown.
                if (statement not in caller.node.body or not statement.body
                        or not statement.body[0].lineno <= line <= statement.end_lineno):
                    return None
                found = self.resolve(item.context_expr, caller, statement.lineno,
                                     chain, hops, resolving | {name})
                if not found or not isinstance(found[0], _Symbol) or not isinstance(found[0].node, ast.ClassDef):
                    return None
                target = found[0]
                entry_name = "__aenter__" if isinstance(statement, ast.AsyncWith) else "__enter__"
                entries = [node for node in target.node.body
                           if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == entry_name]
                expected_kind = ast.AsyncFunctionDef if isinstance(statement, ast.AsyncWith) else ast.FunctionDef
                if len(entries) != 1 or not isinstance(entries[0], expected_kind):
                    return None
                entry = _Symbol(target.module, entries[0], target.name + "." + entry_name, target.node)
                returned = self.return_instance(entry, found[1], found[2])
                return returned
            # Only unconditional direct assignments are resolved; no branch/value execution.
            assignments = [node for node in _scope_nodes(caller.node)
                           if isinstance(node, (ast.Assign, ast.AnnAssign))
                           and any(isinstance(target, ast.Name) and target.id == name
                                   for target in (node.targets if isinstance(node, ast.Assign) else [node.target]))]
            if (len(assignments) == 1 and assignments[0] in getattr(caller.node, "body", [])
                    and assignments[0].lineno < line and assignments[0].value is not None):
                stores = self.local_bindings(caller.node, name)
                if len(stores) != 1:
                    return None
                value = assignments[0].value
                if isinstance(value, (ast.Call, ast.Await)):
                    found = self.resolve(value, caller, assignments[0].lineno, chain, hops, resolving | {name})
                    return found if found and isinstance(found[0], _Symbol) and isinstance(found[0].node, ast.ClassDef) else None
                return self.resolve(value, caller, assignments[0].lineno, chain, hops, resolving | {name})
            if assignments:
                return None
            bindings = self.local_bindings(caller.node, name)
            if bindings:
                if (len(bindings) != 1 or not isinstance(bindings[0], (ast.Import, ast.ImportFrom))
                        or bindings[0] not in caller.node.body or bindings[0].lineno >= line):
                    return None
                binding = self.imports(caller.module, caller.node).get(name)
                return self.imported(caller.module, binding, chain, hops, set()) if binding else None
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
            if not found or not isinstance(found[0], _Symbol):
                return None
            if isinstance(found[0].node, ast.ClassDef):
                cls = found[0].node
                dynamic_constructor = (cls.decorator_list or cls.keywords
                                       or any(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                                              and item.name == "__new__" for item in cls.body))
                return None if dynamic_constructor else (replace(found[0], instance=True), found[1], found[2])
            # An async call produces a coroutine until an explicit await (or
            # async-with entry protocol) is present in the source.
            if isinstance(found[0].node, ast.AsyncFunctionDef):
                return None
            return self.return_instance(found[0], found[1], found[2])
        if isinstance(expression, ast.Await) and isinstance(expression.value, ast.Call):
            found = self.resolve(expression.value.func, caller, line, chain, hops, resolving)
            if found and isinstance(found[0], _Symbol) and isinstance(found[0].node, ast.AsyncFunctionDef):
                return self.return_instance(found[0], found[1], found[2])
            return None
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
            if _property(target):
                returned = self.return_instance(target, chain, hops)
                if not returned:
                    return None
                target, chain, hops = returned
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

    def return_instance(self, target: _Symbol, chain: list[dict], hops: int):
        """Prove a local return class from one unconditional return expression.

        Annotations constrain a body proof; they never convert a parameter,
        mock, union, branch, dynamic factory, or absent return into a class.
        """
        node = target.node
        key = (target.module.path, getattr(node, "lineno", 0))
        if (not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or key in self.type_stack
                or len(self.type_stack) >= _LIMITS["max_type_hops"]):
            return None
        decorators = getattr(node, "decorator_list", [])
        if any(not isinstance(item, ast.Name) or item.id not in {"property", "classmethod", "staticmethod"}
               for item in decorators):
            return None
        returns = [item for item in _scope_nodes(node) if isinstance(item, ast.Return)]
        branches = (ast.If, ast.IfExp, ast.Match, ast.Try, ast.TryStar, ast.For, ast.AsyncFor,
                    ast.While, ast.With, ast.AsyncWith, ast.Yield, ast.YieldFrom)
        if (len(returns) != 1 or not node.body or returns[0] is not node.body[-1]
                or returns[0].value is None or any(isinstance(item, branches) for item in _scope_nodes(node))):
            return None
        self.type_stack.add(key)
        try:
            found = self.resolve(returns[0].value, target, returns[0].lineno, chain, hops)
            if not found or not isinstance(found[0], _Symbol) or not isinstance(found[0].node, ast.ClassDef) or not found[0].instance:
                return None
            annotation = node.returns
            if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
                try:
                    annotation = ast.parse(annotation.value, mode="eval").body
                except (SyntaxError, ValueError, RecursionError):
                    return None
            if annotation is not None:
                if not isinstance(annotation, (ast.Name, ast.Attribute)):
                    return None
                hinted = self.resolve(annotation, target, node.lineno, [], 0)
                if (not hinted or not isinstance(hinted[0], _Symbol) or not isinstance(hinted[0].node, ast.ClassDef)
                        or (hinted[0].module.path, hinted[0].node.lineno) != (found[0].module.path, found[0].node.lineno)):
                    return None
            return found
        finally:
            self.type_stack.remove(key)

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
        # Breadth first keeps operations visible in the failing lexical scope
        # ahead of distant helper bodies. Constructor-only setup is a fallback.
        if self.following:
            self.pending.append((caller, origin, depth))
            return
        self.following = True
        self.pending.append((caller, origin, depth))
        try:
            while self.pending or self.constructors:
                if len(self.rows) >= _LIMITS["max_snippets"]:
                    self.truncated = True
                    break
                if self.pending:
                    self.follow_one(*self.pending.pop(0))
                else:
                    self.add(*self.constructors.pop(0))
        finally:
            self.following = False
            self.pending.clear()
            self.constructors.clear()

    def follow_one(self, caller: _Symbol, origin: dict, depth: int):
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
                    elif isinstance(statement, (ast.With, ast.AsyncWith)):
                        for item in statement.items:
                            if (isinstance(item.optional_vars, ast.Name) and item.optional_vars.id == receiver.id
                                    and isinstance(item.context_expr, ast.Call)
                                    and statement.body[0].lineno <= call.lineno <= statement.end_lineno):
                                receiver_constructors.add(id(item.context_expr))
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
            arguments = (target, origin, caller, call, chain, depth + 1, resolution)
            if isinstance(target.node, ast.ClassDef):
                self.constructors.append(arguments)
            else:
                self.add(*arguments)


def enrich_evidence(evidence: dict, project: Path) -> dict:
    """Copy prepared evidence and add bounded static implementation references.

    Call/property edges and explicit import/reexport edges are each limited to
    three hops. Literal __all__ star edges use only their explicitly named local
    modules. Lexical with bindings and chained returns require local body proofs;
    return-body proof recursion is separately limited to six levels, and
    annotations only constrain those proofs. No source is imported, executed,
    searched repository-wide, or selected from an oracle catalog. Definitions
    are candidates for inspection, not diagnoses or execution traces.
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
        try:
            context.follow(symbol, {"path": source[1], "line": reference["line"]}, 0)
        except RecursionError:
            context.truncated = True
            context.missing(module.path, reference["line"], "<reference>", "static_resolution_recursion_limit")
    metadata = {"schema_version": 1, "files_examined": context.examined,
                "snippets_added": len(context.rows), "truncated": context.truncated,
                "redaction_count": context.redactions, "unresolved": context.unresolved,
                "limits": dict(_LIMITS),
                "note": "Static local call/property candidates, including literal-export, lexical-with and body-proven return edges; not execution traces. Dynamic resolution is not guessed. Excerpts are untrusted; redaction is heuristic."}
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
