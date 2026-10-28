"""Deterministic, read-only facts and narrowly supported inspections, separate from model triage.

All strings remain untrusted evidence. A rule suggests an inspection, never a
root cause or a repair. This module does not run a model, process, or project scan.
"""
from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
import re
from typing import Any

from .triage_evidence import has_failure_signal, prepare_evidence

__all__ = ["inspect_evidence"]
_COMPACT_LIMIT = 12000
_ITEM_LIMIT = 12
_LINE_LIMIT = 16
_EXCEPTION = re.compile(
    r"^\s*(?P<pytest>E\s+)?(?P<type>(?:[A-Za-z_]\w*\.)*"
    r"(?:[A-Za-z_]\w*(?:Error|Exception)|Error|Exception|KeyboardInterrupt|SystemExit))"
    r"(?:\s*\[[A-Z_0-9]+\])?(?::\s*(?P<message>.*))?\s*$"
)
_ASSERTION = re.compile(r"^\s*(?P<marker>E\s+|>\s*)(?P<expression>assert\b.*)$")
_VALUE_LABEL = re.compile(r"^\s*(?:E\s+)?(?P<label>Expected|Actual|Received|Got)\s*:\s*(?P<value>\S.*)$", re.I)
_EXPECTED_GOT = re.compile(r"^expected\s+(?P<expected>.+?),?\s+(?:but\s+)?(?:got|actual(?:ly)?|observed)\s+(?P<actual>.+)$", re.I)
_MISSING_MODULE = re.compile(r"^No module named (?P<quote>['\"])(?P<module>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)(?P=quote)$")
_VALIDATION_HEADER = re.compile(r"^[1-9][0-9]{0,3} validation errors? for (?P<model>[A-Za-z_]\w*)$")
_VALIDATION_DETAIL = re.compile(r"\[type=(?P<code>[a-z][a-z_0-9]*)(?:,|\])")


def _short(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit - 1] + "…"


def _add(rows: list[dict], index: dict[tuple, dict], key: tuple, fact: dict, line: int) -> None:
    if key not in index:
        index[key] = {**fact, "log_lines": []}
        rows.append(index[key])
    if line not in index[key]["log_lines"]:
        index[key]["log_lines"].append(line)


def _test_id(line: str) -> str | None:
    match = re.match(r"^\s*FAILED\s+(.+)$", line)
    if not match:
        return None
    identifier = match[1].partition(" - ")[0].strip()
    return identifier if re.match(r"^.+\.py::.+$", identifier) else None


def _family(identifier: str) -> str:
    # Parameter values are variants of a test, not separate failure families.
    return identifier.split("[", 1)[0]


def _relative_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.replace("\\", "/")
    pure = PurePosixPath(value)
    if (pure.is_absolute() or PureWindowsPath(value).drive or ".." in pure.parts or ":" in value
            or "[EXTERNAL]" in value or len(value) > 1024 or not pure.parts or not value.isprintable()):
        return None
    return value


def _context_location(row: Any, *, symbol: bool = False) -> dict | None:
    if not isinstance(row, dict):
        return None
    path, number = _relative_path(row.get("path")), row.get("line")
    if path is None or type(number) is not int or not 1 <= number <= 999_999_999:
        return None
    result = {"path": path, "line": number}
    if symbol:
        if not isinstance(row.get("symbol"), str):
            return None
        result["symbol"] = _short(prepare_evidence(row["symbol"], project=None)["log"], 256)
    return result


