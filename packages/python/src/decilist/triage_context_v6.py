"""Retrieve complete AST bodies from bounded, observed Python execution metadata.

No project code is imported or executed here. Source selection follows runtime
function identities, failure locations, recorded calls and explicit state
candidates, with bounded read-only static helper references kept separate.
It does not use benchmark labels, repository-wide search or guessed dispatch.
"""
from __future__ import annotations

import ast
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import re
import tokenize

from .triage_trace_v5 import _sanitize_text, _source_path

__all__ = ["build_context"]
_LIMITS = {"max_chars": 24000, "max_functions": 32, "max_depth": 4,
           "max_source_bytes": 256000, "max_files": 32,
           "max_input_chars": 600000, "max_module_chars": 2400,
           "max_events": 128, "max_missing": 32}
_NOISE_WORDS = {"self", "cls", "args", "kwargs", "true", "false", "none", "return", "assert", "value", "values", "item", "items", "result", "actual", "expected", "test", "failure", "init", "enter", "exit", "close", "setup", "teardown", "get", "set", "append", "len", "str", "int", "dict", "list"}


def _words(value, *, field=False):
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    value = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", value)
    words = set()
    for word in re.findall(r"[A-Za-z][A-Za-z0-9]*", value):
        word = word.lower()
        if len(word) > 5 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us")):
            word = word[:-1]
        if len(word) >= 3 and (field or word not in _NOISE_WORDS):
            words.add(word)
    return words


def _tokens(node, *, executed_lines=None):
    """Names/data accesses from actual AST, excluding prose/docstrings."""
    result = Counter()
    nodes = []
    roots = node.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else [node]
    for root in roots:
        nodes.extend(ast.walk(root))
    call_targets = {id(item.func) for item in nodes if isinstance(item, ast.Call)}
    for item in nodes:
        if executed_lines and getattr(item, "lineno", 0) not in executed_lines:
            continue
        if isinstance(item, ast.Attribute):
            weight, value, field = (1 if id(item) in call_targets else 4), item.attr, True
        elif isinstance(item, ast.Name):
            weight, value, field = 1, item.id, False
        elif isinstance(item, ast.keyword):
            weight, value, field = 2, item.arg or "", True
        elif isinstance(item, ast.Subscript) and isinstance(item.slice, ast.Constant) and isinstance(item.slice.value, str):
            weight, value, field = 6, item.slice.value, True
        elif isinstance(item, ast.Dict):
            weight, field = 4, True
            value = " ".join(key.value for key in item.keys if isinstance(key, ast.Constant) and isinstance(key.value, str))
        elif isinstance(item, ast.Compare):
            weight, field = 7, True
            value = " ".join(part.value for part in [item.left, *item.comparators] if isinstance(part, ast.Constant) and isinstance(part.value, str))
        elif isinstance(item, ast.Call):
            fields = [part.value for part in item.keywords if part.arg == "match" and isinstance(part.value, ast.Constant) and isinstance(part.value.value, str)]
            if isinstance(item.func, ast.Attribute) and item.func.attr in {"get", "setdefault", "pop"}:
                fields += [part for part in item.args[:1] if isinstance(part, ast.Constant) and isinstance(part.value, str)]
            weight, field = 6, True
            value = " ".join(part.value for part in fields)
        else:
            continue
        for word in _words(value, field=field):
            result[word] = min(12, result[word] + weight)
    return result


def _value_calls(function):
    """Label source call sites that syntactically supply observable values.

    Labels are applied only to matching recorded edges by the caller. This
    bounded lexical analysis does not resolve names, receivers or dispatch.
    """
    nodes = []
    parents = {}

    def walk(node):
        nodes.append(node)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            parents[id(child)] = node
            walk(child)

    for statement in function.body:
        walk(statement)
    outputs = [node.value if isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom)) else node.test
               for node in nodes if isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom, ast.Assert))]
    output_names = {node.id for output in outputs if output is not None
                    for node in ast.walk(output) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
    result = []
    for call in nodes:
        if not isinstance(call, ast.Call):
            continue
        target = call.func.id if isinstance(call.func, ast.Name) else call.func.attr if isinstance(call.func, ast.Attribute) else None
        if target is None:
            continue
        current = call
        strength, kind = 0, ""
        for _ in range(12):
            parent = parents.get(id(current))
            if parent is None:
                break
            if isinstance(parent, (ast.Return, ast.Yield, ast.YieldFrom, ast.Assert)):
                strength = 3
                kind = "yield_from" if isinstance(parent, ast.YieldFrom) else "return_or_yield_or_assert"
                break
            if isinstance(parent, (ast.Assign, ast.AnnAssign)) and current is parent.value:
                targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
                assigned = {node.id for target_node in targets for node in ast.walk(target_node) if isinstance(node, ast.Name)}
                if assigned & output_names:
                    strength, kind = 3, "assigned_output_value"
                break
            if isinstance(parent, (ast.For, ast.AsyncFor)) and current is parent.iter:
                bound = {node.id for node in ast.walk(parent.target) if isinstance(node, ast.Name)}
                yielded = {node.id for statement in parent.body for output in ast.walk(statement)
                           if isinstance(output, (ast.Yield, ast.YieldFrom, ast.Return)) and output.value is not None
                           for node in ast.walk(output.value) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
                if bound & yielded:
                    strength, kind = 2, "iteration_supplies_yielded_value"
                break
            current = parent
        if strength:
            result.append((call.lineno, call.end_lineno, target, strength, kind))
    return result


def _state_accesses(function, executed_lines):
    """Direct receiver fields on executed lines, without object values."""
    arguments = [*function.args.posonlyargs, *function.args.args]
    if not arguments or any((isinstance(item, ast.Name) and item.id in {"staticmethod", "classmethod"})
                           or (isinstance(item, ast.Attribute) and item.attr in {"staticmethod", "classmethod"})
                           for item in function.decorator_list):
        return {"reads": set(), "writes": set(), "write_calls": []}
    receiver = arguments[0].arg
    reads, writes, calls = set(), set(), []

    def field(node):
        return node.attr if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == receiver else None

    def walk(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return
        name = field(node)
        if name and node.lineno in executed_lines:
            if isinstance(node.ctx, ast.Load):
                reads.add(name)
            elif isinstance(node.ctx, ast.Store):
                writes.add(name)
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.lineno in executed_lines:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            assigned = {name for target in targets for child in ast.walk(target)
                        if isinstance(child, ast.Attribute) and isinstance(child.ctx, ast.Store) and (name := field(child))}
            if assigned and node.value is not None:
                for call in ast.walk(node.value):
                    if isinstance(call, ast.Call):
                        target = call.func.id if isinstance(call.func, ast.Name) else call.func.attr if isinstance(call.func, ast.Attribute) else None
                        if target:
                            calls.append((call.lineno, call.end_lineno, target, frozenset(assigned)))
        if isinstance(node, ast.AugAssign) and node.lineno in executed_lines and (name := field(node.target)):
            reads.add(name)
            writes.add(name)
        for child in ast.iter_child_nodes(node):
            walk(child)

    for statement in function.body:
        walk(statement)
    return {"reads": reads, "writes": writes, "write_calls": calls}


def _executed_exception_scope(function, executed_lines):
    """An executed handler and raise in this function's own lexical scope."""
    handlers, raises = False, False

    def visit(node):
        nonlocal handlers, raises
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return
        if isinstance(node, ast.ExceptHandler) and node.lineno in executed_lines:
            handlers = True
        if isinstance(node, ast.Raise) and node.lineno in executed_lines:
            raises = True
        for child in ast.iter_child_nodes(node):
            visit(child)

    for statement in function.body:
        visit(statement)
    return handlers and raises


def _unique_module_binding(tree, name):
    """Fail closed on visible rebinding, wildcard imports or global mutation."""
    count = 0
    ambiguous = False

    def walk(node):
        nonlocal count, ambiguous
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            count += node.name == name
            return
        if isinstance(node, ast.ImportFrom):
            if any(alias.name == "*" for alias in node.names):
                ambiguous = True
            count += sum((alias.asname or alias.name) == name for alias in node.names)
        elif isinstance(node, ast.Import):
            count += sum((alias.asname or alias.name.split(".")[0]) == name for alias in node.names)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            count += node.id == name
        elif isinstance(node, ast.ExceptHandler):
            count += node.name == name
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)):
            count += node.name == name
        elif isinstance(node, ast.MatchMapping):
            count += node.rest == name
        for child in ast.iter_child_nodes(node):
            walk(child)

    for node in tree.body:
        walk(node)
    if any(isinstance(node, ast.Global) and name in node.names for node in ast.walk(tree)):
        ambiguous = True
    return count == 1 and not ambiguous


def _integer(value, default=0):
    return value if type(value) is int and 0 <= value <= 100000000 else default


def _test_path(path):
    location = Path(path)
    return (any(part.lower() in {"test", "tests", "testing"} for part in location.parts)
            or location.name == "conftest.py" or location.name.startswith("test_")
            or location.name.endswith("_test.py"))


def _definitions(tree):
    rows = []

    def walk(node, prefix=""):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbol = prefix + node.name
            start = min([node.lineno] + [item.lineno for item in node.decorator_list])
            rows.append((symbol, start, node.end_lineno, node))
            for child in node.body:
                walk(child, symbol + ".<locals>.")
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                walk(child, prefix + node.name + ".")
        else:
            for child in ast.iter_child_nodes(node):
                walk(child, prefix)

    walk(tree)
    return rows


def _fixture(node, tree):
    fixture_names = {"fixture"}
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom) and statement.module == "pytest":
            fixture_names.update(alias.asname or alias.name for alias in statement.names if alias.name == "fixture")
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name) and target.id in fixture_names:
            return True
        if isinstance(target, ast.Attribute) and target.attr == "fixture":
            return True
    return False


