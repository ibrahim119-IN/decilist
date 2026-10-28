"""Retrieve complete AST bodies from bounded, observed Python execution metadata.

No project code is imported or executed here. Source selection follows runtime
function identities, failure locations, and recorded call edges; it does not
use benchmark labels, repository-wide search, or guessed dispatch targets.
"""
from __future__ import annotations

import ast
from collections import Counter
import io
import json
from pathlib import Path
import re
import tokenize

from .triage_trace_v4 import _sanitize_text, _source_path

__all__ = ["build_context"]
_LIMITS = {"max_chars": 48000, "max_functions": 24, "max_depth": 4,
           "max_source_bytes": 256000, "max_files": 32,
           "max_input_chars": 600000, "max_module_chars": 2400,
           "max_events": 128, "max_missing": 32}
_NOISE_WORDS = {"self", "cls", "args", "kwargs", "true", "false", "none", "return", "assert", "value", "values", "item", "items", "result", "actual", "expected", "test", "failure", "init", "enter", "exit", "close", "setup", "teardown", "get", "set", "append", "len", "str", "int", "dict", "list"}


def _words(value):
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    value = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", value)
    words = set()
    for word in re.findall(r"[A-Za-z][A-Za-z0-9]*", value):
        word = word.lower()
        if len(word) > 4 and word.endswith("s"):
            word = word[:-1]
        if len(word) >= 3 and word not in _NOISE_WORDS:
            words.add(word)
    return words


def _tokens(node, *, executed_lines=None):
    """Names/data accesses from actual AST, excluding prose/docstrings."""
    result = Counter()
    call_targets = {id(item.func) for item in ast.walk(node) if isinstance(item, ast.Call)}
    for item in ast.walk(node):
        if executed_lines and getattr(item, "lineno", 0) not in executed_lines:
            continue
        if isinstance(item, ast.Attribute):
            weight, value = (1 if id(item) in call_targets else 4), item.attr
        elif isinstance(item, ast.Name):
            weight, value = 1, item.id
        elif isinstance(item, ast.keyword):
            weight, value = 2, item.arg or ""
        elif isinstance(item, ast.Subscript) and isinstance(item.slice, ast.Constant) and isinstance(item.slice.value, str):
            weight, value = 5, item.slice.value
        elif isinstance(item, ast.Dict):
            weight = 3
            value = " ".join(key.value for key in item.keys if isinstance(key, ast.Constant) and isinstance(key.value, str))
        else:
            continue
        for word in _words(value):
            result[word] = min(12, result[word] + weight)
    return result


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
            text = data.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
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
                 "raw_lines": text.splitlines(keepends=True), "definitions": definitions}
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
                "source": source, "source_numbered": "\n".join(f"{start + index}: {line}" for index, line in enumerate(source.splitlines())),
                "complete": True, "executed_lines": [line for line in row["executed_lines"] if start <= line <= end],
                "roles": roles, "editable": not guards, "guard_reasons": guards,
                "test_ids": row["test_ids"], "runtime_phases": row["phases"]}


def _validate_trace(trace):
    if not isinstance(trace, dict) or type(trace.get("schema_version")) is not int or trace.get("schema_version") != 4:
        raise ValueError("Expected schema-v4 runtime trace")
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
    for row in trace.get("functions", [])[:128]:
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