def _source_references(evidence: dict, lines: list[str]) -> list[dict]:
    rows, seen = [], set()
    for reference in evidence.get("references", []):
        if not isinstance(reference, dict):
            continue
        path, number, excerpt = reference.get("path"), reference.get("line"), reference.get("excerpt")
        if not isinstance(path, str) or type(number) is not int or not 1 <= number <= 999_999_999 or not isinstance(excerpt, str):
            continue
        path = _relative_path(path)
        if path is None:
            continue
        location = re.compile(re.escape(path) + rf"(?:['\"], line {number}(?:\D|$)|:{number}(?::|\s|$))")
        observed = [index for index, line in enumerate(lines, 1) if location.search(line.replace("\\", "/"))]
        if not observed or (path, number) in seen:
            continue
        seen.add((path, number))
        # Reuse existing redaction without passing a project, so no source is opened.
        safe = prepare_evidence(excerpt, project=None)["log"]
        rows.append({"path": path, "line": number, "excerpt": _short(safe, 1200), "log_lines": observed, "origins": ["log_reference"]})
        if len(rows) == 3:
            break
    originals = {(row["path"], row["line"]) for row in rows}
    for reference in evidence.get("implementation_references", []):
        location = _context_location(reference)
        if location is None or not isinstance(reference.get("excerpt"), str) or reference.get("resolution") not in ("static_python_call", "static_python_property"):
            continue
        provenance = reference.get("provenance")
        if not isinstance(provenance, dict):
            continue
        origin = _context_location(provenance.get("reference"))
        caller = _context_location(provenance.get("caller"), symbol=True)
        if origin is None or caller is None or (origin["path"], origin["line"]) not in originals:
            continue
        chain = []
        imports = provenance.get("import_chain", [])
        if not isinstance(imports, list):
            continue
        for item in imports[:8]:
            entry = _context_location(item)
            if entry is None:
                continue
            for key in ("module", "symbol"):
                if isinstance(item.get(key), str):
                    entry[key] = _short(prepare_evidence(item[key], project=None)["log"], 256)
            chain.append(entry)
        symbol = reference.get("symbol")
        if not isinstance(symbol, str):
            continue
        context = {"symbol": _short(prepare_evidence(symbol, project=None)["log"], 256),
                   "resolution": reference["resolution"], "reference": origin, "caller": caller, "import_chain": chain}
        key = (location["path"], location["line"])
        existing = next((row for row in rows if (row["path"], row["line"]) == key), None)
        if existing is None:
            existing = {**location, "excerpt": prepare_evidence(reference["excerpt"], project=None)["log"], "log_lines": [], "origins": []}
            rows.append(existing)
        else:
            safe_excerpt = prepare_evidence(reference["excerpt"], project=None)["log"]
            if len(safe_excerpt) > len(existing["excerpt"]):
                existing["excerpt"] = safe_excerpt
        end = reference.get("end_line")
        if type(end) is int and location["line"] <= end <= 999_999_999:
            existing["end_line"] = end
        if reference.get("truncated") is True:
            existing["truncated"] = True
        if "implementation" not in existing["origins"]:
            existing["origins"].append("implementation")
        contexts = existing.setdefault("implementation_context", [])
        if context not in contexts and len(contexts) < 4:
            contexts.append(context)
        if len(rows) >= 8:
            break
    return rows


