"""Optional deterministic inspection candidates, generated before either model ranks them.

Candidates describe observed source operations, never a root cause or repair.
No catalog, reference fix, model, project import, or subprocess is consulted.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import PurePosixPath, PureWindowsPath
import re
import textwrap
from typing import Any

from .triage_evidence import prepare_evidence
from .triage_inspect import inspect_evidence

__all__ = ["build_actions"]
_KINDS = ("contract_comparison", "branch_flow", "validation_boundary", "state_transition", "helper_call", "log_signal")
_SKIP_CALLS = {"print", "len", "str", "int", "float", "bool", "list", "dict", "set", "tuple", "range", "enumerate", "zip", "min", "max", "sum", "sorted", "repr", "super"}
_VALIDATION_CALLS = {"isinstance", "issubclass", "model_validate", "model_validate_json", "validate_python", "validate_json", "field_validator", "model_validator"}


def _safe(value: str, limit: int = 240) -> str:
    return prepare_evidence(value, project=None)["log"][:limit]


def _symbol(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 256:
        return None
    value = _safe(value, 256)
    return value if value and all(part.isidentifier() for part in value.split(".")) else None


def _path(value: Any) -> str | None:
    if not isinstance(value, str) or not value.isprintable() or len(value) > 1024:
        return None
    value = value.replace("\\", "/")
    pure = PurePosixPath(value)
    if not pure.parts or pure.is_absolute() or PureWindowsPath(value).drive or ".." in pure.parts or ":" in value or "[EXTERNAL]" in value:
        return None
    if _safe(value, 1024) != value:
        return None
    return value


def _lines(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return sorted({line for line in value if type(line) is int and 1 <= line <= 999_999_999})[:24]


def _sources(facts: dict) -> list[dict]:
    sources, seen = [], set()
    for row in facts.get("source_references", [])[:12]:
        if not isinstance(row, dict):
            continue
        path, line = _path(row.get("path")), row.get("line")
        if path is None or type(line) is not int or not 1 <= line <= 999_999_999 or not isinstance(row.get("excerpt"), str):
            continue
        key = (path, line)
        if key in seen:
            continue
        seen.add(key)
        contexts = row.get("implementation_context", [])
        contexts = contexts if isinstance(contexts, list) else []
        origins = row.get("origins", [])
        origins = origins if isinstance(origins, list) else []
        excerpt = _safe(row["excerpt"], 8000)
        if not any(re.match(rf"^\s*{line}\s*\|", text) for text in excerpt.splitlines()):
            continue
        source = {"path": path, "line": line, "excerpt": excerpt, "contexts": contexts, "origins": origins,
                  "log_lines": _lines(row.get("log_lines", []))}
        end = row.get("end_line")
        if type(end) is int and line <= end <= 999_999_999:
            source["end_line"] = end
        sources.append(source)
    originals = {(row["path"], row["line"]) for row in sources if "log_reference" in row["origins"] and row["log_lines"]}
    verified = []
    for source in sources:
        valid = []
        for context in source["contexts"]:
            if not isinstance(context, dict) or context.get("resolution") not in ("static_python_call", "static_python_property") or not _symbol(context.get("symbol")):
                continue
            reference, caller = context.get("reference"), context.get("caller")
            if not isinstance(reference, dict) or not isinstance(caller, dict):
                continue
            origin_path, origin_line = _path(reference.get("path")), reference.get("line")
            if origin_path is None or type(origin_line) is not int or (origin_path, origin_line) not in originals:
                continue
            if _path(caller.get("path")) is None or type(caller.get("line")) is not int or caller["line"] < 1:
                continue
            valid.append(context)
        implementation = "implementation" in source["origins"] and bool(valid)
        if not implementation and (source["path"], source["line"]) not in originals:
            continue
        symbols = sorted({_symbol(context["symbol"]) for context in valid})
        source.update(implementation=implementation, symbol=symbols[0] if symbols else None, contexts=valid,
                      resolution=valid[0]["resolution"] if valid else None)
        verified.append(source)
    return verified


def _ref(source: dict, *, line: int | None = None, end_line: int | None = None) -> dict:
    result = {"path": source["path"], "line": line if line is not None else source["line"]}
    end = end_line if end_line is not None else source.get("end_line")
    if end is not None and end >= result["line"]:
        result["end_line"] = end
    if source["symbol"]:
        result["symbol"] = source["symbol"]
    if source["resolution"]:
        result["resolution"] = source["resolution"]
    return result


def _action(kind: str, title: str, objective: str, refs: list[dict], lines: list[int], grounding: str, target: str = "", *, unit: tuple | None = None, specificity: int = 1) -> dict:
    unique = {json.dumps(ref, sort_keys=True, separators=(",", ":")): ref for ref in refs}
    refs = sorted(unique.values(), key=lambda ref: (ref["path"], ref["line"], ref.get("symbol", "")))
    lines = _lines(lines)
    identity = {"kind": kind, "source_refs": refs, "evidence_lines": lines, "target": target}
    suffix = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]
    return {"id": f"{kind}_{suffix}", "inspection_kind": kind, "title": _safe(title, 180), "objective": _safe(objective, 700),
            "grounding": grounding, "source_refs": refs, "evidence_lines": lines,
            "_unit": unit or (kind, target), "_specificity": specificity}


def _syntax(source: dict) -> tuple[ast.AST, list[int]] | None:
    code, coordinates = [], []
    for line in source["excerpt"].splitlines():
        match = re.fullmatch(r"\s*([1-9][0-9]{0,8})\s*\| ?(.*)", line)
        if not match:
            return None
        number = int(match[1])
        if coordinates and number != coordinates[-1] + 1:
            return None
        coordinates.append(number)
        code.append(match[2])
    if not code or source["line"] not in coordinates or (source.get("end_line") is not None and coordinates[-1] > source["end_line"]):
        return None
    try:
        tree = ast.parse(textwrap.dedent("\n".join(code)))
    except (SyntaxError, ValueError, RecursionError):
        return None
    return tree, coordinates


def _name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return _symbol(node.id)
    if isinstance(node, ast.Attribute):
        parent = _name(node.value)
        return _symbol(parent + "." + node.attr) if parent else None
    if isinstance(node, ast.Subscript):
        parent = _name(node.value)
        return parent + "[...]" if parent else None
    return None


def _call_name(node: ast.AST) -> str | None:
    direct = _name(node)
    if direct:
        return direct
    if isinstance(node, ast.Attribute) and node.attr.isidentifier():
        if isinstance(node.value, ast.Call):
            callee = _call_name(node.value.func)
            parent = callee + "(...)" if callee else None
        else:
            parent = _call_name(node.value)
        result = parent + "." + node.attr if parent else None
        return result if result and len(result) <= 256 else None
    return None


def _terms(node: ast.AST) -> set[str]:
    """Identifier overlap only; never outcome strings, comments, or case labels."""
    receivers = {id(part.value) for part in ast.walk(node) if isinstance(part, ast.Attribute) and isinstance(part.value, ast.Name)}
    terms = set()
    for part in ast.walk(node):
        value = part.id if isinstance(part, ast.Name) and id(part) not in receivers else part.attr if isinstance(part, ast.Attribute) else part.arg if isinstance(part, ast.keyword) else None
        if value and value not in _SKIP_CALLS | {"self", "cls", "True", "False", "None"}:
            terms.add(value.casefold())
    return terms


def _observed_assertions(source: dict) -> tuple[ast.AST, list[int], list[ast.Assert]] | None:
    parsed = _syntax(source)
    if parsed is None:
        return None
    tree, coordinates = parsed
    assertions = [node for node in ast.walk(tree) if isinstance(node, ast.Assert)
                  and coordinates[node.lineno - 1] <= source["line"] <= coordinates[(node.end_lineno or node.lineno) - 1]]
    return (tree, coordinates, assertions) if assertions else None


def _visible_calls(source: dict, parsed: tuple[ast.AST, list[int], list[ast.Assert]], implementations: list[dict], evidence_lines: list[int]) -> list[dict]:
    tree, coordinates, _ = parsed
    candidates = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or coordinates[node.lineno - 1] > source["line"]:
            continue
        name = _call_name(node.func)
        if name is None or name.rsplit(".", 1)[-1] in _SKIP_CALLS:
            continue
        line = coordinates[node.lineno - 1]
        end = coordinates[(node.end_lineno or node.lineno) - 1]
        leaf = name.rsplit(".", 1)[-1]
        # Avoid presenting an already resolved target as unresolved. Same-line nested
        # calls remain distinct by their actual callee name, not merely the line.
        if any(context.get("caller", {}).get("path") == source["path"] and context.get("caller", {}).get("line") == line
               and (context.get("symbol", "").rsplit(".", 1)[-1] == leaf or context.get("symbol") == name)
               for implementation in implementations for context in implementation["contexts"] if isinstance(context, dict)):
            continue
        candidates.append(_action("helper_call", f"Inspect visible {name} call before the assertion",
            f"Read the visible arguments and chained result at {source['path']}:{line} relative to the assertion at line {source['line']}. This establishes the call site only; use callee bodies only where matching static provenance is supplied, and do not infer a repair.",
            [_ref(source, line=line, end_line=end)], evidence_lines, "test_contract", "visible:" + name,
            unit=("visible", source["path"], name), specificity=0))
    return candidates


def _relevance(candidate: dict, sources: list[dict], terms: set[str]) -> int:
    observed = set()
    for reference in candidate["source_refs"]:
        for source in sources:
            if source["path"] != reference["path"]:
                continue
            if candidate["grounding"] == "implementation" and not source["implementation"]:
                continue
            parsed = _syntax(source)
            if parsed is None:
                continue
            tree, coordinates = parsed
            start, end = reference["line"], reference.get("end_line", reference["line"])
            for node in ast.walk(tree):
                offset = getattr(node, "lineno", 0)
                if 1 <= offset <= len(coordinates) and start <= coordinates[offset - 1] <= end:
                    observed |= _terms(node)
    return len(observed & terms)


def _operations(source: dict, evidence_lines: list[int], implementations: list[dict]) -> list[dict]:
    parsed = _syntax(source)
    if parsed is None:
        return []
    tree, coordinates = parsed
    label = source["symbol"] or f"{source['path']}:{source['line']}"
    unit = (source["path"], source["line"], source["symbol"])
    def action(*args, **kwargs):
        return _action(*args, unit=unit, specificity=2, **kwargs)
    parents = {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    calls = {}
    for call in ast.walk(tree):
        if isinstance(call, ast.Call) and (call_name := _call_name(call.func)):
            key = (coordinates[call.lineno - 1], call_name.rsplit(".", 1)[-1])
            calls[key] = calls.get(key, 0) + 1
    candidates = []
    for node in ast.walk(tree):
        offset = getattr(node, "lineno", 0)
        if not 1 <= offset <= len(coordinates):
            continue
        line = coordinates[offset - 1]
        if line < source["line"]:
            continue
        end_offset = getattr(node, "end_lineno", offset)
        end = coordinates[end_offset - 1] if 1 <= end_offset <= len(coordinates) else line
        reference = _ref(source, line=line, end_line=end)
        location = f"{source['path']}:{line}"
        if isinstance(node, (ast.If, ast.IfExp, ast.Match, ast.Try)):
            branch_label = "exception-handling path" if isinstance(node, ast.Try) else "conditional branch"
            test = getattr(node, "test", getattr(node, "subject", None))
            names = list(dict.fromkeys(observed_name for part in ast.walk(test) if (observed_name := _name(part))))[:3] if test is not None else []
            subject = " on " + ", ".join(names) if names else ""
            candidates.append(action("branch_flow", f"Inspect {branch_label}{subject} in {label}",
                f"Read the condition or handler and its visible outcomes at {location}; compare that flow with the recorded failure without assuming which path executed.",
                [reference], evidence_lines, "implementation", type(node).__name__))
        elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            name = _name(node.exc.func)
            if name and name.rsplit(".", 1)[-1] in {"ValidationError", "ValueError", "TypeError"}:
                candidates.append(action("validation_boundary", f"Inspect {name} boundary in {label}",
                    f"Read the raised exception and its surrounding input checks at {location}; compare the declared boundary with the recorded expectation before proposing changes.",
                    [reference], evidence_lines, "implementation", name))
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, (ast.Attribute, ast.Subscript)):
                    continue
                name = _name(target)
                if name:
                    candidates.append(action("state_transition", f"Inspect write to {name} in {label}",
                        f"Read the value written at {location} and where that value is subsequently returned or observed; do not assume the write caused the failure.",
                        [reference], evidence_lines, "implementation", name))
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name is None:
                continue
            leaf = name.rsplit(".", 1)[-1]
            if leaf in _SKIP_CALLS:
                continue
            if leaf in _VALIDATION_CALLS or leaf.startswith("validate_"):
                kind, title = "validation_boundary", f"Inspect {name} validation call in {label}"
                objective = f"Read the arguments and result handling of {name} at {location}; compare them with the observed validation or assertion evidence, without presuming the contract is wrong."
            elif leaf not in {"ValidationError", "ValueError", "TypeError"}:
                kind, title = "helper_call", f"Inspect {name} call in {label}"
                objective = f"Read the call-site arguments and how its result is used at {location}. This reference establishes the call site only; do not assume an unresolved callee implementation or runtime result."
            else:
                continue
            targets = []
            if calls.get((line, leaf)) == 1:
                for implementation in implementations:
                    for context in implementation["contexts"]:
                        caller = context.get("caller", {})
                        if (context["resolution"] == "static_python_call" and caller.get("path") == source["path"] and caller.get("line") == line
                                and context.get("symbol", "").rsplit(".", 1)[-1] == leaf):
                            if implementation not in targets:
                                targets.append(implementation)
            if len(targets) == 1:
                target = targets[0]
                target_label = target["symbol"] or f"{target['path']}:{target['line']}"
                candidates.append(_action(kind, f"Compare {name} call with {target_label}",
                    f"Compare the visible call-site arguments and result handling at {location} with the supplied callee at {target['path']}:{target['line']}. This exact static caller link is an inspection target, not an execution trace or a proven cause.",
                    [reference, _ref(target)], evidence_lines, "implementation", name,
                    unit=(target["path"], target["line"], target["symbol"]), specificity=3))
            else:
                used = isinstance(parents.get(id(node)), (ast.Return, ast.Assign, ast.AnnAssign, ast.Await))
                candidates.append(_action(kind, title, objective, [reference], evidence_lines, "implementation", name,
                    unit=unit, specificity=2 if used or kind == "validation_boundary" else 1))
    return candidates


def build_actions(inspected_or_evidence: dict) -> list[dict]:
    """Return at most five grounded candidates plus manual_review, identically for both model types.

    Inputs are prepared/enriched evidence or ``inspect_evidence`` output. The
    full returned list should be retained in experiments so candidate availability
    can be assessed separately from a model's selection. No model is called here.
    """
    if not isinstance(inspected_or_evidence, dict):
        raise ValueError("Expected prepared evidence or an inspection packet")
    if "facts" in inspected_or_evidence and (type(inspected_or_evidence.get("schema_version")) is not int or inspected_or_evidence["schema_version"] != 1):
        raise ValueError("Expected a schema-v1 inspection packet")
    inspected = inspected_or_evidence if "facts" in inspected_or_evidence else inspect_evidence(inspected_or_evidence)
    facts = inspected.get("facts")
    if not isinstance(facts, dict) or type(facts.get("failure_observed")) is not bool:
        raise ValueError("Expected inspection facts with an observed-failure boolean")
    for field in ("exceptions", "assertions", "expected_actual", "source_references"):
        if not isinstance(facts.get(field, []), list):
            raise ValueError("Inspection fact collections must be lists")
    manual = {"id":"manual_review", "inspection_kind":"manual_review", "title":"Review the evidence before selecting an inspection",
              "objective":"Use this option if the provided facts or referenced source do not distinguish a useful next inspection. Retain the failure and request missing context; do not infer a repair.",
              "grounding":"manual", "source_refs":[], "evidence_lines":[]}
    if not facts["failure_observed"]:
        return [manual]
    sources = _sources(facts)
    implementations = [source for source in sources if source["implementation"]]
    originals = [source for source in sources if not source["implementation"]]
    expectation_lines = sorted({line for row in facts.get("assertions", []) + facts.get("expected_actual", []) if isinstance(row, dict) for line in _lines(row.get("log_lines", []))})
    failure_lines = sorted({line for row in facts.get("exceptions", []) if isinstance(row, dict) for line in _lines(row.get("log_lines", []))})
    observed_contracts = [(source, parsed) for source in originals if (parsed := _observed_assertions(source)) is not None]
    assertion_error = any(isinstance(row, dict) and isinstance(row.get("type"), str) and row["type"].rsplit(".", 1)[-1] == "AssertionError" for row in facts.get("exceptions", []))
    if not expectation_lines and assertion_error and observed_contracts:
        # Pytest short tracebacks sometimes retain `assert ...` only in the
        # AssertionError message. The observed source coordinate corroborates an
        # assertion without guessing operand roles or expanding the evidence.
        expectation_lines = failure_lines
    contract_terms = set()
    for source, parsed in observed_contracts:
        for node in ast.walk(parsed[0]):
            if isinstance(node, ast.Assert):
                contract_terms |= _terms(node)
    evidence_lines = expectation_lines or failure_lines
    pool = []
    for source in implementations:
        label = source["symbol"] or f"{source['path']}:{source['line']}"
        if expectation_lines:
            refs = [_ref(source)] + [_ref(item) for item in originals[:1]]
            pool.append(_action("contract_comparison", f"Compare {label} with the recorded assertion",
                f"Read the referenced implementation at {source['path']}:{source['line']} alongside the recorded expectation and observed value; compare the contract without assuming an implementation or test defect.",
                refs, expectation_lines, "implementation", label,
                unit=(source["path"], source["line"], source["symbol"]), specificity=0))
        pool.extend(_operations(source, evidence_lines, implementations))
    if expectation_lines and not implementations and originals:
        source = originals[0]
        pool.append(_action("contract_comparison", f"Inspect assertion at {source['path']}:{source['line']}",
            "Compare the recorded expectation with the exact referenced assertion. Resolved implementation context was not supplied, so do not infer an implementation defect.",
            [_ref(source)], expectation_lines, "test_contract"))
    if expectation_lines:
        for source, parsed in observed_contracts:
            pool.extend(_visible_calls(source, parsed, implementations, expectation_lines))
    for row in facts.get("exceptions", [])[:3]:
        if not isinstance(row, dict) or not (name := _symbol(row.get("type"))):
            continue
        lines = _lines(row.get("log_lines", []))
        if lines:
            pool.append(_action("log_signal", f"Inspect recorded {name} at log line {lines[0]}",
                "Read this exact exception and its adjacent failure evidence. This candidate is grounded in prepared log lines only, not a resolved implementation or a proposed package installation.",
                [], lines, "log_only", name))
    if not pool and expectation_lines:
        pool.append(_action("log_signal", f"Inspect assertion evidence at log line {expectation_lines[0]}",
            "Read the recorded assertion and explicit values. No grounded implementation location is available; collect source context before selecting a repair.",
            [], expectation_lines, "log_only", "assertion"))
    unique = {candidate["id"]: candidate for candidate in pool}
    relevance = {candidate["id"]: _relevance(candidate, sources, contract_terms) for candidate in unique.values()}
    ordered = sorted(unique.values(), key=lambda row: (-relevance[row["id"]], -row["_specificity"], _KINDS.index(row["inspection_kind"]), row["source_refs"][0]["path"] if row["source_refs"] else "", row["source_refs"][0]["line"] if row["source_refs"] else 0, row["id"]))
    relevant = [candidate for candidate in ordered if relevance[candidate["id"]] > 0]
    # A comparison includes the assertion reference itself; it must not crowd
    # out all implementation operations merely by matching its own test text.
    focused = any(relevance[candidate["id"]] > 0 and candidate["inspection_kind"] != "contract_comparison" for candidate in ordered)
    eligible = relevant if focused else ordered
    grounded = [candidate for candidate in eligible if candidate["grounding"] != "log_only"]
    coverage = grounded or eligible
    selected, used_units = [], set()
    for candidate in coverage:
        if candidate["_unit"] not in used_units:
            selected.append(candidate)
            used_units.add(candidate["_unit"])
        if len(selected) == 5:
            break
    used_kinds = {candidate["inspection_kind"] for candidate in selected}
    for candidate in coverage:
        if len(selected) == 5:
            break
        if candidate not in selected and candidate["inspection_kind"] not in used_kinds:
            selected.append(candidate)
            used_kinds.add(candidate["inspection_kind"])
    for candidate in coverage:
        if len(selected) == 5:
            break
        if candidate not in selected:
            selected.append(candidate)
    for candidate in eligible:
        if len(selected) == 5:
            break
        if candidate not in selected:
            selected.append(candidate)
    return [{key:value for key,value in candidate.items() if not key.startswith("_")} for candidate in selected] + [manual]
