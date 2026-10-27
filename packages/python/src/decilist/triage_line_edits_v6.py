"""Draft whole-line protocol. Canonical source stays private and exact."""
from __future__ import annotations

import copy
import io
import re
import token as token_types
import tokenize
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, model_validator
from decilist import triage_hypothesis_v6 as legacy
from decilist import triage_module_literals_v8 as literals

VERSION = "evidence_absolute_lines_verbatim.4"
MAX_CONTEXT_CHARS = 24000
MAX_EDIT_LINES = 16


class LineEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_evidence_id: str = Field(min_length=1, max_length=2048)
    start_line: int = Field(ge=1, le=100000000)
    line_count: int = Field(ge=1, le=MAX_EDIT_LINES)
    replacement: str = Field(max_length=1200)


class LiteralEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_evidence_id: str = Field(min_length=1, max_length=2048)
    replacement_literal: str = Field(min_length=1, max_length=1200)


class LineHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    hypothesis: str | None = Field(max_length=500)
    failure_evidence_id: str = Field(min_length=1, max_length=2048)
    evidence_ids: list[str] = Field(max_length=5)
    expected_test_behavior: str | None = Field(max_length=500)
    edits: list[LineEdit | LiteralEdit] = Field(max_length=2)

    @model_validator(mode="after")
    def consistent(self):
        # Reuse the public V6 consistency contract, independently of edits.
        references = [] if self.hypothesis is None else list(dict.fromkeys(
            [self.failure_evidence_id, *self.evidence_ids, *(edit.source_evidence_id for edit in self.edits)]))
        legacy.Hypothesis.model_validate({"hypothesis": self.hypothesis, "evidence_ids": references,
            "expected_test_behavior": self.expected_test_behavior, "replacements": []})
        if self.hypothesis is None and (self.edits or self.evidence_ids):
            raise ValueError("An unsupported hypothesis keeps only its selected failure observation")
        return self


def physical_lines(text: str) -> list[str]:
    """Python physical lines, retaining EOL; Unicode line-separator data stays data."""
    return re.findall(r"[^\r\n]*(?:\r\n|\n|\r|$)", text)[:-1]


def _eol(line):
    return "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else "\r" if line.endswith("\r") else ""


def _string_tokens(text):
    try:
        result, fstrings = [], []
        fstart = getattr(token_types, "FSTRING_START", None)
        fend = getattr(token_types, "FSTRING_END", None)
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.STRING:
                result.append(token)
            elif fstart is not None and token.type == fstart:
                fstrings.append(token.start)
            elif fend is not None and token.type == fend:
                if not fstrings:
                    raise ValueError("Unbalanced f-string token boundary")
                result.append(tokenize.TokenInfo(tokenize.STRING, "", fstrings.pop(), token.end, ""))
        if fstrings:
            raise ValueError("Unbalanced f-string token boundary")
        return result
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        raise ValueError("Replacement must be a lexically complete relative fragment") from exc


def _verbatim_replacement(text: str, ending: str, old_terminated: bool) -> str:
    if "\r" in text:
        raise ValueError("Model replacement uses LF only; canonical CRLF is reconstructed")
    if not text:
        return ""
    # Code and literal whitespace are verbatim. Only physical EOL framing changes.
    rendered = text.replace("\n", ending)
    if old_terminated and not text.endswith("\n"):
        rendered += ending
    return rendered


@dataclass
class Projection:
    canonical: dict
    model: dict
    sources: dict[str, dict]
    literal_sources: dict[str, dict] = field(default_factory=dict)