def _extract(evidence: dict) -> tuple[dict, dict]:
    safe = prepare_evidence(evidence["log"], project=None)
    lines = safe["log"].splitlines()
    exceptions, assertions, pairs, tests = [], [], [], []
    exception_index, assertion_index, pair_index, test_index = {}, {}, {}, {}
    full_exception_keys, summary_exception_keys = set(), set()
    traceback_seen = False
    for number, line in enumerate(lines, 1):
        if line.strip() == "Traceback (most recent call last):":
            traceback_seen = True
        identifier = _test_id(line)
        if identifier:
            _add(tests, test_index, (identifier,), {"id": identifier, "family": _family(identifier)}, number)
        summary = bool(identifier and " - " in line)
        candidate = line.partition(" - ")[2] if summary else line
        exception = _EXCEPTION.fullmatch(candidate)
        if exception:
            kind, message = exception["type"], (exception["message"] or "").strip()
            raised = bool(exception["pytest"] or traceback_seen or summary)
            _add(exceptions, exception_index, (kind, message), {"type": kind, "message": message, "raised_context": raised}, number)
            exception_index[(kind, message)]["raised_context"] |= raised
            (summary_exception_keys if summary else full_exception_keys).add((kind, message))
            if kind.rsplit(".", 1)[-1] == "AssertionError" and not (summary and message.endswith(("...", "…"))):
                values = _EXPECTED_GOT.fullmatch(message)
                if values:
                    _add(pairs, pair_index, (values["expected"], values["actual"]), {"expected": values["expected"], "actual": values["actual"], "basis": "explicit_assertion_message"}, number)
        assertion = _ASSERTION.fullmatch(line)
        if assertion:
            expression = assertion["expression"].strip()
            _add(assertions, assertion_index, (expression,), {"expression": expression, "representations": []}, number)
            representation = "evaluated_expression" if assertion["marker"].startswith("E") else "source_expression"
            if representation not in assertion_index[(expression,)]["representations"]:
                assertion_index[(expression,)]["representations"].append(representation)
        label = _VALUE_LABEL.fullmatch(line)
        if label and label["label"].lower() == "expected":
            # Only pair explicit neighboring labels. Never infer expected/actual from == operands.
            for later in range(number + 1, min(number + 4, len(lines)) + 1):
                actual = _VALUE_LABEL.fullmatch(lines[later - 1])
                if actual and actual["label"].lower() in {"actual", "received", "got"}:
                    key = (label["value"].strip(), actual["value"].strip())
                    _add(pairs, pair_index, key, {"expected": key[0], "actual": key[1], "basis": "explicit_value_labels"}, number)
                    _add(pairs, pair_index, key, {"expected": key[0], "actual": key[1], "basis": "explicit_value_labels"}, later)
                    break
                if actual or _EXCEPTION.fullmatch(lines[later - 1]) or _test_id(lines[later - 1]):
                    break
    for key in summary_exception_keys - full_exception_keys:
        kind, message = key
        candidates = [item for item in full_exception_keys if (
            item[0] == kind or (item[0].rsplit(".", 1)[-1] == kind.rsplit(".", 1)[-1] and ("." not in kind or "." not in item[0])))]
        if len(candidates) != 1:
            continue
        target = exception_index[candidates[0]]
        truncated_prefix = message[:-3] if message.endswith("...") else message[:-1] if message.endswith("…") else None
        if message and not (truncated_prefix is not None and target["message"].startswith(truncated_prefix)):
            continue
        summary_row = exception_index[key]
        target["log_lines"] = sorted(set(target["log_lines"] + summary_row["log_lines"]))
        target["raised_context"] |= summary_row["raised_context"]
        exceptions.remove(summary_row)
    facts = {"failure_observed": has_failure_signal(safe["log"]), "exceptions": exceptions,
             "assertions": assertions, "expected_actual": pairs, "failing_tests": tests,
             "source_references": _source_references(evidence, lines)}
    enrichment = evidence.get("context")
    enrichment_cut = isinstance(enrichment, dict) and enrichment.get("truncated") is True
    excerpt_cut = any(isinstance(row, dict) and row.get("truncated") is True for row in evidence.get("implementation_references", []))
    return facts, {"lines": lines, "truncated": bool(safe["truncated"] or evidence.get("truncated") or enrichment_cut or excerpt_cut)}


def _ambiguity(facts: dict) -> list[str]:
    reasons = []
    if len({row["type"].removeprefix("builtins.") for row in facts["exceptions"]}) > 1:
        reasons.append("different_exception_types")
    if len(facts["exceptions"]) > 1:
        reasons.append("different_exception_messages")
    if len({item["family"] for item in facts["failing_tests"]}) > 1:
        reasons.append("different_test_families")
    if len(facts["expected_actual"]) > 1:
        reasons.append("different_expected_actual_pairs")
    evaluated = [row for row in facts["assertions"] if "evaluated_expression" in row["representations"]]
    if len(evaluated) > 1:
        reasons.append("different_evaluated_assertions")
    if evaluated and any(row["type"].rsplit(".", 1)[-1] != "AssertionError" for row in facts["exceptions"]):
        reasons.append("assertion_and_different_exception")
    return reasons


