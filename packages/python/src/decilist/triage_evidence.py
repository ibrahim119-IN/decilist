"""Bounded, untrusted evidence for test-failure diagnosis; never executes evidence.

Redaction is a heuristic for common credentials, not a secrecy guarantee. Source
context is read only for recognized log references confined to the supplied
project. No subprocess, repository scan, model, or network operation is used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PureWindowsPath
import re
from urllib.parse import unquote, urlsplit

__all__ = ["prepare_evidence", "has_failure_signal"]
_LOG_LIMIT = 12_000
_STATE_LIMIT = 24_000
_RAW_LIMIT = 48_000
_SOURCE_LIMIT = 128_000
_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs"}
_KEY = r"(?:[a-z0-9]+[_-])*(?:api[_-]?(?:key|token|secret)|access[_-]?(?:token|key)|secret[_-]?(?:access[_-]?)?key|private[_-]?key|auth[_-]?token|authorization|password|passwd|pwd|secret|client[_-]?secret|token)"
_ASSIGNMENT = re.compile(
    rf"(?P<prefix>\b{_KEY}[\"']?\s*[:=]\s*)(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|Bearer[ \t]+[^\s,;}}]+|[^\s,;}}]+)", re.I)
_SECRET_FLAG = re.compile(rf"^--?{_KEY}$", re.I)
_BEARER = re.compile(r"\bBearer[ \t]+(?!\[REDACTED\])[A-Za-z0-9._~+/=-]+", re.I)
_TOKEN = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{12,}|github_pat_[A-Za-z0-9_]{12,}|hf_[A-Za-z0-9]{12,}|AKIA[0-9A-Z]{16})\b")
_PRIVATE_KEY = re.compile(r"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----.*?(?:-----END(?: [A-Z0-9]+)* PRIVATE KEY-----|\Z)", re.S)
_ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PYTHON_FRAME = re.compile(r"^\s*File [\"'](.+?)[\"'], line ([1-9][0-9]{0,8})\b")
_PATH_LINE = re.compile(r"^\s*(?:[EF]\s+)?(.+?\.(?:py|tsx?|mjs|cjs|js)):([1-9][0-9]{0,8})(?::[1-9][0-9]{0,8})?(?::|\s|$)", re.I)
_JS_LOCATION = re.compile(r"(.+?\.(?:tsx?|mjs|cjs|js)):([1-9][0-9]{0,8}):[1-9][0-9]{0,8}$", re.I)
_EXCEPTION = re.compile(r"^\s*(?:E\s+)?(?:[A-Za-z_][\w.]*\.)?(?:[A-Za-z_][\w]*(?:Error|Exception)|AssertionError|KeyboardInterrupt|SystemExit|Error|Exception)(?::|\s*$)")
_FAILED_COUNT = re.compile(r"\b([0-9]{1,9})\s+(?:failed|errors?)\b", re.I)
_QUOTED_ABSOLUTE = re.compile(r"(?P<quote>[\"'])(?P<path>(?:[A-Za-z]:[\\/]|\\\\|/)[^\"'\r\n]*)(?P=quote)")
_FILE_URI = re.compile(r"file://[^\s\"'<>)]*", re.I)
_ABSOLUTE = re.compile(r"(?<![\w:/\\\]>])(?:[A-Za-z]:[\\/]|\\\\|/)[^\s\"'<>()[\]{},;]+")
_SECRET_PARTS = {"secret", "secrets", "credential", "credentials", "password", "passwords", "token", "tokens", "private", "env"}


def _clip(text: str, limit: int, json_limit: int | None = None) -> tuple[str, bool]:
    clipped = text[:limit]
    if json_limit is not None and len(json.dumps(clipped)) > json_limit:
        low, high = 0, len(clipped)
        while low < high:
            middle = (low + high + 1) // 2
            if len(json.dumps(clipped[:middle])) <= json_limit:
                low = middle
            else:
                high = middle - 1
        clipped = clipped[:low]
    return clipped, len(clipped) != len(text)


def _project_pattern(project: Path | None) -> re.Pattern | None:
    if project is None:
        return None
    prefix = str(project).rstrip("\\/")
    if not prefix:
        return None
    pattern = r"[\\/]".join(re.escape(part) for part in re.split(r"[\\/]", prefix))
    return re.compile(pattern + r"(?=[\\/]|[\s\"']|$)", re.I if os.name == "nt" else 0)


def _sanitize(text: str, project_pattern: re.Pattern | None) -> tuple[str, int]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", _ANSI.sub("", text))
    count = 0

    def private(match):
        nonlocal count
        count += 1
        # Preserve physical line numbers, including in source excerpts.
        return "[REDACTED PRIVATE KEY]" + "\n" * match.group().count("\n")

    def assignment(match):
        nonlocal count
        value = match["value"]
        if value.strip("\"'").upper() == "[REDACTED]":
            return match.group()
        count += 1
        quote = value[0] if value[:1] in {"'", '"'} else ""
        return match["prefix"] + quote + "[REDACTED]" + quote

    text = _PRIVATE_KEY.sub(private, text)
    text = _ASSIGNMENT.sub(assignment, text)
    text, replacements = _BEARER.subn("Bearer [REDACTED]", text)
    count += replacements
    text, replacements = _TOKEN.subn("[REDACTED]", text)
    count += replacements
    if project_pattern is not None:
        text, replacements = project_pattern.subn("[PROJECT]", text)
        count += replacements

    def external(path):
        nonlocal count
        if path.startswith(("[PROJECT]/", "[PROJECT]\\", "file://[PROJECT]/", "file:///[PROJECT]/")):
            return path
        count += 1
        # Keep a useful basename, never an outside path that could be read later.
        return "[EXTERNAL]/" + re.split(r"[\\/]", path.rstrip("\\/"))[-1]

    def file_uri(match):
        nonlocal count
        value = "file://" + match.group()[len("file://"):]
        if project_pattern is not None and "[PROJECT]" not in value:
            value, replacements = project_pattern.subn("[PROJECT]", unquote(value))
            count += replacements
        if value.startswith("file://localhost[PROJECT]/"):
            value = "file://[PROJECT]/" + value[len("file://localhost[PROJECT]/"):]
        return external(value)

    text = _QUOTED_ABSOLUTE.sub(lambda match: match["quote"] + external(match["path"]) + match["quote"], text)
    text = _FILE_URI.sub(file_uri, text)
    text = _ABSOLUTE.sub(lambda match: external(match.group()), text)
    return text, count


def _location(line: str) -> tuple[str, int] | None:
    match = _PYTHON_FRAME.match(line)
    if match:
        return match[1], int(match[2])
    stripped = line.strip()
    if stripped.startswith("at "):
        target = stripped[3:]
        if target.endswith(")") and "(" in target:
            target = target.rsplit("(", 1)[1][:-1]
        match = _JS_LOCATION.fullmatch(target)
        if match:
            return match[1], int(match[2])
    elif "@" in stripped:
        match = _JS_LOCATION.fullmatch(stripped.rsplit("@", 1)[1])
        if match:
            return match[1], int(match[2])
    match = _PATH_LINE.match(line)
    if match:
        return match[1], int(match[2])
    return None


def _signals(log: str) -> tuple[list[dict], bool]:
    signals = []
    for number, line in enumerate(log.splitlines(), 1):
        stripped = line.strip()
        if (_EXCEPTION.match(line) or re.match(r"^(?:FAILED\s+\S|FAIL\s+\S)", stripped)
                or re.match(r"^\s*E\s+assert\b", line)
                or any(int(match[1]) > 0 for match in _FAILED_COUNT.finditer(stripped))):
            kind = "failure"
        elif stripped == "Traceback (most recent call last):":
            kind = "traceback"
        elif _location(line):
            kind = "stack_frame"
        else:
            continue
        signals.append({"kind": kind, "line": number, "text": line})
    # Keep actual failure witnesses even when a long stack comes first.
    selected = sorted(sorted(signals, key=lambda row: row["kind"] != "failure")[:16], key=lambda row: row["line"])
    return selected, len(selected) != len(signals)


def has_failure_signal(log: str) -> bool:
    """Recognize the same bounded failure heuristic without trusting caller signals."""
    if not isinstance(log, str):
        raise TypeError("log must be a string")
    signals, _ = _signals(log[:_LOG_LIMIT])
    return any(signal["kind"] == "failure" for signal in signals)


def _secret_path(path: Path) -> bool:
    for part in path.parts:
        lower = part.lower()
        words = re.split(r"[._-]+", lower)
        if (lower.startswith(".env") or set(words) & _SECRET_PARTS
                or "privatekey" in lower or "apikey" in lower or lower.startswith("id_rsa")):
            return True
    return False


def _source_path(raw: str, project: Path) -> tuple[Path, str] | None:
    if len(raw) > 1024 or "[EXTERNAL]" in raw:
        return None
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", raw) and not raw.startswith("file://"):
        return None
    if raw.startswith("file://[PROJECT]/"):
        # Replacing a POSIX project prefix in file:///project/file yields this
        # placeholder form. Parse it before urlsplit interprets [] as an IPv6 host.
        raw = raw[len("file://"):]
    elif raw.startswith("file://"):
        try:
            parsed = urlsplit(raw)
        except ValueError:
            return None
        if parsed.netloc not in {"", "localhost"}:
            return None
        raw = unquote(parsed.path)
        if raw.startswith("/[PROJECT]") or (os.name == "nt" and re.match(r"^/[A-Za-z]:/", raw)):
            raw = raw[1:]
    if raw.startswith("[PROJECT]"):
        raw = raw[len("[PROJECT]"):]
        if raw and raw[0] not in "\\/":
            return None
        raw = raw.lstrip("\\/")
    # UNC/device paths can trigger network access. Drive-relative paths are ambiguous.
    windows = PureWindowsPath(raw)
    if raw.startswith(("\\\\", "//")) or (windows.drive and (not windows.root or os.name != "nt")):
        return None
    if ":" in raw and not windows.drive:
        return None
    if ".." in re.split(r"[\\/]", raw):
        return None
    path = Path(raw.replace("\\", "/"))
    if path.suffix.lower() not in _EXTENSIONS or _secret_path(path):
        return None
    candidate = path if path.is_absolute() else project / path
    try:
        relative = candidate.relative_to(project)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(project)
        if not resolved.is_file() or _secret_path(resolved.relative_to(project)):
            return None
        return resolved, relative.as_posix()
    except (OSError, ValueError, RuntimeError):
        return None


def _excerpt(path: Path, line: int, pattern: re.Pattern | None) -> tuple[str | None, int, bool]:
    try:
        with path.open("rb") as source:
            data = source.read(_SOURCE_LIMIT + 1)
    except OSError:
        return None, 0, False
    source_cut = len(data) > _SOURCE_LIMIT
    text, count = _sanitize(data[:_SOURCE_LIMIT].decode("utf-8", errors="replace"), pattern)
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    # A final partial line in a bounded read is not evidence of its full contents.
    if source_cut and lines:
        lines.pop()
    if not 1 <= line <= len(lines):
        return None, 0, source_cut
    start, end = max(0, line - 3), min(len(lines), line + 2)
    excerpt = "\n".join(f"{index + 1} | {lines[index]}" for index in range(start, end))
    excerpt, clipped = _clip(excerpt, 1200, 1600)
    return excerpt, count, clipped


def prepare_evidence(log: str, project: Path | None = None, command: list[str] | None = None) -> dict:
    """Prepare schema-v1 evidence. All strings remain untrusted; redaction is heuristic.

    Signal lines are 1-based indexes into the emitted log and text is its exact
    line. Only ``failure`` signals assert an observed failure heuristic; frames
    alone do not imply failed execution. References use 1-based source lines.
    The default JSON serialization is bounded to 24,000 characters.
    """
    if not isinstance(log, str):
        raise TypeError("log must be a string")
    if project is not None and not isinstance(project, Path):
        raise TypeError("project must be a pathlib.Path or None")
    if command is not None and (not isinstance(command, list) or any(not isinstance(arg, str) for arg in command)):
        raise TypeError("command must be a list of strings or None")
    root = None
    if project is not None and not str(project).startswith(("\\\\", "//")):
        try:
            root = project.resolve(strict=True)
            if not root.is_dir():
                root = None
        except (OSError, ValueError, RuntimeError):
            pass
    try:
        redaction_root = (project if str(project).startswith(("\\\\", "//")) else project.resolve()) if project is not None else None
    except (OSError, ValueError, RuntimeError):
        redaction_root = None
    pattern = _project_pattern(redaction_root)
    sanitized, count = _sanitize(log[:_RAW_LIMIT], pattern)
    bounded, cut = _clip(sanitized, _LOG_LIMIT, 13_000)
    truncated = cut or len(log) > _RAW_LIMIT
    redacted_command = None
    if command is not None:
        redacted_command = []
        secret_next = False
        for arg in command[:32]:
            if secret_next:
                value, changes = "[REDACTED]", 1
            else:
                value, changes = _sanitize(arg[:_RAW_LIMIT], pattern)
            secret_next = bool(_SECRET_FLAG.fullmatch(arg))
            count += changes
            value, clipped = _clip(value, 512, 1000)
            truncated |= clipped or len(arg) > _RAW_LIMIT
            if len(json.dumps(redacted_command + [value])) > 2000:
                truncated = True
                break
            redacted_command.append(value)
        truncated |= len(redacted_command) != len(command)
    references = []
    seen = set()
    if root is not None:
        attempts = 0
        for line in bounded.splitlines():
            location = _location(line)
            if not location:
                continue
            attempts += 1
            if attempts > 32:
                truncated = True
                break
            source = _source_path(location[0], root)
            if source is None:
                continue
            path, relative = source
            key = (relative, location[1])
            if key in seen:
                continue
            seen.add(key)
            if len(references) == 3:
                truncated = True
                break
            excerpt, changes, clipped = _excerpt(path, location[1], pattern)
            truncated |= clipped
            if excerpt is not None:
                row = {"path": relative, "line": location[1], "excerpt": excerpt}
                if len(json.dumps(row)) > 2200:
                    truncated = True
                    continue
                references.append(row)
                count += changes
    signals, clipped = _signals(bounded)
    result = {"schema_version": 1, "command": redacted_command, "log": bounded,
              "references": references, "signals": signals, "truncated": bool(truncated or clipped),
              "redaction_count": count}
    while len(json.dumps(result)) > _STATE_LIMIT:
        result["truncated"] = True
        expendable = next((index for index in range(len(signals) - 1, -1, -1) if signals[index]["kind"] != "failure"), None)
        if expendable is not None:
            signals.pop(expendable)
        elif len(signals) > 1:
            signals.pop()
        elif references:
            references.pop()
        else:
            # Even one unusually long failure line can duplicate most of the log.
            result["log"] = result["log"][:max(0, len(result["log"]) - 500)]
            signals[:], _ = _signals(result["log"])
    return result