def project_context(context: dict, *, max_chars=MAX_CONTEXT_CHARS) -> Projection:
    if type(max_chars) is not int or not 4000 <= max_chars <= MAX_CONTEXT_CHARS:
        raise ValueError("Line projection budget must be between 4,000 and 24,000 characters")
    canonical = literals.checked_context(context)
    eligible_rows = legacy._editable_sources(canonical)
    eligible = {row["evidence_id"]: row for row in eligible_rows}
    if len(eligible) != len(eligible_rows):
        raise ValueError("Editable source evidence IDs must be unique")
    module_literals = literals.checked_literal_sources(canonical)
    model = copy.deepcopy(canonical)
    if literals.CONTEXT_KEY in model:
        private = {"source", "source_sha256", "file_sha256", "literal_start_offset", "literal_end_offset"}
        model[literals.CONTEXT_KEY] = [{key: value for key, value in row.items() if key not in private}
                                      for row in model[literals.CONTEXT_KEY]]
    for row in model.get("functions", []):
        if row.get("evidence_id") not in eligible:
            continue
        source = eligible[row["evidence_id"]]
        lines = physical_lines(source["source"])
        if len(lines) != source["end_line"] - source["start_line"] + 1:
            raise ValueError("Canonical source does not match its absolute line span")
        if any(_eol(line) == "\r" for line in lines):
            raise ValueError("Bare-CR source is outside the line-edit prototype")
        row.pop("source", None)
        row.pop("source_numbered", None)
        row["editable_lines"] = "\n".join(f"{source['start_line'] + index} | {line.removesuffix(_eol(line)) if _eol(line) else line}" for index, line in enumerate(lines))
        row["edit_indent_contract"] = "replacement uses exact displayed indentation; no prefixing or dedent"
    model["line_edit_protocol"] = {"version": VERSION, "range": "absolute start_line plus physical line_count", "max_lines": MAX_EDIT_LINES,
        "indentation": "Replacement uses exact displayed tabs/spaces on every line. No whitespace is added, removed or inferred.",
        "line_endings": "Model text uses LF; original uniform LF/CRLF and required final line terminator are reconstructed."}
    model["source_projection"] = "Editable bodies are numbered; exact canonical originals stay private. Read-only bodies are unchanged."
    size = lambda: len(legacy._json_data({"runtime_context": model}))
    omitted = []
    def synchronize():
        remaining = {row["id"] for row in model.get("functions", [])}
        if "edit_allowlist" in model:
            model["edit_allowlist"] = [row for row in model["edit_allowlist"] if row.get("function_id") in remaining]
        if "call_graph" in model:
            model["call_graph"] = [row for row in model["call_graph"] if row.get("caller") in remaining and row.get("callee") in remaining]
        if "state_dependencies" in model:
            model["state_dependencies"] = [row for row in model["state_dependencies"] if row.get("reader") in remaining and row.get("writer") in remaining]
        if "read_only_helpers" in model:
            model["read_only_helpers"] = [row for row in model["read_only_helpers"] if row.get("provenance", {}).get("observed_constructor_id") in remaining]
        parents = {row.get("evidence_id") for row in model.get("module_context", [])}
        if literals.CONTEXT_KEY in model:
            model[literals.CONTEXT_KEY] = [row for row in model[literals.CONTEXT_KEY]
                if row.get("provenance", {}).get("module_evidence_id") in parents]
        supplied = {row.get("evidence_id") for key in ("functions", "failures", "module_context", "events", "read_only_helpers", literals.CONTEXT_KEY)
                    for row in model.get(key, []) if isinstance(row, dict)}
        model["evidence_ids"] = [key for key in canonical["evidence_ids"] if key in supplied]

    synchronize()
    for key in ("events", "limits", "coverage", "missing", "truncation"):
        if size() <= max_chars:
            break
        if key in model:
            model.pop(key)
            omitted.append(key)
            synchronize()
    # Optional relations/prose cannot force a valid source projection past its
    # fixed cap. Preserve literal parents as a unit while alternatives remain.
    for key in ("note", "state_dependency_note", "source_projection", "state_dependencies", "call_graph"):
        if size() <= max_chars:
            break
        if key in model:
            model.pop(key)
            omitted.append(key)
            synchronize()
    while size() > max_chars and model.get("module_context"):
        parents = {row.get("provenance", {}).get("module_evidence_id") for row in model.get(literals.CONTEXT_KEY, [])}
        optional = [row for row in model["module_context"] if row.get("evidence_id") not in parents]
        dropped = (optional or model["module_context"])[-1]
        model["module_context"].remove(dropped)
        omitted.append(dropped["evidence_id"])
        synchronize()
    # Do not expose edit permission for source that the numbered projection drops.
    while size() > max_chars:
        editable = [row for row in model.get("functions", []) if row.get("evidence_id") in eligible]
        if not editable:
            raise ValueError("Mandatory model evidence does not fit the 24k numbered projection")
        dropped = editable[-1]
        model["functions"].remove(dropped)
        omitted.append(dropped["evidence_id"])
        synchronize()
    visible = {row["evidence_id"] for row in model.get("functions", []) if "editable_lines" in row}
    # Grammar/citation IDs must correspond to payload still visible to model.
    synchronize()
    model["line_edit_projection_omissions"] = omitted
    # Omission accounting cannot silently overrun the same fixed budget.
    if size() > max_chars:
        model["line_edit_projection_omissions"] = {"count": len(omitted)}
    if size() > max_chars:
        model.pop("line_edit_projection_omissions")
    if size() > max_chars:
        raise ValueError("Mandatory model evidence does not fit the numbered projection budget")
    literal_visible = {row["evidence_id"] for row in model.get(literals.CONTEXT_KEY, [])}
    return Projection(canonical, model, {key: eligible[key] for key in visible},
                      {key: module_literals[key] for key in literal_visible})