def _rule(facts: dict, lines: list[str], ambiguity: list[str]) -> dict | None:
    if ambiguity or not facts["failure_observed"] or len(facts["exceptions"]) != 1:
        return None
    exception = facts["exceptions"][0]
    missing = _MISSING_MODULE.fullmatch(exception["message"])
    if exception["type"].rsplit(".", 1)[-1] == "ModuleNotFoundError" and missing:
        return {
            "rule_id": "python.missing-module.v1", "evidence_lines": list(exception["log_lines"]),
            "title": "Inspect the missing import and its declared dependency",
            "steps": ["Inspect the exact missing module and the referenced import location.",
                      "Compare that import with the project's declared dependencies and active environment; a module name need not equal a package installation name."],
        }
    if (exception["type"].startswith(("pydantic.", "pydantic_core."))
            and exception["type"].rsplit(".", 1)[-1] == "ValidationError"
            and exception["raised_context"] and _VALIDATION_HEADER.fullmatch(exception["message"])):
        details = []
        for number in exception["log_lines"]:
            for index in range(number + 1, min(number + 8, len(lines)) + 1):
                detail = lines[index - 1]
                if _EXCEPTION.fullmatch(detail) or _test_id(detail):
                    break
                content = re.sub(r"^\s*E\s+", "", detail).strip()
                if (_VALIDATION_DETAIL.search(content) and "input_" in content
                        and content.startswith(("Input ", "Value error,", "Field required", "Extra inputs ", "String ", "List ", "Dictionary ", "Tuple ", "Set ", "Frozen set ", "Object ", "JSON "))):
                    details.append(index)
        if details:
            return {
                "rule_id": "pydantic.raised-validation.v1", "evidence_lines": sorted(set(exception["log_lines"] + details)),
                "title": "Inspect the raised validation error and the declared field constraint",
                "steps": ["Inspect the typed validation detail and the exact input at the referenced boundary.",
                          "Compare that input with the declared field constraint before changing the data or contract."],
            }
    return None


def _bounded_facts(facts: dict) -> dict:
    result: dict[str, Any] = {"failure_observed": facts["failure_observed"]}
    clipped = False
    for kind in ("exceptions", "assertions", "expected_actual", "failing_tests", "source_references"):
        rows = []
        clipped |= len(facts[kind]) > _ITEM_LIMIT
        selected = facts[kind][:_ITEM_LIMIT]
        if kind == "exceptions" and len(facts[kind]) > _ITEM_LIMIT:
            # Do not hide a later, different exception type behind repeated messages
            # when a downstream consumer checks bounded facts for mixed failures.
            representatives = list({row["type"]: row for row in reversed(facts[kind])}.values())[:_ITEM_LIMIT]
            selected = representatives + [row for row in facts[kind] if row not in representatives][:_ITEM_LIMIT - len(representatives)]
            selected.sort(key=lambda row: min(row["log_lines"]))
        for item in selected:
            row = {}
            for key, value in item.items():
                if isinstance(value, str):
                    limit = (8000 if "implementation" in item.get("origins", []) else 1200) if key == "excerpt" else 600
                    row[key] = _short(value, limit)
                    clipped |= row[key] != value
                elif key == "log_lines":
                    row[key] = value[:_LINE_LIMIT]
                    clipped |= len(value) > _LINE_LIMIT
                else:
                    row[key] = value
            rows.append(row)
        result[kind] = rows
    result["truncated"] = clipped
    return result