class _Sources:
    def __init__(self, project):
        self.project = project
        self.modules = {}
        self.missing = []
        self.reasons = set()
        self.redactions = 0

    def miss(self, path, reason, **extra):
        self.reasons.add("source_unavailable")
        row = {"path": path, "reason": reason, **extra}
        if row not in self.missing and len(self.missing) < _LIMITS["max_missing"]:
            self.missing.append(row)
        elif row not in self.missing:
            self.reasons.add("missing_entry_limit")

    def load(self, raw):
        if raw in self.modules:
            return self.modules[raw]
        source = _source_path(raw, self.project)
        if source is None:
            self.miss(raw[:256], "path_not_project_local_python")
            return None
        if len(self.modules) >= _LIMITS["max_files"]:
            self.reasons.add("file_limit")
            self.miss(source[1], "file_limit")
            return None
        self.modules[raw] = None
        try:
            with source[0].open("rb") as handle:
                data = handle.read(_LIMITS["max_source_bytes"] + 1)
            if len(data) > _LIMITS["max_source_bytes"]:
                self.reasons.add("source_byte_limit")
                self.miss(source[1], "source_byte_limit")
                return None
            encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
            original = data.decode(encoding)
            text = original.replace("\r\n", "\n").replace("\r", "\n")
            tree = ast.parse(text, filename=source[1])
            definitions = _definitions(tree)
            sanitized, redactions = _sanitize_text(text, self.project)
            if sanitized.count("\n") != text.count("\n"):
                self.miss(source[1], "sanitization_changed_line_layout")
                return None
        except (OSError, SyntaxError, UnicodeError, LookupError, ValueError, RecursionError):
            self.miss(source[1], "unreadable_or_malformed_python")
            return None
        self.redactions += redactions
        value = {"path": source[1], "tree": tree, "lines": sanitized.splitlines(keepends=True),
                 "raw_lines": original.splitlines(keepends=True), "definitions": definitions,
                 "file_sha256": hashlib.sha256(data).hexdigest()}
        self.modules[raw] = value
        return value

    def function(self, row):
        module = self.load(row["path"])
        if module is None:
            return None
        candidates = [item for item in module["definitions"]
                      if item[0] == row["symbol"] and item[1] <= row["first_line"] <= item[2]]
        if not candidates:
            self.miss(module["path"], "runtime_function_not_in_ast", function_id=row["id"])
            return None
        symbol, start, end, node = min(candidates, key=lambda item: item[2] - item[1])
        source = "".join(module["lines"][start - 1:end])
        original = "".join(module["raw_lines"][start - 1:end])
        if source == original.replace("\r\n", "\n").replace("\r", "\n"):
            source = original
        roles = ["test" if _test_path(module["path"]) else "implementation"]
        if _fixture(node, module["tree"]) or node.name in {"setUp", "tearDown", "setup", "teardown", "setup_method", "teardown_method", "setup_function", "teardown_function"}:
            roles.append("fixture")
        guards = []
        if "test" in roles or "fixture" in roles:
            guards.append("test_or_fixture_source")
        if source != original:
            guards.append("source_redacted")
        return {"id": row["id"], "evidence_id": "source:" + row["id"],
                "path": module["path"], "symbol": symbol, "start_line": start, "end_line": end,
                "source": source, "source_numbered": "",
                "complete": True, "executed_lines": [line for line in row["executed_lines"] if start <= line <= end],
                "roles": roles, "editable": not guards, "guard_reasons": guards,
                "test_ids": row["test_ids"], "runtime_phases": row["phases"]}


