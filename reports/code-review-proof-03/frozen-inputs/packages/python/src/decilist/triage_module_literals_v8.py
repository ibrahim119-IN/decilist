"""Cited module literal leaves with canonical snapshot requalification.

Context metadata alone cannot authorize a write. The snapshot runner requalifies
raw bytes and hashes before mutation, then retains the existing whole-file screen.
No target-project code is imported or executed by source qualification.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import io
import json
import re
from string import Formatter
import tokenize

from . import triage_hypothesis_v6 as legacy


VERSION = "module_literal_leaf.1"
CONTEXT_KEY = "module_literal_sources"
MAX_FILE_BYTES = 256000
MAX_ASSIGNMENT_BYTES = 1200
MAX_ASSIGNMENT_LINES = 16
MAX_ROWS = 2
_FLAGS = {"ASCII", "IGNORECASE", "MULTILINE", "DOTALL", "VERBOSE", "UNICODE", "NOFLAG"}
_DYNAMIC = {"globals", "locals", "vars", "getattr", "setattr", "delattr", "eval", "exec", "__import__"}
_IGNORED_TOKENS = {tokenize.NL, tokenize.NEWLINE, tokenize.ENDMARKER, tokenize.ENCODING}


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _lines(text: str) -> list[str]:
    return re.findall(r"[^\r\n]*(?:\r\n|\n|\r|$)", text)[:-1]


def _ending(text: str) -> str:
    endings = set(re.findall(r"\r\n|\r|\n", text))
    if "\r" in endings or len(endings) > 1:
        raise ValueError("Literal assignment must use uniform LF or CRLF")
    return next(iter(endings), "\n")


def _parse(text: str, *, mode="exec"):
    try:
        tree = ast.parse(text, mode=mode, type_comments=True)
    except (SyntaxError, RecursionError, MemoryError) as exc:
        raise ValueError("Malformed or excessive literal source AST") from exc
    if sum(1 for _ in ast.walk(tree)) > 50000:
        raise ValueError("Literal source AST budget exceeded")
    return tree


def _tokens(text: str):
    try:
        return list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        raise ValueError("Incomplete literal token source") from exc


def _one_literal(text: str):
    """One complete str/bytes token, not concatenation, comments or expression."""
    if not isinstance(text, str) or not text or len(text.encode("utf-8")) > MAX_ASSIGNMENT_BYTES:
        raise ValueError("Literal token must be bounded text")
    tree = _parse(text, mode="eval")
    significant = [token for token in _tokens(text) if token.type not in _IGNORED_TOKENS]
    if (len(significant) != 1 or significant[0].type != tokenize.STRING
            or significant[0].string != text or significant[0].start != (1, 0)
            or not isinstance(tree.body, ast.Constant) or type(tree.body.value) not in {str, bytes}):
        raise ValueError("Only one complete immutable string/bytes literal token is supported")
    return tree.body.value


def _root_name(node):
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _stable_binding(tree, name: str) -> bool:
    """Conservative syntax check; no claim about arbitrary imported side effects."""
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
            return False
        if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
            return False
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            count += node.id == name
        elif isinstance(node, ast.arg):
            count += node.arg == name
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            count += node.name == name
        elif isinstance(node, ast.ExceptHandler):
            count += node.name == name
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)):
            count += node.name == name
        elif isinstance(node, ast.MatchMapping):
            count += node.rest == name
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            count += sum((alias.asname or (alias.name.split(".")[0] if isinstance(node, ast.Import)
                                          else alias.name)) == name for alias in node.names)
        if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if _root_name(node) == name:
                return False
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value == name:
                return False
    return count == 1


def _dynamic_namespace(tree) -> bool:
    return any((isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in _DYNAMIC)
               or (isinstance(node, ast.Attribute) and node.attr in _DYNAMIC)
               for node in ast.walk(tree))


def _canonical_re(tree) -> bool:
    imports = [node for node in tree.body if isinstance(node, ast.Import)
               and len(node.names) == 1 and node.names[0].name == "re" and node.names[0].asname is None]
    if len(imports) != 1 or not _stable_binding(tree, "re"):
        return False
    # A second alias can mutate the same module/function without storing to the
    # spelling "re". Do not treat one canonical import as proof against that.
    re_imports = [node for node in ast.walk(tree) if isinstance(node, ast.Import)
                  and any(alias.name == "re" or alias.name.startswith("re.") for alias in node.names)]
    if (re_imports != imports or any(isinstance(node, ast.ImportFrom)
            and node.module is not None and (node.module == "re" or node.module.startswith("re."))
            for node in ast.walk(tree))):
        return False
    parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == "re":
            parent = parents.get(id(node))
            # Reject exposing the module through an alias/argument/return.
            if not isinstance(parent, ast.Attribute) or parent.value is not node:
                return False
            if parent.attr.startswith("__") and parent.attr.endswith("__"):
                return False
            if parent.attr == "compile":
                caller = parents.get(id(parent))
                if not isinstance(caller, ast.Call) or caller.func is not parent:
                    return False
    return True


def _safe_flags(node, depth=0) -> bool:
    if depth > 3:
        return False
    if isinstance(node, ast.Constant):
        return type(node.value) is int and node.value == 0
    if isinstance(node, ast.Attribute):
        return isinstance(node.value, ast.Name) and node.value.id == "re" and node.attr in _FLAGS
    return (isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
            and _safe_flags(node.left, depth + 1) and _safe_flags(node.right, depth + 1))


def _format_keywords(call, fragment: str):
    """Existing literal str.format with exact unique simple keyword fields only."""
    if (not isinstance(call, ast.Call) or call.args or not 1 <= len(call.keywords) <= 8
            or not isinstance(call.func, ast.Attribute) or call.func.attr != "format"
            or not isinstance(call.func.value, ast.Constant) or type(call.func.value.value) is not str):
        raise ValueError("Only the existing literal-template keyword format shape is supported")
    template_node, keywords = call.func.value, {}
    template_source = ast.get_source_segment(fragment, template_node)
    if template_source is None:
        raise ValueError("Format template source is unavailable")
    # Only this unchanged receiver may contain bounded implicit concatenation.
    # Parenthesis framing permits its existing physical newlines to be parsed
    # without treating indentation as statements. Replacements remain one token.
    wrapped = "(" + template_source + ")"
    template_tree = _parse(wrapped, mode="eval")
    significant = [token for token in _tokens(wrapped) if token.type not in _IGNORED_TOKENS]
    tokens = significant[1:-1]
    if (not 1 <= len(tokens) <= 8 or significant[0].string != "(" or significant[-1].string != ")"
            or any(token.type != tokenize.STRING or type(_one_literal(token.string)) is not str for token in tokens)
            or not isinstance(template_tree.body, ast.Constant) or type(template_tree.body.value) is not str
            or template_tree.body.value != template_node.value):
        raise ValueError("Format template must remain bounded unchanged adjacent str literal tokens")
    for keyword in call.keywords:
        if (not isinstance(keyword.arg, str) or not keyword.arg.isidentifier() or keyword.arg in keywords
                or keyword.arg.startswith("__") or not isinstance(keyword.value, ast.Constant)
                or type(keyword.value.value) is not str):
            raise ValueError("Format keyword values must be fixed unique literal strings")
        token = ast.get_source_segment(fragment, keyword.value)
        if token is None or type(_one_literal(token)) is not str:
            raise ValueError("Format keyword must be one complete literal token")
        keywords[keyword.arg] = keyword.value
    template = template_node.value
    try:
        parsed = list(Formatter().parse(template))
    except ValueError as exc:
        raise ValueError("Malformed static format template") from exc
    fields = [field for _, field, spec, conversion in parsed if field is not None]
    if (not 1 <= len(fields) <= 8 or len(fields) != len(set(fields)) or set(fields) != set(keywords)
            or any(not field.isidentifier() or field.startswith("__") for field in fields)
            or any(spec or conversion is not None for _, _, spec, conversion in parsed)):
        raise ValueError("Format fields must match unique exact keywords without access/conversion/specifiers")
    # Formatter reports both {field} and {field:} with spec="". Enforce the
    # narrower spelling explicitly while allowing escaped literal braces.
    index, spelled_fields = 0, []
    while index < len(template):
        char = template[index]
        if char in "{}" and index + 1 < len(template) and template[index + 1] == char:
            index += 2
            continue
        if char == "{":
            end = template.find("}", index + 1)
            if end < 0 or not template[index + 1:end].isidentifier():
                raise ValueError("Format fields must use the exact {keyword} spelling")
            spelled_fields.append(template[index + 1:end])
            index = end + 1
        elif char == "}":
            raise ValueError("Unescaped format closing brace")
        else:
            index += 1
    if spelled_fields != fields:
        raise ValueError("Format field syntax is not canonical")
    return keywords


def _assignment(fragment: str, module_tree, *, selector=None):
    """Return one single-name Assign and its allowed literal leaf, or reject."""
    if (len(fragment.encode("utf-8")) > MAX_ASSIGNMENT_BYTES
            or not 1 <= len(_lines(fragment)) <= MAX_ASSIGNMENT_LINES):
        raise ValueError("Module literal assignment exceeds its source budget")
    _ending(fragment)
    tree = _parse(fragment)
    if any(token.type == tokenize.OP and token.string == ";" for token in _tokens(fragment)):
        raise ValueError("Mixed physical statements are not a module literal")
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assign):
        raise ValueError("Only one unannotated module assignment is supported")
    node = tree.body[0]
    if (len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name)
            or node.type_comment is not None or node.lineno != 1 or node.end_lineno != len(_lines(fragment))):
        raise ValueError("Literal assignment binding/span is not exact")
    name = node.targets[0].id
    if (name.startswith("__") and name.endswith("__")) or (module_tree is not None and not _stable_binding(module_tree, name)):
        raise ValueError("Literal assignment binding is ambiguous or protected")
    literal, kind, selected = node.value, "direct_literal", "value"
    selectors = [selected]
    if isinstance(literal, ast.Call):
        call = literal
        if (not isinstance(call.func, ast.Attribute) or not isinstance(call.func.value, ast.Name)
                or call.func.value.id != "re" or call.func.attr != "compile"
                or (module_tree is not None and not _canonical_re(module_tree))
                or not 1 <= len(call.args) <= 2 or len(call.keywords) > 1
                or any(keyword.arg != "flags" for keyword in call.keywords)
                or (len(call.args) == 2 and call.keywords)):
            raise ValueError("Only the canonical existing regex wrapper is supported")
        flags = call.args[1:] + [keyword.value for keyword in call.keywords]
        if any(not _safe_flags(flag) for flag in flags):
            raise ValueError("Regex flags are unknown, dynamic, or effectful")
        literal, kind, selected = call.args[0], "canonical_re_compile_literal", "pattern"
        selectors = [selected]
        if isinstance(literal, ast.Call):
            keywords = _format_keywords(literal, fragment)
            selectors = ["format_keyword:" + name for name in keywords]
            selected = selectors[0] if selector is None else selector
            if selected not in selectors:
                raise ValueError("Unknown static-format literal selector")
            literal, kind = keywords[selected.split(":", 1)[1]], "canonical_re_compile_format_keyword"
    if selector is not None and selector != selected:
        raise ValueError("Literal selector does not match the canonical assignment")
    if not isinstance(literal, ast.Constant) or type(literal.value) not in {str, bytes}:
        raise ValueError("Assignment value is not a supported string/bytes literal leaf")
    token = ast.get_source_segment(fragment, literal)
    if token is None or _one_literal(token) != literal.value:
        raise ValueError("Literal leaf is not one exact complete token")
    # Locate by token coordinates, not string matching (which may be ambiguous).
    matching = [item for item in _tokens(fragment) if item.type == tokenize.STRING
                and item.start[0] == literal.lineno and item.end[0] == literal.end_lineno
                and item.string == token
                and len(_lines(fragment)[item.start[0] - 1][:item.start[1]].encode("utf-8")) == literal.col_offset]
    if len(matching) != 1:
        raise ValueError("Literal token location is ambiguous")
    selected = matching[0]
    lines = _lines(fragment)
    offset = lambda pos: sum(len(line) for line in lines[:pos[0] - 1]) + pos[1]
    return node, literal, kind, token, offset(selected.start), offset(selected.end), selectors


def qualify_module_literals(raw_file: bytes, module_row: dict) -> list[dict]:
    """Only already-supplied clean snippets can gain separate literal authority."""
    if not isinstance(raw_file, bytes) or len(raw_file) > MAX_FILE_BYTES:
        raise ValueError("Canonical module bytes are required and bounded")
    if (not isinstance(module_row, dict) or not legacy._source_path(module_row.get("path"))
            or not isinstance(module_row.get("evidence_id"), str) or not module_row["evidence_id"]
            or len(module_row["evidence_id"]) > 1900
            or any(ord(char) < 32 for char in module_row["evidence_id"])
            or module_row.get("complete") is not False or module_row.get("editable") is not False
            or not isinstance(module_row.get("snippets"), list) or len(module_row["snippets"]) > 128):
        raise ValueError("A bounded existing read-only module context row is required")
    try:
        text = raw_file.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("Initial literal prototype supports canonical UTF-8 source only") from exc
    tree = _parse(text)
    if _dynamic_namespace(tree):
        return []
    spans = [(item["start_line"], item["end_line"]) for item in module_row["snippets"]
             if isinstance(item, dict) and type(item.get("start_line")) is int and type(item.get("end_line")) is int]
    if len(spans) != len(set(spans)):
        raise ValueError("Duplicate supplied module snippet spans are ambiguous")
    lines, rows, seen = _lines(text), [], set()
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)]
    for snippet in module_row["snippets"]:
        if not isinstance(snippet, dict) or type(snippet.get("start_line")) is not int or type(snippet.get("end_line")) is not int:
            continue
        start, end = snippet["start_line"], snippet["end_line"]
        if (start, end) in seen:
            raise ValueError("Duplicate supplied module snippet spans are ambiguous")
        if not 1 <= start <= end <= len(lines):
            continue
        seen.add((start, end))
        canonical = "".join(lines[start - 1:end])
        # Existing snippets are LF-normalized/sanitized. Restore raw bytes only
        # when equality proves that no redaction changed the selected source.
        if snippet.get("source") != canonical.replace("\r\n", "\n").replace("\r", "\n"):
            continue
        matches = [node for node in assignments if node.lineno == start and node.end_lineno == end]
        if len(matches) != 1:
            continue
        try:
            *_, selectors = _assignment(canonical, tree)
        except ValueError:
            continue
        for selector in selectors:
            node, leaf, kind, token, begin, finish, _ = _assignment(canonical, tree, selector=selector)
            evidence_id = f"{module_row['evidence_id']}.literal.{start}.{selector}"
            rows.append({"evidence_id": evidence_id, "path": module_row["path"], "symbol": node.targets[0].id,
                "start_line": start, "end_line": end, "source": canonical,
                "source_sha256": _hash(canonical.encode("utf-8")), "file_sha256": _hash(raw_file),
                "literal_source": token, "literal_type": type(leaf.value).__name__, "literal_selector": selector,
                "literal_start_offset": begin, "literal_end_offset": finish, "literal_kind": kind,
                "complete": True, "editable": True, "roles": ["module_literal"], "runtime_observed": False,
                "provenance": {"kind": "already_supplied_module_literal", "module_evidence_id": module_row["evidence_id"]}})
            if len(rows) == MAX_ROWS:
                break
        if len(rows) == MAX_ROWS:
            break
    return rows


def _masked_assignment(fragment: str, module_tree, selector: str):
    node, leaf, *_ = _assignment(fragment, module_tree, selector=selector)
    cloned = copy.deepcopy(node)
    if selector == "value":
        target = cloned.value
    elif selector == "pattern":
        target = cloned.value.args[0]
    else:
        target = next(keyword.value for keyword in cloned.value.args[0].keywords
                      if keyword.arg == selector.split(":", 1)[1])
    target.value, target.kind = b"" if type(leaf.value) is bytes else "", None
    return ast.dump(cloned, include_attributes=False)


def reconstruct_literal(raw_file: bytes, module_row: dict, row: dict, replacement_literal: str, *,
                        expected_file_sha256: str, cited_ids) -> dict:
    """Revalidate canonical snapshot bytes, then produce old/new only; no writes."""
    if (not isinstance(raw_file, bytes) or not isinstance(expected_file_sha256, str) or _hash(raw_file) != expected_file_sha256
            or not isinstance(row, dict) or row.get("file_sha256") != expected_file_sha256):
        raise ValueError("Canonical snapshot file hash changed")
    canonical = [item for item in qualify_module_literals(raw_file, module_row)
                 if item["evidence_id"] == row.get("evidence_id")]
    if len(canonical) != 1 or canonical[0] != row:
        raise ValueError("Module literal authority is stale, forged, incomplete or changed")
    if not isinstance(cited_ids, (list, tuple)) or len(cited_ids) > 8 or row["evidence_id"] not in cited_ids:
        raise ValueError("Exact supplied module literal source citation is required")
    result = candidate_replacement(row, replacement_literal)
    old, new = result["old"], result["new"]
    text = raw_file.decode("utf-8")
    tree = _parse(text)
    if _masked_assignment(old, tree, row["literal_selector"]) != _masked_assignment(new, tree, row["literal_selector"]):
        raise ValueError("Only the selected literal leaf may change")
    if text.count(old) != 1:
        raise ValueError("Canonical old assignment must match exactly once")
    candidate = text.replace(old, new, 1)
    compile(candidate, row["path"], "exec", dont_inherit=True)
    from .triage_verify_v6 import _screen_ast_changes
    _screen_ast_changes(text, candidate, row["path"])
    return result


def candidate_replacement(row: dict, replacement_literal: str) -> dict:
    """Render from checked evidence only; snapshot requalification is still required."""
    if not isinstance(replacement_literal, str) or "\r" in replacement_literal:
        raise ValueError("Replacement literal uses LF only")
    before_value, after_value = _one_literal(row["literal_source"]), _one_literal(replacement_literal)
    if type(before_value) is not type(after_value):
        raise ValueError("Literal runtime type must remain unchanged")
    if before_value == after_value:
        raise ValueError("Identical literal value is not a source intervention")
    old = row["source"]
    token = replacement_literal.replace("\n", _ending(old))
    new = old[:row["literal_start_offset"]] + token + old[row["literal_end_offset"]:]
    if _masked_assignment(old, None, row["literal_selector"]) != _masked_assignment(new, None, row["literal_selector"]):
        raise ValueError("Only the selected literal leaf may change")
    return {"path": row["path"], "old": old, "new": new}


def checked_literal_sources(context: dict) -> dict[str, dict]:
    """Check bounded source metadata; canonical raw snapshot checks remain separate."""
    rows = context.get(CONTEXT_KEY, [])
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError("Expected at most two bounded module literal source rows")
    if not rows:
        return {}
    known, checked, total = set(context.get("evidence_ids", [])), {}, 0
    parents = context.get("module_context", [])
    if not isinstance(parents, list) or any(not isinstance(parent, dict) for parent in parents):
        raise ValueError("Module literal parents must be supplied module context rows")
    fields = {"evidence_id", "path", "symbol", "start_line", "end_line", "source", "source_sha256", "file_sha256",
              "literal_source", "literal_type", "literal_selector", "literal_start_offset", "literal_end_offset",
              "literal_kind", "complete", "editable", "roles", "runtime_observed", "provenance"}
    for row in rows:
        if (not isinstance(row, dict) or set(row) != fields or not isinstance(row.get("evidence_id"), str)
                or row.get("evidence_id") not in known
                or row.get("evidence_id") in checked or not legacy._source_path(row.get("path"))
                or row.get("complete") is not True or row.get("editable") is not True
                or row.get("runtime_observed") is not False or row.get("roles") != ["module_literal"]
                or type(row.get("start_line")) is not int or type(row.get("end_line")) is not int
                or not 1 <= row["start_line"] <= row["end_line"]
                or not isinstance(row.get("source"), str) or not row["source"]
                or not isinstance(row.get("literal_selector"), str)
                or not isinstance(row.get("file_sha256"), str) or re.fullmatch(r"[0-9a-f]{64}", row["file_sha256"]) is None
                or row.get("source_sha256") != _hash(row["source"].encode("utf-8"))):
            raise ValueError("Module literal source metadata is incomplete, changed or ambiguous")
        total += len(row["source"].encode("utf-8"))
        if total > 2400 or len(_lines(row["source"])) != row["end_line"] - row["start_line"] + 1:
            raise ValueError("Module literal source byte/line budget exceeded")
        provenance = row["provenance"]
        if (not isinstance(provenance, dict) or set(provenance) != {"kind", "module_evidence_id"}
                or provenance.get("kind") != "already_supplied_module_literal"):
            raise ValueError("Module literal source needs its distinct module provenance")
        anchors = [parent for parent in parents if parent.get("evidence_id") == provenance["module_evidence_id"]]
        if len(anchors) != 1:
            raise ValueError("Module literal source has no unique supplied parent")
        parent = anchors[0]
        if (parent.get("evidence_id") not in known or parent.get("path") != row["path"]
                or parent.get("complete") is not False or parent.get("editable") is not False
                or not isinstance(parent.get("snippets"), list)):
            raise ValueError("Module literal parent must remain distinct and read-only")
        matches = [snippet for snippet in parent["snippets"] if isinstance(snippet, dict)
                   and snippet.get("start_line") == row["start_line"] and snippet.get("end_line") == row["end_line"]
                   and snippet.get("source") == row["source"].replace("\r\n", "\n")]
        if len(matches) != 1:
            raise ValueError("Module literal source is absent from its exact clean parent snippet")
        node, leaf, kind, token, begin, finish, _ = _assignment(row["source"], None, selector=row["literal_selector"])
        if (row["evidence_id"] != f"{parent['evidence_id']}.literal.{row['start_line']}.{row['literal_selector']}"
                or row["symbol"] != node.targets[0].id or row["literal_kind"] != kind
                or row["literal_source"] != token or row["literal_type"] != type(leaf.value).__name__
                or type(row["literal_start_offset"]) is not int or row["literal_start_offset"] != begin
                or type(row["literal_end_offset"]) is not int or row["literal_end_offset"] != finish):
            raise ValueError("Module literal token, selector or binding metadata changed")
        if any(isinstance(other, dict) and other.get("evidence_id") == row["evidence_id"]
               for key in ("functions", "module_context", "events", "failures", "read_only_helpers")
               for other in context.get(key, [])):
            raise ValueError("Module literal evidence ID collides with other supplied evidence")
        checked[row["evidence_id"]] = row
    return checked


def checked_context(context: dict) -> dict:
    result = legacy._checked_context(context)
    if CONTEXT_KEY in context:
        result[CONTEXT_KEY] = json.loads(json.dumps(context[CONTEXT_KEY], ensure_ascii=False, allow_nan=False))
        if len(legacy._json_data(result)) > legacy.MAX_CONTEXT_CHARS:
            raise ValueError("Runtime context with module literals exceeds its bounded input size")
        checked_literal_sources(result)
    return result


def _split_replacements(result, context):
    rows = checked_literal_sources(context)
    function_replacements, module_changes, spans = [], [], []
    for replacement in result.replacements:
        if replacement.old == replacement.new:
            raise legacy.GenerationValidationError("no_op_replacement", "old and new are identical; supply a real grounded change or abstain from a repair.", "replacements")
        candidates = [row for row in rows.values() if row["path"] == replacement.path and row["source"] == replacement.old]
        if not candidates:
            function_replacements.append(replacement.model_dump())
            continue
        matches = []
        for row in candidates:
            if row["evidence_id"] not in result.evidence_ids:
                continue
            try:
                _, _, _, token, *_ = _assignment(replacement.new, None, selector=row["literal_selector"])
                rendered = candidate_replacement(row, token)
            except ValueError:
                continue
            if rendered == replacement.model_dump():
                matches.append(row)
        if len(matches) != 1:
            raise legacy.GenerationValidationError("module_literal_invalid", "Change only one cited module literal leaf with its exact source authority.", "replacements")
        row = matches[0]
        spans.append((row["path"], (row["start_line"], 0), legacy._position(row, len(row["source"]))))
        module_changes.append((row, replacement))
    function_result = legacy.validate_generated({**result.model_dump(), "replacements": function_replacements}, context)
    for replacement in function_result.replacements:
        locations = set()
        for row in legacy._editable_sources(context):
            if row["path"] != replacement.path:
                continue
            offset = 0
            while (offset := row["source"].find(replacement.old, offset)) >= 0:
                locations.add((legacy._position(row, offset), legacy._position(row, offset + len(replacement.old))))
                offset += 1
        start, end = next(iter(locations))
        spans.append((replacement.path, start, end))
    for index, (path, start, end) in enumerate(spans):
        if any(path == other_path and start < other_end and other_start < end
               for other_path, other_start, other_end in spans[:index]):
            raise ValueError("Source replacements overlap")
    return function_result, module_changes


def validate_generated(raw: dict, context: dict):
    if not context.get(CONTEXT_KEY):
        return legacy.validate_generated(raw, context)
    result = legacy.Hypothesis.model_validate(raw)
    _split_replacements(result, context)
    return result


def intervention_signature(result, context: dict) -> str | None:
    if not context.get(CONTEXT_KEY):
        return legacy.intervention_signature(result, context)
    function_result, changes = _split_replacements(result, context)
    function_signature = legacy.intervention_signature(function_result, context)
    if not changes:
        return function_signature
    affected = []
    for row, replacement in changes:
        node, leaf, *_ = _assignment(replacement.new, None, selector=row["literal_selector"])
        # The selected value's u-prefix/quote spelling is not a new repair.
        # Keep the actual decoded value while normalizing this lexical marker.
        leaf.kind = None
        affected.append({"path": row["path"], "start_line": row["start_line"], "end_line": row["end_line"],
                         "resulting_ast": ast.dump(node, include_attributes=False)})
    affected.sort(key=lambda row: (row["path"], row["start_line"], row["end_line"]))
    return legacy._fingerprint({"function_signature": function_signature, "module_interventions": affected})


def checked_feedback(feedback, context):
    return legacy._checked_feedback(feedback, context, validate_fn=validate_generated)


def validate_revision(result, context, feedback):
    return legacy._validate_revision(result, context, feedback, signature_fn=intervention_signature)