def generation_schema(projection: Projection) -> dict:
    schema = LineHypothesis.model_json_schema()
    failure_ids = list(dict.fromkeys(row.get("evidence_id") for row in projection.model.get("failures", [])
        if row.get("evidence_id") in projection.model["evidence_ids"]))
    if not failure_ids:
        raise ValueError("The line protocol requires a supplied failure evidence ID")
    schema["properties"]["failure_evidence_id"] = {"type": "string", "enum": failure_ids}
    schema["properties"]["evidence_ids"]["items"] = {"type": "string", "enum": projection.model["evidence_ids"]}
    variants = []
    for evidence_id, row in sorted(projection.sources.items()):
        numbers = list(range(row["start_line"], row["end_line"] + 1))
        variants.append({"type": "object", "additionalProperties": False,
            "required": ["source_evidence_id", "start_line", "line_count", "replacement"],
            "properties": {"source_evidence_id": {"type": "string", "const": evidence_id},
                "start_line": {"type": "integer", "enum": numbers},
                "line_count": {"type": "integer", "enum": list(range(1, min(MAX_EDIT_LINES, len(numbers)) + 1))},
                "replacement": {"type": "string", "maxLength": 1200}}})
    for evidence_id in sorted(projection.literal_sources):
        variants.append({"type": "object", "additionalProperties": False,
            "required": ["source_evidence_id", "replacement_literal"],
            "properties": {"source_evidence_id": {"type": "string", "const": evidence_id},
                           "replacement_literal": {"type": "string", "minLength": 1, "maxLength": 1200}}})
    if variants:
        schema["properties"]["edits"]["items"] = {"anyOf": variants}
    else:
        schema["properties"]["edits"]["maxItems"] = 0
    return schema


def reconstruct(raw: dict, projection: Projection):
    result = LineHypothesis.model_validate(raw)
    failure_ids = {row.get("evidence_id") for row in projection.model.get("failures", [])}
    if result.failure_evidence_id not in failure_ids or result.failure_evidence_id not in projection.model["evidence_ids"]:
        raise ValueError("Select one actual model-visible failure evidence ID")
    if set(result.evidence_ids) - set(projection.model["evidence_ids"]):
        raise ValueError("Only model-visible evidence may be cited")
    replacements, occupied = [], []
    for edit in result.edits:
        if isinstance(edit, LiteralEdit):
            row = projection.literal_sources.get(edit.source_evidence_id)
            if row is None:
                raise ValueError("Each literal edit needs its visible module literal source citation")
            if any(path == row["path"] and row["start_line"] <= end and start <= row["end_line"] for path, start, end in occupied):
                raise ValueError("Absolute source edits overlap")
            occupied.append((row["path"], row["start_line"], row["end_line"]))
            replacements.append(literals.candidate_replacement(row, edit.replacement_literal))
            continue
        row = projection.sources.get(edit.source_evidence_id)
        if row is None:
            raise ValueError("Each line edit needs its visible editable source citation")
        end_line = edit.start_line + edit.line_count - 1
        if not row["start_line"] <= edit.start_line <= end_line <= row["end_line"]:
            raise ValueError("Absolute edit lines must stay inside cited source")
        if any(path == row["path"] and edit.start_line <= end and start <= end_line for path, start, end in occupied):
            raise ValueError("Absolute line edits overlap")
        occupied.append((row["path"], edit.start_line, end_line))
        lines = physical_lines(row["source"])
        start, end = edit.start_line - row["start_line"], end_line - row["start_line"] + 1
        for token in _string_tokens(row["source"]):
            if (token.start[0] < start + 1 <= token.end[0]) or (token.start[0] <= end < token.end[0]):
                raise ValueError("Line edits may not cut a multiline literal token")
        selected = lines[start:end]
        old = "".join(selected)
        endings = {_eol(line) for line in selected if _eol(line)}
        if len(endings) > 1:
            raise ValueError("Select a uniform-line-ending span")
        ending = next(iter(endings), next((_eol(line) for line in lines if _eol(line)), "\n"))
        new = _verbatim_replacement(edit.replacement, ending, bool(_eol(selected[-1])))
        replacements.append({"path": row["path"], "old": old, "new": new})
    references = [] if result.hypothesis is None else list(dict.fromkeys(
        [result.failure_evidence_id, *result.evidence_ids, *(edit.source_evidence_id for edit in result.edits)]))
    canonical = {"hypothesis": result.hypothesis, "evidence_ids": references,
                 "expected_test_behavior": result.expected_test_behavior, "replacements": replacements}
    # Every existing citation/source/1200-character/AST/helper/revision guard is
    # still authoritative. Duplicate old spans deliberately remain rejected.
    return literals.validate_generated(canonical, projection.canonical)