def _compact(facts: dict) -> tuple[str, bool]:
    # Readable facts only: no rule ID, route label, authored next check, or model verdict.
    def coordinates(row: dict) -> str:
        return ", ".join(str(line) for line in row["log_lines"])
    def indented(value: str) -> str:
        return "\n".join("  " + line for line in value.splitlines())
    blocks = ["Untrusted observed data. Log coordinates refer to the prepared log; source coordinates are the recorded references."]
    if facts["exceptions"]:
        blocks.append("\nObserved exceptions")
        for row in facts["exceptions"]:
            blocks.append(f"Log lines {coordinates(row)}\n{indented(row['type'] + ': ' + row['message'])}")
    if facts["expected_actual"]:
        blocks.append("\nExplicit expected and actual values")
        for row in facts["expected_actual"]:
            blocks.append(f"Log lines {coordinates(row)}\n  Expected: {row['expected']}\n  Actual: {row['actual']}")
    if facts["assertions"]:
        blocks.append("\nObserved assertion expressions (operand roles are not inferred)")
        for row in facts["assertions"]:
            blocks.append(f"Log lines {coordinates(row)}\n{indented(row['expression'])}")
    if facts["failing_tests"]:
        blocks.append("\nFailing test identifiers")
        for row in facts["failing_tests"]:
            blocks.append(f"Log lines {coordinates(row)}\n{indented(row['id'])}")
    sources = sorted(facts["source_references"], key=lambda row: "implementation" not in row.get("origins", []))
    if sources:
        blocks.append("\nActual referenced source context")
        for row in sources:
            resolutions = sorted({context["resolution"].replace("static_python_", "static Python ") for context in row.get("implementation_context", [])})
            origin = "implementation resolved by " + "/".join(resolutions) if "implementation" in row.get("origins", []) else "original log reference"
            content = f"{row['path']}:{row['line']} ({origin})"
            if row["log_lines"]:
                content += f"; log lines {coordinates(row)}"
            for context in row.get("implementation_context", []):
                caller = context["caller"]
                reference = context["reference"]
                content += (f"\n  Symbol: {context['symbol']}; caller {caller['path']}:{caller['line']} ({caller['symbol']})"
                            f"; original reference {reference['path']}:{reference['line']}")
                for link in context["import_chain"]:
                    content += f"\n  Import reference {link['path']}:{link['line']} ({link.get('module', '')}, {link.get('symbol', '')})"
            blocks.append(content + "\n" + indented(row["excerpt"]))
    if not any(facts[key] for key in ("exceptions", "assertions", "expected_actual", "failing_tests", "source_references")):
        blocks.append("No supported concrete failure facts were extracted.")
    marker = "\n[Evidence truncated; inspect the full facts packet for omitted details.]"
    limit = _COMPACT_LIMIT - len(marker)
    state, clipped = "", bool(facts["truncated"])
    for block in blocks:
        addition = ("\n" if state else "") + block
        if len(state) + len(addition) <= limit:
            state += addition
        else:
            remaining = limit - len(state)
            state += addition[:max(0, remaining)]
            clipped = True
            break
    if clipped:
        state += marker
    return state, clipped


def inspect_evidence(evidence: dict) -> dict:
    """Extract corroborated facts and authored inspections without executing or modifying anything.

    ``compact_state`` is readable facts-only text, at most 12,000 characters. Its line
    numbers refer to the prepared log, never guessed original-file locations.
    Rule coverage is deliberately narrow; no rule is a diagnosis or a repair.
    """
    if (not isinstance(evidence, dict) or type(evidence.get("schema_version")) is not int
            or evidence["schema_version"] != 1 or not isinstance(evidence.get("log"), str)
            or not isinstance(evidence.get("references", []), list)
            or not isinstance(evidence.get("implementation_references", []), list)):
        raise ValueError("Expected schema-v1 prepared evidence with a log string and reference list")
    facts, context = _extract(evidence)
    ambiguity = _ambiguity(facts)
    rule = _rule(facts, context["lines"], ambiguity)
    inspection = rule
    if inspection is None and not ambiguity and facts["failure_observed"] and (facts["expected_actual"] or facts["assertions"]):
        inspection = {
            "basis": "observed_assertion", "evidence_lines": sorted({line for item in facts["expected_actual"] + facts["assertions"] for line in item["log_lines"]}),
            "title": "Inspect the recorded expectation against the observed value",
            "steps": ["At the cited assertion, compare the stated expectation and observed expression with the intended contract and configured policy.",
                      "Inspect the referenced source context before selecting an explanation or changing the assertion."],
        }
    bounded = _bounded_facts(facts)
    bounded["truncated"] |= context["truncated"]
    compact, compact_truncated = _compact(bounded)
    return {"schema_version": 1, "facts": bounded, "compact_state": compact,
            "compact_truncated": compact_truncated, "truncated": bool(bounded["truncated"] or compact_truncated),
            "rule_suggestion": rule, "next_inspection": inspection, "ambiguity": ambiguity,
            "limitations": ["Facts remain untrusted log data; a suggested inspection is not a proven cause.",
                            "Redaction is heuristic. Unsupported or mixed evidence receives no rule suggestion."]}