def _validate_trace(trace):
    if not isinstance(trace, dict) or type(trace.get("schema_version")) is not int or trace.get("schema_version") not in {4, 5}:
        raise ValueError("Expected bounded schema-v4 replay or schema-v5 runtime trace")
    try:
        serialized = json.dumps(trace, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError("Expected bounded JSON runtime trace") from error
    if len(serialized) > _LIMITS["max_input_chars"]:
        raise ValueError("Runtime trace exceeds input character limit")
    for field in ("functions", "edges", "events", "failures"):
        if not isinstance(trace.get(field, []), list):
            raise ValueError("Expected runtime trace record lists")
    functions = []
    seen = set()
    if len(trace.get("functions", [])) > 256 or len(trace.get("edges", [])) > 512 or len(trace.get("events", [])) > 2000:
        raise ValueError("Runtime trace record count exceeds V6 bounds")
    for row in trace.get("functions", [])[:256]:
        if not isinstance(row, dict):
            continue
        if not all(isinstance(row.get(key), str) and len(row[key]) <= 1600 for key in ("id", "path", "symbol")):
            continue
        if row["id"] in seen or not 1 <= _integer(row.get("first_line")):
            continue
        if not all(isinstance(row.get(field, []), list) for field in ("executed_lines", "test_ids", "phases")):
            continue
        seen.add(row["id"])
        functions.append({"id": row["id"], "path": row["path"], "symbol": row["symbol"],
                          "first_line": row["first_line"], "last_line": _integer(row.get("last_line")),
                          "executed_lines": sorted({_integer(item) for item in row.get("executed_lines", [])[:256] if _integer(item) > 0}),
                          "test_ids": sorted({item[:1024] for item in row.get("test_ids", [])[:128] if isinstance(item, str)}),
                          "phases": sorted({item for item in row.get("phases", [])[:3] if isinstance(item, str) and item in {"setup", "call", "teardown"}})})
    return functions


def _failure_projection(text):
    lines = text.splitlines()
    useful = [line for line in lines if line.lstrip().startswith(("E ", "E\t", "> "))
              or "At index " in line or "DID NOT RAISE" in line]
    projected = "\n".join(useful) if useful else text
    return projected[:1800], len(projected) > 1800


def _json_size(value):
    return len(json.dumps(value, ensure_ascii=True, allow_nan=False))


def build_context(project: Path, trace: dict, *, max_chars: int = 24000,
                  max_functions: int = 32, max_depth: int = 4) -> dict:
    """Pack whole observed AST bodies and a bounded connected runtime slice.

    Schema-v4 traces are accepted only as replay input. Schema-v6 output uses
    compact evidence IDs and one canonical unnumbered source copy. Candidate
    selection never imports project source or invents dynamic call targets.
    """
    for value, minimum, maximum in ((max_chars, 2000, 96000), (max_functions, 1, 64), (max_depth, 0, 8)):
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError("Invalid context limit")
    if not isinstance(project, Path) or str(project).startswith(("\\\\", "//")):
        raise ValueError("Project must be a local pathlib.Path directory")
    project = project.resolve(strict=True)
    if not project.is_dir():
        raise ValueError("Project must be a directory")
    functions = _validate_trace(trace)
    by_id = {row["id"]: row for row in functions}
    compact = {identifier: f"f{index + 1}" for index, identifier in enumerate(sorted(by_id))}
    sources = _Sources(project)
    reasons = sources.reasons
    failures = []
    locations = []
    for row in trace.get("failures", [])[:16]:
        if not isinstance(row, dict) or not isinstance(row.get("nodeid"), str):
            continue
        safe_locations = []
        raw_locations = row.get("traceback", [])
        for location in raw_locations[:24] if isinstance(raw_locations, list) else []:
            if not isinstance(location, dict) or not isinstance(location.get("path"), str):
                continue
            source = _source_path(location["path"], project)
            if source and _integer(location.get("line")) > 0:
                safe_locations.append({"path": source[1], "line": location["line"], "symbol": str(location.get("symbol", ""))[:256]})
        text, _ = _sanitize_text(str(row.get("longrepr", ""))[:6000], project)
        projection, clipped = _failure_projection(text)
        phase = row.get("phase")
        failures.append({"evidence_id": f"failure{len(failures) + 1}", "nodeid": row["nodeid"][:1024],
                         "phase": phase if isinstance(phase, str) and phase in {"setup", "call", "teardown"} else "unknown",
                         "exception_type": str(row.get("exception_type", "Failure"))[:128],
                         "longrepr": projection, "longrepr_projected": projection != text,
                         "longrepr_truncated": clipped, "traceback": safe_locations,
                         "failure_text_captured": row.get("failure_text_captured") is True})
        locations.extend(safe_locations)
    failed_tests = {row["nodeid"] for row in failures}
    related = {row["id"] for row in functions if not failed_tests or failed_tests.intersection(row["test_ids"])}
    edges = []
    for row in trace.get("edges", [])[:512]:
        if (isinstance(row, dict) and isinstance(row.get("caller"), str) and isinstance(row.get("callee"), str)
                and row["caller"] in by_id and row["callee"] in by_id and _integer(row.get("line")) > 0):
            edges.append({"caller": row["caller"], "callee": row["callee"], "line": row["line"],
                          "count": min(_integer(row.get("count"), 1), 100000000)})
    roots = set()
    for row in functions:
        if row["id"] in related and _test_path(row["path"]):
            tail = row["symbol"].rsplit(".", 1)[-1]
            if any(re.sub(r"\[.*\]$", "", nodeid.rsplit("::", 1)[-1]) == tail for nodeid in failed_tests):
                roots.add(row["id"])
    witnesses = set()
    origin_witnesses = set()
    for location in locations:
        matches = [row for row in functions if row["path"] == location["path"]
                   and row["first_line"] <= location["line"] <= max(row["first_line"], row["last_line"])]
        if matches:
            witnesses.add(min(matches, key=lambda row: row["last_line"] - row["first_line"])["id"])
    for failure in failures:
        if failure["traceback"]:
            location = failure["traceback"][-1]
            matches = [row for row in functions if row["path"] == location["path"]
                       and row["first_line"] <= location["line"] <= max(row["first_line"], row["last_line"])]
            if matches:
                origin_witnesses.add(min(matches, key=lambda row: row["last_line"] - row["first_line"])["id"])
    related |= witnesses
    distance = {identifier: 0 for identifier in roots}
    for depth in range(len(functions)):
        changed = False
        for edge in edges:
            if distance.get(edge["caller"]) == depth and edge["callee"] not in distance:
                distance[edge["callee"]] = depth + 1
                changed = True
        if not changed:
            break
    query = Counter()
    root_spans = []
    for identifier in sorted(roots):
        row = by_id[identifier]
        module = sources.load(row["path"])
        if not module:
            continue
        node = next((item[3] for item in module["definitions"] if item[0] == row["symbol"] and item[1] <= row["first_line"] <= item[2]), None)
        if node is None:
            continue
        query.update(_tokens(node))  # setup has deliberately lower weight
        root_spans.append((row["path"], node.lineno, node.end_lineno, identifier))
        failure_lines = {location["line"] for location in locations if location["path"] == row["path"]}
        anchors = [item for item in ast.walk(node) if isinstance(item, (ast.Assert, ast.With, ast.AsyncWith))
                   and any(item.lineno <= line <= item.end_lineno for line in failure_lines)]
        for anchor in anchors:
            for word, weight in _tokens(anchor).items():
                query[word] += weight * 5
        for callback in ast.walk(node):
            if isinstance(callback, (ast.FunctionDef, ast.AsyncFunctionDef)) and callback is not node:
                for word, weight in _tokens(callback).items():
                    query[word] += weight * 2
    query = Counter({word: min(weight, 48) for word, weight in query.items()})
    bodies = {}
    function_nodes = {}
    state_info = {}
    method_owners = {}
    relevance = {}
    body_cost = {}
    first_event = {}
    exception_events = set()
    for event in trace.get("events", [])[:2000]:
        if isinstance(event, dict) and isinstance(event.get("function_id"), str):
            first_event.setdefault(event["function_id"], _integer(event.get("seq"), 100000000))
            if (event.get("kind") == "exception" and isinstance(event.get("exception_type"), str)
                    and event["exception_type"] not in {"StopIteration", "StopAsyncIteration", "GeneratorExit"}
                    and isinstance(event.get("nodeid"), str) and (not failed_tests or event["nodeid"] in failed_tests)):
                exception_events.add(event["function_id"])
    for row in functions:
        identifier = row["id"]
        if identifier not in related:
            continue
        body = sources.function(row)
        if body is None:
            continue
        module = sources.load(row["path"])
        node = next(item[3] for item in module["definitions"] if item[0] == row["symbol"] and item[1] == body["start_line"])
        function_nodes[identifier] = node
        owner = next((owner for owner in ast.walk(module["tree"]) if isinstance(owner, ast.ClassDef) and node in owner.body), None)
        if owner is not None:
            method_owners[identifier] = (row["path"], row["symbol"].rsplit(".", 1)[0])
            state_info[identifier] = _state_accesses(node, set(row["executed_lines"]))
        tokens = _tokens(node, executed_lines=set(row["executed_lines"]))
        score = sum(query[word] * min(weight, 4) for word, weight in tokens.items())
        parts = row["symbol"].rsplit(".", 1)
        score += sum(query[word] * 4 for word in _words(parts[-1]))
        if len(parts) > 1:
            score += sum(query[word] for word in _words(parts[0]))
        if parts[-1] in {"__init__", "__new__", "__enter__", "__exit__", "__aenter__", "__aexit__", "close", "aclose"} and identifier not in witnesses:
            score //= 3
        body["id"] = compact[identifier]
        body["evidence_id"] = "s" + compact[identifier][1:]
        body["runtime_observed"] = True
        body["observed_call_depth"] = distance.get(identifier)
        body.pop("test_ids", None)  # canonical failing identifiers live once above
        body.pop("runtime_phases", None)
        bodies[identifier] = body
        relevance[identifier] = score
        body_cost[identifier] = _json_size(body) + (180 if body["editable"] else 0)
    # A root body already includes its lexical callbacks. Keep callback identity
    # only when it is a failure witness; this avoids duplicating the same code.
    redundant = {identifier for identifier, body in bodies.items() if identifier not in witnesses
                 and any(path == body["path"] and start <= body["start_line"] <= body["end_line"] <= end and identifier != root_id
                         for path, start, end, root_id in root_spans)}
    eligible = {identifier for identifier in bodies if identifier not in redundant
                and (identifier not in distance or distance[identifier] <= max_depth
                     or (max_depth > 0 and distance[identifier] <= max_depth + 4 and relevance[identifier] > 0)
                     or identifier in witnesses)}
    # A recorded same-class writer is not excluded solely because its name
    # has no lexical similarity to an assertion several call hops away.
    for reader in list(eligible):
        if reader not in state_info:
            continue
        for writer in state_info:
            if (writer in bodies and writer not in redundant and method_owners.get(reader) == method_owners.get(writer)
                    and state_info[reader]["reads"] & state_info[writer]["writes"]
                    and (writer not in distance or distance[writer] <= max_depth + 4)):
                eligible.add(writer)
    if len(eligible) < len(set(bodies) - redundant):
        reasons.add("call_depth_limit")
    adjacency = {identifier: set() for identifier in eligible}
    explicit_adjacency = {identifier: set() for identifier in eligible}
    producer_adjacency = {identifier: {} for identifier in eligible}
    value_sites = {identifier: _value_calls(function_nodes[identifier]) for identifier in eligible}
    producer_edges = {}
    constructor_output_edges = set()
    state_helpers = {identifier: {} for identifier in eligible}
    for edge in edges:
        if edge["caller"] in eligible and edge["callee"] in eligible:
            adjacency[edge["caller"]].add(edge["callee"])
            adjacency[edge["callee"]].add(edge["caller"])
            target = by_id[edge["callee"]]["symbol"].rsplit(".", 1)[-1]
            caller = function_nodes[edge["caller"]]
            if any(isinstance(node, ast.Call) and node.lineno <= edge["line"] <= node.end_lineno
                   and ((isinstance(node.func, ast.Name) and node.func.id == target)
                        or (isinstance(node.func, ast.Attribute) and node.func.attr == target))
                   for node in ast.walk(caller)):
                # This labels an already observed edge's source call site;
                # it does not infer dispatch from source syntax.
                explicit_adjacency[edge["caller"]].add(edge["callee"])
                explicit_adjacency[edge["callee"]].add(edge["caller"])
            constructor = target == "__init__" and edge["callee"] in method_owners
            protocol = target in {"__iter__", "__aiter__", "__next__", "__anext__"}
            for start, end, name, strength, kind in value_sites[edge["caller"]]:
                if (name == target or (constructor and name != "__init__") or protocol) and start <= edge["line"] <= end:
                    if constructor:
                        kind = "observed_constructor_at_output_site"
                        constructor_output_edges.add((edge["caller"], edge["callee"]))
                    elif protocol:
                        kind = "observed_protocol_at_output_site"
                    prior = producer_adjacency[edge["caller"]].get(edge["callee"])
                    if prior is None or strength > prior[0]:
                        producer_adjacency[edge["caller"]][edge["callee"]] = (strength, edge["count"], kind)
                    producer_edges[(edge["caller"], edge["callee"], edge["line"])] = kind
            for start, end, name, assigned in state_info.get(edge["caller"], {}).get("write_calls", []):
                if (name == target or (constructor and name != "__init__")) and start <= edge["line"] <= end:
                    state_helpers[edge["caller"]].setdefault(edge["callee"], set()).update(assigned)
    state_writers = {identifier: [] for identifier in eligible}
    for reader in state_info:
        if reader not in eligible:
            continue
        for writer in state_info:
            if writer not in eligible or writer == reader or method_owners[reader] != method_owners[writer]:
                continue
            if by_id[writer]["symbol"].rsplit(".", 1)[-1] in {"__exit__", "__aexit__", "close", "aclose"}:
                continue
            shared_tests = set(by_id[reader]["test_ids"]) & set(by_id[writer]["test_ids"])
            fields = state_info[reader]["reads"] & state_info[writer]["writes"]
            if fields and (shared_tests or not by_id[reader]["test_ids"] or not by_id[writer]["test_ids"]):
                state_writers[reader].append((writer, frozenset(fields)))
        state_writers[reader].sort(key=lambda item: (by_id[item[0]]["symbol"].rsplit(".", 1)[-1] != "__init__",
                                                    -len(item[1]), body_cost[item[0]], compact[item[0]]))
    result = {"schema_version": 6, "kind": "runtime_python_context", "functions": [],
              "module_context": [], "call_graph": [], "state_dependencies": [], "read_only_helpers": [], "events": [], "failures": failures,
              "evidence_ids": [], "edit_allowlist": [], "missing": sources.missing,
              "limits": {**_LIMITS, "max_chars": max_chars, "max_functions": max_functions,
                         "max_depth": max_depth, "semantic_depth_extension": 4, "trace_function_cap": 256,
                         "max_state_sources": 10, "max_state_call_hops": 2, "max_state_source_bytes": max_chars // 2,
                         "max_read_only_helpers": 2, "max_read_only_helper_bytes": 1600, "max_read_only_source_bytes": 2400,
                         "max_exception_anchors": 2, "max_exception_sources": 8, "max_exception_source_bytes": max_chars // 3},
              "truncation": {"truncated": False, "reasons": [], "omitted_function_count": 0,
                             "omitted_functions": [], "omitted_events": 0, "omitted_call_edges": 0},
              "coverage": {"input_schema_version": trace["schema_version"], "observed_functions": len(functions),
                           "retrieved_functions": len(bodies), "candidate_functions": len(eligible),
                           "complete_failing_tests": 0, "unobserved_source_fallback": False},
              "redaction_count": sources.redactions,
              "source_projection": "one exact unnumbered AST body; source_numbered intentionally empty; start_line supports rendering",
              "state_dependency_note": "Executed receiver-field reads/writes in the exact same lexical class are source candidates, not observed object identity. State links are separate from recorded runtime call edges.",
              "note": "functions contains runtime-observed bodies. read_only_helpers contains separately supplied unobserved static definitions; these do not establish calls or edit permission. Executed handler/raise paths reserve bounded recorded neighbors before setup packing. exception_context marks selection only, not proved exception propagation. selected_exception_sources counts newly admitted bodies. Failure/assertion fields and callback accesses rank candidates. Whole bodies use actual serialized size with bounded observed neighbors. No dynamic dispatch is guessed. Reachability is not causality. Source/failure-text redaction is heuristic. Missing trace metadata is incomplete coverage, not proof of non-execution. Static helper policy requires exactly one unobserved module-level definition and does not search other modules or all helpers."}
    selected = []
    def refresh():
        result["functions"] = [bodies[identifier] for identifier in selected]
        result["edit_allowlist"] = [{"path": row["path"], "function_id": row["id"], "evidence_id": row["evidence_id"],
                                      "start_line": row["start_line"], "end_line": row["end_line"]}
                                     for row in result["functions"] if row["editable"] and row["complete"]]
        result["evidence_ids"] = [row["evidence_id"] for key in ("functions", "module_context", "read_only_helpers", "events", "failures") for row in result[key]]
        result["truncation"]["reasons"] = sorted(reasons)
        result["truncation"]["truncated"] = bool(reasons)
        result["coverage"]["complete_failing_tests"] = sum(identifier in roots and bodies[identifier]["complete"] for identifier in selected)
        result["coverage"]["unobserved_source_fallback"] = bool(result["read_only_helpers"])
        result["coverage"]["represented_state_links"] = len(result["state_dependencies"])
    input_truncation = trace.get("truncation", {})
    if isinstance(input_truncation, dict) and input_truncation.get("truncated") is True:
        reasons.add("input_trace_incomplete")
    coverage = trace.get("coverage", {})
    if isinstance(coverage, dict):
        result["coverage"]["trace_function_evictions"] = _integer(coverage.get("function_evictions"))
        result["coverage"]["trace_function_rejections"] = _integer(coverage.get("function_rejections"))
        result["coverage"]["trace_output_functions_omitted"] = _integer(coverage.get("output_functions_omitted"))
    refresh()
    # Bound fixed failure evidence before source admission, so mandatory test
    # bodies are never displaced merely by duplicated assertion renderings.
    while _json_size(result) > max_chars and any(row["longrepr"] for row in result["failures"]):
        row = next(row for row in reversed(result["failures"]) if row["longrepr"])
        row["longrepr"] = ""
        row["longrepr_truncated"] = True
        reasons.add("failure_projection_limit")
        refresh()
    source_ceiling = max_chars - min(2400, max_chars // 10)
    def try_add(identifier, *, mandatory=False, neighbor_of=None, permit_full_budget=False):
        if identifier in selected:
            return True
        if identifier not in eligible or len(selected) >= max_functions:
            return False
        selected.append(identifier)
        if neighbor_of is not None:
            bodies[identifier]["selection_provenance"] = {"observed_neighbor_of": compact[neighbor_of]}
        refresh()
        if _json_size(result) <= (max_chars if mandatory or permit_full_budget else source_ceiling):
            return True
        selected.pop()
        bodies[identifier].pop("selection_provenance", None)
        refresh()
        if mandatory:
            while any(row["longrepr"] for row in result["failures"]):
                row = next(row for row in reversed(result["failures"]) if row["longrepr"])
                row["longrepr"] = ""
                row["longrepr_truncated"] = True
                reasons.add("failure_projection_limit")
                if try_add(identifier, permit_full_budget=True):
                    return True
            original = bodies[identifier]
            original.update(source="", source_numbered="", complete=False, editable=False,
                            guard_reasons=sorted(set(original["guard_reasons"]) | {"whole_function_omitted_by_character_limit"}))
            selected.append(identifier)
            reasons.add("mandatory_body_exceeds_budget")
            refresh()
            if _json_size(result) <= max_chars:
                return True
            selected.pop()
            refresh()
        return False
    # Root/origin bodies stay complete; outer traceback wrappers compete for
    # bytes with observed state producers instead of all becoming mandatory.
    for identifier in sorted(roots | origin_witnesses, key=lambda identifier: (identifier not in roots, identifier)):
        if identifier in bodies:
            try_add(identifier, mandatory=True)
    lifecycle = {"__init__", "__new__", "__enter__", "__exit__", "__aenter__", "__aexit__", "close", "aclose"}
    connected_admissions = 0
    producer_admissions, producer_bytes = 0, 0
    state_admissions, state_bytes = 0, 0
    retained_state_links = []
    value_readers = {identifier for identifier, node in function_nodes.items()
                     if any(isinstance(child, (ast.Return, ast.Yield, ast.YieldFrom)) and child.value is not None
                            for child in ast.walk(node))} | origin_witnesses
    def admit_state_bundle(reader):
        nonlocal state_admissions, state_bytes
        if reader not in value_readers:
            return
        queue = []
        for writer, fields in state_writers.get(reader, [])[:2]:
            if writer not in selected:
                if state_admissions >= 10 or body_cost[writer] > min(5000, max_chars // 3) or state_bytes + body_cost[writer] > max_chars // 2:
                    continue
                if not try_add(writer):
                    continue
                state_admissions += 1
                state_bytes += body_cost[writer]
            link = (reader, writer, tuple(sorted(fields)[:16]))
            if link not in retained_state_links:
                retained_state_links.append(link)
            queue.append((writer, 0, set(fields)))
        # A constructor does not open all of its initialization calls. Helpers
        # are followed only for fields consumed by an admitted same-class
        # method; this avoids pulling unrelated setup (URLs, caches, clients).
        visited = set()
        while queue and state_admissions < 10:
            parent, depth, fields = queue.pop(0)
            if parent in visited or depth >= 2:
                continue
            visited.add(parent)
            helpers = [(identifier, assigned) for identifier, assigned in state_helpers.get(parent, {}).items() if assigned & fields]
            helpers += [(identifier, set()) for identifier in producer_adjacency.get(parent, {})
                        if (parent, identifier) in constructor_output_edges]
            helpers.sort(key=lambda item: (body_cost[item[0]], compact[item[0]]))
            for identifier, assigned in helpers[:2]:
                if identifier not in selected:
                    if body_cost[identifier] > min(5000, max_chars // 3) or state_bytes + body_cost[identifier] > max_chars // 2:
                        continue
                    if not try_add(identifier, neighbor_of=parent):
                        continue
                    state_admissions += 1
                    state_bytes += body_cost[identifier]
                child_fields = set(state_info.get(identifier, {}).get("writes", set()))
                queue.append((identifier, depth + 1, child_fields))
    def admit_neighbors(seed):
        nonlocal connected_admissions, producer_admissions, producer_bytes
        queue = [(seed, 0)]
        while queue and (connected_admissions < 12 or producer_admissions < 8):
            parent, depth = queue.pop(0)
            if depth >= 2:
                continue
            admit_state_bundle(parent)
            # Return/yield producers form a source bundle before tiny terminal
            # helpers are considered. Longer complete bodies are allowed only
            # within this separate bounded, observed value-flow reservation.
            producers = [(identifier, details) for identifier, details in producer_adjacency.get(parent, {}).items()
                         if identifier not in selected and body_cost[identifier] <= min(8000, max_chars // 3)
                         and (by_id[identifier]["symbol"].rsplit(".", 1)[-1] not in lifecycle
                              or (parent, identifier) in constructor_output_edges)]
            producers.sort(key=lambda item: (-item[1][0], -item[1][1], body_cost[item[0]], compact[item[0]]))
            for identifier, details in producers[:3]:
                if producer_admissions >= 8 or producer_bytes + body_cost[identifier] > max_chars // 2:
                    break
                if try_add(identifier, neighbor_of=parent):
                    producer_admissions += 1
                    producer_bytes += body_cost[identifier]
                    admit_state_bundle(identifier)
                    queue.append((identifier, depth + 1))
            if connected_admissions >= 12:
                continue
            neighbors = [identifier for identifier in adjacency.get(parent, ()) if identifier not in selected
                         and body_cost[identifier] <= 1800 and relevance[identifier] > 0
                         and by_id[identifier]["symbol"].rsplit(".", 1)[-1] not in lifecycle]
            neighbors.sort(key=lambda identifier: (-relevance[identifier] * 10000 // max(300, body_cost[identifier]),
                                                    body_cost[identifier], compact[identifier]))
            chosen = neighbors[:1]
            remaining = [identifier for identifier in neighbors if identifier not in chosen]
            explicit = [identifier for identifier in remaining if identifier in explicit_adjacency.get(parent, ())]
            if explicit:
                chosen.append(min(explicit, key=lambda identifier: (body_cost[identifier], -relevance[identifier], compact[identifier])))
            elif remaining:
                chosen.append(remaining[0])
            for identifier in chosen:
                if try_add(identifier, neighbor_of=parent):
                    connected_admissions += 1
                    admit_state_bundle(identifier)
                    queue.append((identifier, depth + 1))
                    if connected_admissions >= 12:
                        break
    # Process failure-origin field writers before API/setup wrappers can spend
    # their reserved source budget. This preserves initialization of an error
    # object even when the visible failure is a later property access.
    for seed in sorted(origin_witnesses - roots):
        if seed in selected:
            admit_neighbors(seed)
    failure_state = set(origin_witnesses)
    for seed in origin_witnesses:
        failure_state.update(writer for writer, _ in state_writers.get(seed, []))
    exception_candidates = [identifier for identifier in eligible & exception_events
                            if _executed_exception_scope(function_nodes[identifier], set(by_id[identifier]["executed_lines"]))]
    related_handlers = {edge["caller"] for edge in edges if edge["callee"] in failure_state}
    exception_candidates.sort(key=lambda identifier: (identifier not in related_handlers, body_cost[identifier], compact[identifier]))
    exception_ids = failure_state & set(selected)
    exception_sources, exception_bytes, exception_anchors = 0, 0, 0
    def add_exception_source(identifier):
        nonlocal exception_sources, exception_bytes
        if identifier in selected:
            exception_ids.add(identifier)
            return True
        if (identifier not in eligible or exception_sources >= 8 or body_cost[identifier] > 3000
                or exception_bytes + body_cost[identifier] > max_chars // 3):
            return False
        if not try_add(identifier):
            return False
        exception_sources += 1
        exception_bytes += body_cost[identifier]
        exception_ids.add(identifier)
        return True
    covered_tests = set()
    for anchor in exception_candidates:
        tests = set(by_id[anchor]["test_ids"]) & failed_tests
        if exception_anchors >= 2:
            break
        if exception_anchors and tests <= covered_tests and anchor not in related_handlers:
            continue
        if not add_exception_source(anchor):
            continue
        exception_anchors += 1
        covered_tests |= tests
        # Incoming callers are actual recorded paths; diversify by failing-test
        # association so an early source branch cannot hide a later mode.
        incoming = {edge["caller"] for edge in edges if edge["callee"] == anchor and edge["caller"] in eligible}
        caller_coverage = set()
        for _ in range(2):
            if not incoming:
                break
            caller = min(incoming, key=lambda identifier: (-len((set(by_id[identifier]["test_ids"]) & failed_tests) - caller_coverage),
                                                           body_cost[identifier], compact[identifier]))
            incoming.remove(caller)
            if add_exception_source(caller):
                caller_coverage.update(set(by_id[caller]["test_ids"]) & failed_tests)
        outgoing = {edge["callee"] for edge in edges if edge["caller"] == anchor and edge["callee"] in eligible}
        for child in sorted(outgoing, key=lambda identifier: (body_cost[identifier], compact[identifier]))[:2]:
            if not add_exception_source(child):
                continue
            if by_id[child]["symbol"].rsplit(".", 1)[-1] == "__init__":
                grandchildren = {edge["callee"] for edge in edges if edge["caller"] == child and edge["callee"] in eligible}
                for grandchild in sorted(grandchildren, key=lambda identifier: (body_cost[identifier], compact[identifier]))[:2]:
                    add_exception_source(grandchild)
    result["coverage"]["selected_exception_anchors"] = exception_anchors
    result["coverage"]["selected_exception_sources"] = exception_sources
    # Reserve actual test-to-implementation call paths before generic small
    # accessors can monopolize the byte budget. The closest non-lifecycle call
    # at/before the failure is observed evidence even for callback side effects.
    pipeline_seeds = []
    for root in sorted(roots):
        failure_lines = [location["line"] for location in locations if location["path"] == by_id[root]["path"]]
        focus = max(failure_lines, default=100000000)
        outgoing = [edge for edge in edges if edge["caller"] == root and edge["callee"] in eligible
                    and edge["callee"] not in roots and edge["line"] <= focus
                    and by_id[edge["callee"]]["symbol"].rsplit(".", 1)[-1] not in lifecycle]
        outgoing.sort(key=lambda edge: (-edge["line"], body_cost[edge["callee"]], compact[edge["callee"]]))
        for edge in outgoing[:2]:
            if try_add(edge["callee"], neighbor_of=root):
                pipeline_seeds.append(edge["callee"])
    for seed in pipeline_seeds:
        admit_neighbors(seed)
    for seed in sorted((roots | origin_witnesses) - set(pipeline_seeds)):
        if seed in selected:
            admit_neighbors(seed)
    # Candidate pool is the entire bounded observed set, not a first-N prefix.
    # A failed admission does not stop smaller, useful bodies from fitting.
    pending = set(eligible) - set(selected)
    def utility(identifier):
        adjacent = any(neighbor in selected for neighbor in adjacency.get(identifier, ()))
        depth = distance.get(identifier, max_depth + 1)
        score = relevance[identifier] + (20 if adjacent else 0)
        return score * 10000 // max(300, body_cost[identifier]), -depth, -first_event.get(identifier, 100000000)
    while pending and len(selected) < max_functions:
        identifier = max(pending, key=lambda identifier: (*utility(identifier), compact[identifier]))
        pending.remove(identifier)
        if not try_add(identifier):
            continue
        # Two observed hops expose a small child of an admitted adapter; they
        # do not recursively traverse unrelated setup/lifecycle code.
        if relevance[identifier] > 0 or producer_adjacency.get(identifier) or state_writers.get(identifier):
            admit_neighbors(identifier)
            pending.difference_update(selected)
    omitted = [identifier for identifier in eligible if identifier not in selected]
    if omitted:
        reasons.add("whole_candidates_omitted_by_budget_or_count")
        result["truncation"]["omitted_function_count"] = len(omitted)
        result["truncation"]["omitted_functions"] = [compact[identifier] for identifier in sorted(omitted)[:16]]
    selected_set = set(selected)
    represented_state_links = 0
    for reader, writer, fields in retained_state_links[:16]:
        if reader not in selected_set or writer not in selected_set:
            continue
        link = {"reader": compact[reader], "writer": compact[writer], "fields": list(fields),
                "relation": "observed_functions_same_lexical_class_field_candidate", "runtime_object_identity_observed": False}
        result["state_dependencies"].append(link)
        if _json_size(result) <= max_chars:
            represented_state_links += 1
        else:
            result["state_dependencies"].pop()
            reasons.add("state_dependency_projection_limit")
    result["coverage"]["selected_state_sources"] = state_admissions
    result["coverage"]["represented_state_links"] = represented_state_links
    # Static references are separate from executed functions and may not grant
    # edit or call permission. A consumer must revalidate the canonical body
    # and module binding against the pre-patch snapshot before any exemption.
    helper_bytes = 0
    helper_modules = set()
    for identifier in sorted(selected, key=lambda item: (body_cost[item], compact[item])):
        anchor = bodies[identifier]
        if (by_id[identifier]["symbol"].rsplit(".", 1)[-1] != "__init__" or not anchor["editable"]
                or not anchor["complete"] or anchor["path"] in helper_modules):
            continue
        helper_modules.add(anchor["path"])
        module = sources.load(anchor["path"])
        observed_symbols = {row["symbol"] for row in functions if row["path"] == anchor["path"]}
        unobserved = [node for node in module["tree"].body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and node.name not in observed_symbols]
        if len(unobserved) != 1:
            continue
        node = unobserved[0]
        if (not isinstance(node, ast.FunctionDef) or node.decorator_list
                or not _unique_module_binding(module["tree"], node.name)):
            continue
        original = "".join(module["raw_lines"][node.lineno - 1:node.end_lineno])
        clean = "".join(module["lines"][node.lineno - 1:node.end_lineno])
        size = len(original.encode("utf-8"))
        if (size > 1600 or helper_bytes + size > 2400
                or clean != original.replace("\r\n", "\n").replace("\r", "\n")):
            continue
        helper = {"evidence_id": f"h{len(result['read_only_helpers']) + 1}", "path": anchor["path"],
                  "symbol": node.name, "start_line": node.lineno, "end_line": node.end_lineno,
                  "source": original, "source_sha256": hashlib.sha256(original.encode("utf-8")).hexdigest(),
                  "file_sha256": module["file_sha256"], "complete": True, "editable": False,
                  "roles": ["static_helper"], "runtime_observed": False,
                  "provenance": {"kind": "static_same_module_helper", "binding": "top_level_def",
                                 "observed_constructor_id": anchor["id"], "observed_constructor_evidence_id": anchor["evidence_id"],
                                 "trace_observation": "not_present_in_retained_trace"}}
        result["read_only_helpers"].append(helper)
        refresh()
        if _json_size(result) > max_chars:
            result["read_only_helpers"].pop()
            reasons.add("static_helper_projection_limit")
        else:
            helper_bytes += size
        if len(result["read_only_helpers"]) >= 2:
            break
    result["coverage"]["unobserved_source_fallback"] = bool(result["read_only_helpers"])
    value_obligations = [(caller, callee, line) for caller, callee, line in producer_edges if caller in selected_set]
    missing_producers = [key for key in value_obligations if key[1] not in selected_set]
    result["coverage"]["observed_value_producer_edges"] = len(value_obligations)
    result["coverage"]["selected_value_producer_edges"] = len(value_obligations) - len(missing_producers)
    if missing_producers:
        reasons.add("observed_value_producer_bundle_incomplete")
    # Add observed edges after bodies. Provenance edges are first and cannot be
    # displaced by unrelated event/module metadata.
    graph = [edge for edge in edges if edge["caller"] in selected_set and edge["callee"] in selected_set]
    graph.sort(key=lambda edge: (not (edge["caller"] in exception_ids and edge["callee"] in exception_ids),
                                 (edge["caller"], edge["callee"], edge["line"]) not in producer_edges,
                                 not any(bodies[edge[key]].get("selection_provenance", {}).get("observed_neighbor_of") == compact[edge[other]] for key, other in (("callee", "caller"), ("caller", "callee"))),
                                 -min(relevance[edge["caller"]], relevance[edge["callee"]]), edge["caller"], edge["callee"], edge["line"]))
    for edge in graph:
        projected = {"caller": compact[edge["caller"]], "callee": compact[edge["callee"]], "line": edge["line"], "count": edge["count"]}
        if edge["caller"] in exception_ids and edge["callee"] in exception_ids:
            projected["exception_context"] = True
        if (edge["caller"], edge["callee"], edge["line"]) in producer_edges:
            projected["value_flow"] = producer_edges[(edge["caller"], edge["callee"], edge["line"])]
        if edge["callee"] in state_helpers.get(edge["caller"], {}):
            projected["state_flow"] = "observed_call_at_receiver_field_assignment"
        result["call_graph"].append(projected)
        refresh()
        if _json_size(result) > max_chars:
            result["call_graph"].pop()
            result["truncation"]["omitted_call_edges"] += 1
    # Runtime lifecycle/exception chronology is optional adjunct evidence. Keep
    # only selected function identities and avoid repeating test IDs per event.
    candidates = []
    seen_sequences = set()
    for event in trace.get("events", [])[:2000]:
        if (not isinstance(event, dict) or not isinstance(event.get("function_id"), str)
                or event["function_id"] not in selected_set or not isinstance(event.get("kind"), str)
                or event["kind"] not in {"call", "return", "exception", "suspend", "resume"}):
            continue
        sequence = _integer(event.get("seq"))
        if sequence <= 0 or sequence in seen_sequences:
            continue
        seen_sequences.add(sequence)
        value = {"evidence_id": f"e{sequence}", "seq": sequence, "kind": event["kind"],
                 "function_id": compact[event["function_id"]], "line": _integer(event.get("line")),
                 "invocation": _integer(event.get("invocation"))}
        if isinstance(event.get("exception_type"), str):
            value["exception_type"] = event["exception_type"][:128]
        candidates.append(value)
    # Recent exceptions/lifecycle survive an early setup flood.
    candidates = candidates[-64:]
    for event in reversed(candidates):
        result["events"].append(event)
        refresh()
        if _json_size(result) > max_chars:
            result["events"].pop()
    result["events"].sort(key=lambda event: event["seq"])
    result["truncation"]["omitted_events"] = len(candidates) - len(result["events"])
    if result["truncation"]["omitted_events"]:
        reasons.add("context_event_projection_limit")
    if result["truncation"]["omitted_call_edges"]:
        reasons.add("context_call_graph_limit")
    # Small read-only import/class setup fragments are admitted only if source
    # and observed edges already fit. Never replace a body to add these.
    for path in sorted({bodies[identifier]["path"] for identifier in selected}):
        module = sources.load(path)
        snippets = []
        used = 0
        for node in module["tree"].body:
            if not isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign)):
                continue
            snippet = "".join(module["lines"][node.lineno - 1:node.end_lineno])
            if used + len(snippet) <= 900:
                snippets.append({"start_line": node.lineno, "end_line": node.end_lineno, "source": snippet})
                used += len(snippet)
        if not snippets:
            continue
        value = {"evidence_id": f"m{len(result['module_context']) + 1}", "path": path, "snippets": snippets, "complete": False, "editable": False}
        result["module_context"].append(value)
        refresh()
        if _json_size(result) > max_chars:
            result["module_context"].pop()
    refresh()
    # Omission accounting itself can consume a tight final margin. Remove
    # optional metadata, never already admitted complete source bodies.
    while _json_size(result) > max_chars:
        reasons.add("context_metadata_limit")
        if result["module_context"]:
            result["module_context"].pop()
        elif result["events"]:
            result["events"].pop(0)
            result["truncation"]["omitted_events"] += 1
        elif result["call_graph"]:
            result["call_graph"].pop()
            result["truncation"]["omitted_call_edges"] += 1
        elif result["truncation"]["omitted_functions"]:
            result["truncation"]["omitted_functions"].pop()
        elif result["read_only_helpers"]:
            result["read_only_helpers"].pop()
            reasons.add("static_helper_projection_limit")
        elif result["state_dependencies"]:
            result["state_dependencies"].pop()
            reasons.add("state_dependency_projection_limit")
        elif result["missing"]:
            result["missing"].pop()
        elif any(row["longrepr"] for row in result["failures"]):
            next(row for row in reversed(result["failures"]) if row["longrepr"])["longrepr"] = ""
        elif result["failures"]:
            result["failures"].pop()
        else:
            raise ValueError("Context limit too small for mandatory metadata")
        refresh()
    return result