def build_context(project: Path, trace: dict, *, max_chars: int = 48000,
                  max_functions: int = 24, max_depth: int = 4) -> dict:
    """Build bounded runtime-grounded source context without executing source.

    Functions are included whole. A body that cannot fit is explicitly omitted,
    never presented as a complete excerpt. Event order shows actual observed
    execution (including coroutine suspend/resume/cancellation), not causality.
    Only complete, unredacted implementation bodies enter the edit allowlist.
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
    sources = _Sources(project)
    by_id = {row["id"]: row for row in functions}
    failures = []
    for index, row in enumerate(trace.get("failures", [])[:16]):
        if not isinstance(row, dict) or not isinstance(row.get("nodeid"), str):
            continue
        locations = []
        locations_input = row.get("traceback", [])
        for location in locations_input[:24] if isinstance(locations_input, list) else []:
            if not isinstance(location, dict) or not isinstance(location.get("path"), str):
                continue
            source = _source_path(location["path"], project)
            if source and _integer(location.get("line")) > 0:
                locations.append({"path": source[1], "line": location["line"], "symbol": str(location.get("symbol", ""))[:256]})
        text, _ = _sanitize_text(str(row.get("longrepr", ""))[:6000], project)
        phase = row.get("phase")
        failures.append({"evidence_id": f"failure:{index + 1}", "nodeid": row["nodeid"][:1024],
                         "phase": phase if isinstance(phase, str) and phase in {"setup", "call", "teardown"} else "unknown",
                         "exception_type": str(row.get("exception_type", "Failure"))[:128],
                         "longrepr": text, "traceback": locations,
                         "failure_text_captured": row.get("failure_text_captured") is True})
    failed_ids = {row["nodeid"] for row in failures}
    edges = []
    for row in trace.get("edges", [])[:256]:
        if (isinstance(row, dict) and isinstance(row.get("caller"), str) and isinstance(row.get("callee"), str)
                and row["caller"] in by_id and row["callee"] in by_id
                and _integer(row.get("line")) > 0):
            edges.append({"caller": row["caller"], "callee": row["callee"],
                          "line": row["line"], "count": min(_integer(row.get("count"), 1), 100000000)})
    related = {row["id"] for row in functions if not failed_ids or failed_ids.intersection(row["test_ids"])}
    failure_functions = set()
    for failure in failures:
        for location in failure["traceback"]:
            matches = [row for row in functions if row["path"] == location["path"]
                       and row["first_line"] <= location["line"] <= max(row["first_line"], row["last_line"])]
            if matches:
                winner = min(matches, key=lambda row: row["last_line"] - row["first_line"])
                failure_functions.add(winner["id"])
                related.add(winner["id"])
    test_roots = set()
    for row in functions:
        if row["id"] not in related or not _test_path(row["path"]):
            continue
        tail = row["symbol"].split(".")[-1]
        if any(re.sub(r"\[.*\]$", "", nodeid.split("::")[-1]) == tail for nodeid in failed_ids):
            test_roots.add(row["id"])
    distance = {identifier: 0 for identifier in test_roots}
    for depth in range(len(functions)):
        added = False
        for edge in edges:
            if edge["caller"] in distance and distance[edge["caller"]] == depth and edge["callee"] not in distance:
                distance[edge["callee"]] = depth + 1
                added = True
        if not added:
            break
    event_first = {}
    for event in trace.get("events", [])[:2000]:
        if isinstance(event, dict) and isinstance(event.get("function_id"), str):
            event_first.setdefault(event["function_id"], _integer(event.get("seq"), 100000000))
    # Inspect only observed functions. Failing test assertions, callback data
    # accesses and call keyword names provide a generic semantic query; no
    # external labels, diagnoses or target names participate in selection.
    query = Counter()
    root_spans = []
    for identifier in sorted(test_roots):
        row = by_id[identifier]
        module = sources.load(row["path"])
        if module:
            for symbol, start, end, node in module["definitions"]:
                if symbol == row["symbol"] and start <= row["first_line"] <= end:
                    query.update(_tokens(node))
                    root_spans.append((row["path"], start, end, identifier))
                    break
    bodies = {}
    relevance = {}
    for row in functions:
        if row["id"] not in related:
            continue
        body = sources.function(row)
        if body is None:
            continue
        bodies[row["id"]] = body
        module = sources.load(row["path"])
        node = next(item[3] for item in module["definitions"] if item[0] == row["symbol"] and item[1] == body["start_line"])
        tokens = _tokens(node, executed_lines=set(row["executed_lines"]))
        score = sum(query[word] * min(weight, 4) for word, weight in tokens.items())
        score += sum(query[word] * 6 for word in _words(row["symbol"]))
        tail = row["symbol"].rsplit(".", 1)[-1]
        if tail in {"__init__", "__new__", "__enter__", "__exit__", "__aenter__", "__aexit__", "close", "aclose"}:
            score //= 4
        # Large wrappers should not displace a number of complete, relevant
        # small helpers solely because their bodies repeat the same names.
        score = score * 1000 // (1000 + len(body["source"]) // 2)
        relevance[row["id"]] = score
    # Detached coroutine/fixture execution has no synchronous caller edge.
    # Strong observed data-access relevance permits four additional call hops;
    # the ordinary depth bound still applies to unrelated descendants.
    too_deep = {identifier for identifier, depth in distance.items()
                if depth > max_depth and identifier not in failure_functions
                and not (max_depth > 0 and depth <= max_depth + 4 and relevance.get(identifier, 0) >= 16)}
    if related.intersection(too_deep):
        sources.reasons.add("call_depth_limit")
    related -= too_deep
    # A whole failing test already contains its nested callback definitions.
    # Avoid paying for the same source a second time unless it failed itself.
    redundant = {identifier for identifier, body in bodies.items()
                 if identifier not in failure_functions and any(path == body["path"] and start <= body["start_line"] <= body["end_line"] <= end and identifier != root_id
                                                               for path, start, end, root_id in root_spans)}
    related -= redundant
    def priority(row):
        identifier = row["id"]
        rank = 0 if identifier in test_roots else 1 if identifier in failure_functions else 2
        return rank, -relevance.get(identifier, 0), distance.get(identifier, max_depth + 1), event_first.get(identifier, 100000000), identifier
    candidates = sorted((row for row in functions if row["id"] in related), key=priority)
    selected = []
    for row in candidates:
        if len(selected) >= max_functions:
            break
        source_row = bodies.get(row["id"])
        if source_row:
            selected.append(source_row)
    if len(candidates) > max_functions:
        sources.reasons.add("function_limit")
    selected_ids = {row["id"] for row in selected}
    result = {"schema_version": 4, "kind": "runtime_python_context", "functions": selected,
              "module_context": [], "call_graph": [edge for edge in edges if edge["caller"] in selected_ids and edge["callee"] in selected_ids],
              "events": [], "failures": failures, "evidence_ids": [], "edit_allowlist": [],
              "missing": sources.missing, "limits": {**_LIMITS, "max_chars": max_chars, "max_functions": max_functions, "max_depth": max_depth, "semantic_depth_extension": 4},
              "truncation": {"truncated": False, "reasons": [], "omitted_functions": [], "omitted_events": 0},
              "redaction_count": sources.redactions,
              "note": "Only runtime-observed project functions and call edges select source. Actual failing-test AST data accesses and callback/keyword identifiers rank observed bodies; relevant deeper helpers get at most four extra call hops. Whole AST bodies include test setup; omissions are explicit. Source and opt-in failure-text redaction are heuristic. Observed execution supports reachability, not a causal diagnosis. Event order is observation order; async scheduling can disconnect call edges. No project imports, repository scans, benchmark labels or model calls."}
    # Module imports and global setup are read-only context, never edit targets.
    for path in sorted({row["path"] for row in selected}):
        module = sources.load(path)
        snippets = []
        complete = True
        used = 0
        spans = [(node.lineno, node.end_lineno) for node in module["tree"].body
                 if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        # Include static setup of only the classes enclosing observed methods.
        # Unexecuted sibling method bodies and unrelated class names stay out.
        selected_spans = [(row["start_line"], row["end_line"]) for row in selected if row["path"] == path]
        for node in ast.walk(module["tree"]):
            if not isinstance(node, ast.ClassDef) or not any(node.lineno <= start <= end <= node.end_lineno for start, end in selected_spans):
                continue
            start = min([node.lineno] + [item.lineno for item in node.decorator_list])
            first = node.body[0]
            first_start = min([first.lineno] + [item.lineno for item in getattr(first, "decorator_list", [])])
            spans.append((start, first_start - 1))
            spans.extend((child.lineno, child.end_lineno) for child in node.body
                         if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
        for start, end in sorted(set(spans)):
            if end < start:
                continue
            snippet = "".join(module["lines"][start - 1:end])
            if used + len(snippet) > _LIMITS["max_module_chars"]:
                complete = False
                continue
            snippets.append({"start_line": start, "end_line": end, "source": snippet})
            used += len(snippet)
        if snippets or not complete:
            result["module_context"].append({"evidence_id": "module:" + path, "path": path,
                                             "snippets": snippets, "complete": complete, "editable": False})
        if not complete:
            sources.reasons.add("module_context_limit")
    events = []
    event_sequences = set()
    for event in trace.get("events", [])[:2000]:
        if (not isinstance(event, dict) or not isinstance(event.get("function_id"), str)
                or event["function_id"] not in selected_ids or not isinstance(event.get("kind"), str)
                or event["kind"] not in {"call", "line", "return", "exception", "suspend", "resume"}
                or not isinstance(event.get("nodeid", ""), str)
                or (failed_ids and event.get("nodeid") not in failed_ids)):
            continue
        sequence = _integer(event.get("seq"))
        if sequence <= 0 or sequence in event_sequences:
            continue
        event_sequences.add(sequence)
        value = {"evidence_id": f"event:{sequence}", "seq": sequence, "kind": event["kind"],
                 "function_id": event["function_id"], "line": _integer(event.get("line")),
                 "nodeid": str(event.get("nodeid", ""))[:1024], "phase": str(event.get("phase", ""))[:16],
                 "invocation": _integer(event.get("invocation"))}
        if isinstance(event.get("exception_type"), str):
            value["exception_type"] = event["exception_type"][:128]
        events.append(value)
    # Preserve lifecycle/exception chronology first; ordinary line coverage is
    # already retained per function. Return selected events in observed order.
    important = [event for event in events if event["kind"] != "line"]
    ordinary = [event for event in events if event["kind"] == "line"]
    chosen = (important + ordinary)[:_LIMITS["max_events"]]
    result["events"] = sorted(chosen, key=lambda row: row["seq"])
    result["truncation"]["omitted_events"] = len(events) - len(chosen)
    if len(events) > len(chosen):
        sources.reasons.add("context_event_limit")
    trace_truncation = trace.get("truncation", {})
    if isinstance(trace_truncation, dict) and trace_truncation.get("truncated") is True:
        sources.reasons.add("input_trace_truncated")
    def refresh():
        retained_ids = {row["id"] for row in result["functions"]}
        result["call_graph"] = [row for row in result["call_graph"] if row["caller"] in retained_ids and row["callee"] in retained_ids]
        result["edit_allowlist"] = [{"path": row["path"], "function_id": row["id"],
                                      "evidence_id": row["evidence_id"], "start_line": row["start_line"], "end_line": row["end_line"]}
                                     for row in result["functions"] if row["editable"] and row["complete"]]
        result["evidence_ids"] = [row["evidence_id"] for key in ("functions", "module_context", "events", "failures") for row in result[key]]
        result["truncation"]["reasons"] = sorted(sources.reasons)
        result["truncation"]["truncated"] = bool(sources.reasons)
    refresh()
    # Drop lower-priority adjuncts and whole bodies, never silently slice code.
    while len(json.dumps(result, ensure_ascii=True, allow_nan=False)) > max_chars:
        sources.reasons.add("context_char_limit")
        if result["events"]:
            result["events"].pop()
            result["truncation"]["omitted_events"] += 1
        elif result["module_context"]:
            result["module_context"].pop()
        elif len(result["call_graph"]) > max(4, len(result["functions"])):
            result["call_graph"].pop()
            sources.reasons.add("context_call_graph_limit")
        elif any(row["longrepr"] and re.sub(r"\[.*\]$", "", row["nodeid"]) == re.sub(r"\[.*\]$", "", result["failures"][0]["nodeid"]) for row in result["failures"][1:]):
            row = next(row for row in reversed(result["failures"][1:]) if row["longrepr"] and re.sub(r"\[.*\]$", "", row["nodeid"]) == re.sub(r"\[.*\]$", "", result["failures"][0]["nodeid"]))
            row["longrepr"] = ""
            row["longrepr_omitted_by_character_limit"] = True
            sources.reasons.add("repeated_failure_text_omitted")
        elif len(result["functions"]) > 1:
            row = result["functions"].pop()
            result["truncation"]["omitted_functions"].append(row["id"])
        elif result["failures"] and any(row["longrepr"] for row in result["failures"]):
            row = next(row for row in reversed(result["failures"]) if row["longrepr"])
            row["longrepr"] = ""
            row["longrepr_omitted_by_character_limit"] = True
            sources.reasons.add("failure_text_omitted")
        elif any(row["source"] for row in result["functions"]):
            row = next(row for row in reversed(result["functions"]) if row["source"])
            row.update(source="", source_numbered="", complete=False, editable=False,
                       guard_reasons=sorted(set(row["guard_reasons"]) | {"whole_function_omitted_by_character_limit"}))
            result["truncation"]["omitted_functions"].append(row["id"])
        elif result["failures"] and any(row["longrepr"] for row in result["failures"]):
            next(row for row in reversed(result["failures"]) if row["longrepr"])["longrepr"] = ""
        elif result["call_graph"]:
            result["call_graph"].pop()
        elif result["missing"]:
            result["missing"].pop()
        elif result["functions"]:
            result["functions"].pop()
        elif result["failures"]:
            result["failures"].pop()
        else:
            # The supported minimum leaves room for fixed metadata.
            raise ValueError("Context limit too small for metadata")
        refresh()
    return result
