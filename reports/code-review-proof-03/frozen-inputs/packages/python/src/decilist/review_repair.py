"""Bounded, proof-first JavaScript repair orchestration, without model or process I/O.

The host is a trusted boundary: it must execute untrusted reproduction bodies in an
isolated runtime, independently count assertions/classify failures, and terminate
its processes on cancellation. The added-code filter is defense in depth, not a
sandbox. Accepted workspaces are review artifacts; this module never applies them
to an original checkout and makes no claims about human time saved.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from time import perf_counter
from types import MappingProxyType
from typing import Annotated, Literal, Protocol

from pydantic import Field, StringConstraints, ValidationError, model_validator

from .contracts import Contract

BoundedText = Annotated[str, StringConstraints(min_length=1, max_length=16000)]
Outcome = Literal["pass", "assertion_failure", "syntax_error", "import_error", "timeout", "error"]
RepairStatus = Literal["accepted_candidate", "unproven", "invalid", "infrastructure_failure", "regression"]


def source_sha256(text: str) -> str:
    """Hash exact UTF-8 source text, including whitespace and line endings."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SourceSnapshot:
    """Read-only input to a provider; hashes refer to the original supplied files."""

    sources: Mapping[str, str]
    source_hashes: Mapping[str, str]
    sha256: str


class ReproductionSpec(Contract):
    """An untrusted JS async-function body; the host supplies tracked assert/load.

    ``load`` must be limited to the host's original, allowlisted source entries.
    The filename is descriptive and never controls an execution command.
    """

    code: str = Field(min_length=1, max_length=4000)
    filename: Literal["reproduction.cjs", "reproduction.mjs", "reproduction.js"] = "reproduction.cjs"


class DefectHypothesis(Contract):
    claim: BoundedText
    path: str = Field(min_length=1, max_length=512)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    evidence: BoundedText
    reproduction: ReproductionSpec

    @model_validator(mode="after")
    def ordered(self):
        if self.end_line < self.start_line:
            raise ValueError("reversed evidence range")
        return self


class LineEdit(Contract):
    """Replace an inclusive original line range, guarded by that file's hash."""

    path: str = Field(min_length=1, max_length=512)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    replacement: str = Field(max_length=16000)


class ExecutionResult(Contract):
    """Trusted host observations, never provider assertions about test success."""

    outcome: Outcome
    assertions: int = Field(ge=0)
    evidence: str = Field(default="", max_length=4000)


class RepairPolicy(Contract):
    max_hypotheses: int = Field(default=3, ge=1, le=5)
    max_edits: int = Field(default=2, ge=1, le=2)
    max_changed_lines: int = Field(default=30, ge=1, le=30)
    timeout_ms: int = Field(default=300000, ge=180000, le=900000)
    provider_timeout_ms: int = Field(default=120000, ge=1, le=300000)
    host_timeout_ms: int = Field(default=30000, ge=1, le=120000)


class RepairAttempt(Contract):
    hypothesis: DefectHypothesis | None = None
    status: RepairStatus = "invalid"
    reason: str = "not_started"
    baseline_reproduction: ExecutionResult | None = None
    baseline_regression: ExecutionResult | None = None
    candidate_reproduction: ExecutionResult | None = None
    candidate_regression: ExecutionResult | None = None
    edits: list[LineEdit] = Field(default_factory=list)
    timings_ms: dict[str, float] = Field(default_factory=dict)


class RepairResult(Contract):
    schema_version: Literal["review-repair.1"] = "review-repair.1"
    status: RepairStatus
    reason: str
    source_sha256: str
    attempts: list[RepairAttempt]
    candidate_workspace: str | None = None
    latency_ms: float = Field(ge=0)
    human_time_claim: Literal["not_measured"] = "not_measured"


class RepairProvider(Protocol):
    async def hypothesize(self, snapshot: SourceSnapshot, task: str) -> list[DefectHypothesis]: ...

    async def repair(self, snapshot: SourceSnapshot, hypothesis: DefectHypothesis,
                     baseline: ExecutionResult) -> list[LineEdit]: ...


class RepairHost(Protocol):
    """Callbacks must not mutate project files or the persisted reproduction body."""

    async def reproduce(self, workspace: Path, spec: ReproductionSpec) -> ExecutionResult: ...

    async def regress(self, workspace: Path) -> ExecutionResult: ...


class _Invalid(ValueError):
    pass


class _Infrastructure(Exception):
    pass


def _safe_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return bool(path and "\\" not in path and ":" not in path and "\0" not in path
                and not pure.is_absolute() and pure.as_posix() == path
                and all(part not in (".", "..") for part in path.split("/")))


def _source_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return (_safe_path(path) and pure.suffix.lower() in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"}
            and not any(part.startswith(".") or part.lower() in {"tests", "test", "__tests__", "node_modules"}
                        for part in pure.parts)
            and not re.search(r"(?:^|[._-])(?:test|spec)(?:[._-]|$)", pure.name, re.I))


# Conservative rejection of direct/obfuscated capability access. Parsing and OS
# isolation belong to the host; no string filter proves code safe.
_CAPABILITIES = re.compile(
    r"\b(?:import|require|export\s+\*|process|globalThis|global|window|document|navigator|self|"
    r"fetch|XMLHttpRequest|WebSocket|Worker|SharedWorker|Deno|Bun|eval|Function|constructor|"
    r"__proto__|Reflect|Proxy|module|Buffer)\b|"
    r"\b(?:child_process|node:|fs\b|filesystem|readFile|writeFile|execFile|spawn|execSync|"
    r"readFileSync|writeFileSync|openSync|createConnection|connect\s*\()|"
    r"\\(?:u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2})|\[\s*[\"'`]",
)


def _snapshot(sources: Mapping[str, str]) -> SourceSnapshot:
    copied = dict(sources)
    hashes = {path: source_sha256(code) for path, code in copied.items()}
    digest = source_sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")))
    return SourceSnapshot(MappingProxyType(copied), MappingProxyType(hashes), digest)


def _validated(model, value):
    # Revalidate even objects created with model_construct/model_copy.
    return model.model_validate(value.model_dump() if isinstance(value, model) else value)


class ReviewRepairEngine:
    """Prove first, try one minimal candidate per hypothesis, preserve originals.

    Files are materialized exclusively beneath scratch_root. Every hypothesis gets
    a new baseline and candidate made from the original immutable snapshot. All
    paths are relative POSIX paths; allowed_paths restricts edits, not execution.
    Existing checks must pass on the baseline with tracked assertions. A proposed
    defect must then cause a real assertion failure before repair is requested.
    The same immutable reproduction and existing checks must pass after repair.
    Workspaces are retained in scratch_root for host diagnostics; only an accepted
    candidate's path is exposed in the result. Caller cancellation propagates.
    """

    def __init__(self, provider: RepairProvider, host: RepairHost, policy: RepairPolicy | None = None):
        self.provider, self.host = provider, host
        self.policy = _validated(RepairPolicy, policy or RepairPolicy())

    async def run(self, sources: Mapping[str, str], *, task: str, allowed_paths: set[str],
                  scratch_root: Path) -> RepairResult:
        started = perf_counter()
        attempts: list[RepairAttempt] = []
        snapshot = _snapshot({})
        status: RepairStatus = "invalid"
        reason, candidate = "invalid_input", None
        try:
            if (not isinstance(sources, Mapping) or not isinstance(task, str)
                    or any(not isinstance(path, str) or not isinstance(code, str) for path, code in sources.items())):
                raise _Invalid("invalid_input")
            snapshot = _snapshot(sources)
            allowed_paths = frozenset(allowed_paths)
            if (not snapshot.sources or len(snapshot.sources) > 256 or not task.strip() or len(task) > 16000
                    or any(not _safe_path(path) or not isinstance(code, str) or "\0" in code
                           for path, code in snapshot.sources.items())
                    or sum(len(code.encode("utf-8")) for code in snapshot.sources.values()) > 2_000_000
                    or any(PurePosixPath(path).parts[0] == ".decilist-proof" for path in snapshot.sources)
                    or not allowed_paths or any(path not in snapshot.sources or not _source_path(path)
                                                for path in allowed_paths)):
                raise _Invalid("invalid_input")
            scratch_root = Path(scratch_root).resolve()
            scratch_root.mkdir(parents=True, exist_ok=True)
            async with asyncio.timeout(self.policy.timeout_ms / 1000):
                raw = await self._provider_call(self.provider.hypothesize(snapshot, task))
                if not isinstance(raw, list) or len(raw) > self.policy.max_hypotheses:
                    raise _Invalid("hypothesis_count")
                for item in raw:
                    attempt = RepairAttempt()
                    attempts.append(attempt)
                    try:
                        hypothesis = _validated(DefectHypothesis, item)
                        attempt.hypothesis = hypothesis
                        self._evidence_range(snapshot, hypothesis, allowed_paths)
                        candidate = await self._attempt(snapshot, hypothesis, attempt, scratch_root, allowed_paths)
                    except (ValidationError, _Invalid) as exc:
                        attempt.status = "invalid"
                        attempt.reason = str(exc) if isinstance(exc, _Invalid) else "invalid_provider_output"
                    except _Infrastructure as exc:
                        attempt.status, attempt.reason = "infrastructure_failure", str(exc)
                    if candidate is not None:
                        break
                # Report a substantive rejected candidate before unproven claims.
                status = next((kind for kind in ("accepted_candidate", "regression", "infrastructure_failure", "invalid", "unproven")
                               if any(attempt.status == kind for attempt in attempts)), "unproven")
                reason = next((attempt.reason for attempt in reversed(attempts) if attempt.status == status), "no_hypothesis")
        except _Invalid as exc:
            status, reason = "invalid", str(exc)
        except (TimeoutError, _Infrastructure) as exc:
            status, reason = "infrastructure_failure", "overall_timeout" if isinstance(exc, TimeoutError) else str(exc)
            if attempts and attempts[-1].reason == "not_started":
                attempts[-1].status, attempts[-1].reason = status, reason
        except Exception:
            # Provider/host errors may contain private source: retain only codes.
            status, reason = "infrastructure_failure", "infrastructure_error"
        return RepairResult(status=status, reason=reason, source_sha256=snapshot.sha256, attempts=attempts,
                            candidate_workspace=str(candidate) if status == "accepted_candidate" else None,
                            latency_ms=(perf_counter() - started) * 1000)

    async def _provider_call(self, call):
        try:
            async with asyncio.timeout(self.policy.provider_timeout_ms / 1000):
                return await call
        except TimeoutError:
            raise _Infrastructure("provider_timeout") from None
        except Exception:
            raise _Infrastructure("provider_error") from None

    @staticmethod
    def _evidence_range(snapshot, hypothesis, allowed_paths):
        if hypothesis.path not in allowed_paths:
            raise _Invalid("protected_path")
        lines = snapshot.sources[hypothesis.path].splitlines(keepends=True)
        if hypothesis.end_line > len(lines):
            raise _Invalid("evidence_range")
        cited = lines[hypothesis.start_line - 1:hypothesis.end_line]
        if not hypothesis.evidence.strip():
            raise _Invalid("evidence_content")
        # Model transport can change indentation. Match a contiguous sequence of
        # complete lines inside the existing range, ignoring only leading spaces
        # and tabs, then retain the actual source bytes. Tokens, trailing spaces,
        # comments and line endings are still exact; no range is inferred/widened.
        quoted = hypothesis.evidence.splitlines(keepends=True)
        normalized = "".join(line.lstrip(" \t") for line in quoted)
        for start in range(len(cited) - len(quoted) + 1):
            excerpt = "".join(cited[start:start + len(quoted)])
            # An excerpt may omit the final source line terminator, as before.
            if not hypothesis.evidence.endswith(("\r", "\n")):
                if excerpt.endswith("\r\n"):
                    excerpt = excerpt[:-2]
                elif excerpt.endswith(("\r", "\n")):
                    excerpt = excerpt[:-1]
            if (len(excerpt) <= 16000
                    and "".join(line.lstrip(" \t") for line in excerpt.splitlines(keepends=True)) == normalized):
                hypothesis.evidence = excerpt
                return
        # Preserve exact excerpts which intentionally quote part of a line.
        if hypothesis.evidence not in "".join(cited):
            raise _Invalid("evidence_content")

    @staticmethod
    def _workspace(snapshot, hypothesis, scratch_root, label):
        root = Path(tempfile.mkdtemp(prefix=f"review-repair-{label}-", dir=scratch_root))
        for path, code in snapshot.sources.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(code.encode("utf-8"))
        proof = root / ".decilist-proof" / hypothesis.reproduction.filename
        proof.parent.mkdir(exist_ok=True)
        proof.write_bytes(hypothesis.reproduction.code.encode("utf-8"))
        return root

    @staticmethod
    def _integrity(workspace, expected, hypothesis):
        for path, code in expected.items():
            target = workspace / path
            if (any(parent.is_symlink() for parent in (target, *target.parents) if parent != workspace.parent)
                    or not target.resolve().is_relative_to(workspace.resolve())
                    or not target.is_file() or target.read_bytes() != code.encode("utf-8")):
                raise _Infrastructure("workspace_mutated")
        proof = workspace / ".decilist-proof" / hypothesis.reproduction.filename
        if (proof.parent.is_symlink() or proof.is_symlink() or not proof.is_file()
                or proof.read_bytes() != hypothesis.reproduction.code.encode("utf-8")):
            raise _Infrastructure("reproduction_mutated")

    async def _execute(self, stage, workspace, expected, hypothesis, attempt):
        self._integrity(workspace, expected, hypothesis)
        started = perf_counter()
        try:
            async with asyncio.timeout(self.policy.host_timeout_ms / 1000):
                # Give the host a separate spec so mutation cannot alter the proof.
                call = (self.host.reproduce(workspace, _validated(ReproductionSpec, hypothesis.reproduction))
                        if "reproduction" in stage else self.host.regress(workspace))
                result = _validated(ExecutionResult, await call)
        except TimeoutError:
            raise _Infrastructure("host_timeout") from None
        except Exception:
            raise _Infrastructure("host_error") from None
        finally:
            attempt.timings_ms[stage] = (perf_counter() - started) * 1000
        self._integrity(workspace, expected, hypothesis)
        setattr(attempt, stage, result)
        return result

    def _apply(self, snapshot, hypothesis, edits, allowed_paths):
        if not isinstance(edits, list) or not 1 <= len(edits) <= self.policy.max_edits:
            raise _Invalid("edit_count")
        normalized = [_validated(LineEdit, edit) for edit in edits]
        total, previous = 0, {}
        for edit in sorted(normalized, key=lambda item: (item.path, item.start_line)):
            if edit.path not in allowed_paths or edit.path != hypothesis.path:
                raise _Invalid("protected_path")
            lines = snapshot.sources[edit.path].splitlines(keepends=True)
            if edit.source_sha256 != snapshot.source_hashes[edit.path]:
                raise _Invalid("stale_source")
            if (edit.end_line < edit.start_line or edit.end_line > len(lines)
                    or edit.start_line < hypothesis.start_line or edit.end_line > hypothesis.end_line):
                raise _Invalid("edit_range")
            if edit.start_line <= previous.get(edit.path, 0):
                raise _Invalid("overlapping_edits")
            previous[edit.path] = edit.end_line
            if "\0" in edit.replacement or _CAPABILITIES.search(edit.replacement):
                raise _Invalid("added_capability")
            total += max(edit.end_line - edit.start_line + 1, len(edit.replacement.splitlines()))
        if total > self.policy.max_changed_lines:
            raise _Invalid("changed_line_budget")
        candidate = dict(snapshot.sources)
        for edit in sorted(normalized, key=lambda item: (item.path, -item.start_line)):
            lines = candidate[edit.path].splitlines(keepends=True)
            replacement = edit.replacement
            last = lines[edit.end_line - 1]
            if replacement and not replacement.endswith(("\r", "\n")) and (edit.end_line < len(lines) or last.endswith(("\r", "\n"))):
                replacement += "\r\n" if last.endswith("\r\n") else "\n"
            lines[edit.start_line - 1:edit.end_line] = [replacement] if replacement else []
            candidate[edit.path] = "".join(lines)
        if candidate == dict(snapshot.sources):
            raise _Invalid("unchanged_candidate")
        return candidate, normalized

    async def _attempt(self, snapshot, hypothesis, attempt, scratch_root, allowed_paths):
        baseline = self._workspace(snapshot, hypothesis, scratch_root, "baseline")
        checks = await self._execute("baseline_regression", baseline, snapshot.sources, hypothesis, attempt)
        if checks.outcome != "pass" or checks.assertions < 1:
            attempt.status, attempt.reason = "infrastructure_failure", "baseline_checks_not_passing"
            return None
        proof = await self._execute("baseline_reproduction", baseline, snapshot.sources, hypothesis, attempt)
        if proof.outcome != "assertion_failure" or proof.assertions < 1:
            attempt.status = "unproven" if proof.outcome in {"pass", "assertion_failure"} else "infrastructure_failure"
            attempt.reason = "baseline_not_assertion_failure"
            return None
        started = perf_counter()
        # Separate objects keep provider mutation from changing recorded evidence.
        raw = await self._provider_call(self.provider.repair(snapshot, _validated(DefectHypothesis, hypothesis),
                                                             _validated(ExecutionResult, proof)))
        attempt.timings_ms["repair"] = (perf_counter() - started) * 1000
        expected, edits = self._apply(snapshot, hypothesis, raw, allowed_paths)
        attempt.edits = edits
        # Fresh candidate from original, never from a previous attempt or baseline.
        workspace = self._workspace(snapshot, hypothesis, scratch_root, "candidate")
        for path in {edit.path for edit in edits}:
            (workspace / path).write_bytes(expected[path].encode("utf-8"))
        fixed = await self._execute("candidate_reproduction", workspace, expected, hypothesis, attempt)
        if fixed.outcome != "pass" or fixed.assertions < 1:
            attempt.status = "unproven" if fixed.outcome in {"pass", "assertion_failure"} else "infrastructure_failure"
            attempt.reason = "candidate_reproduction_failed"
            return None
        checks = await self._execute("candidate_regression", workspace, expected, hypothesis, attempt)
        if checks.outcome != "pass" or checks.assertions < 1:
            attempt.status = "regression" if checks.outcome == "assertion_failure" else "infrastructure_failure"
            attempt.reason = "candidate_regression_failed"
            return None
        attempt.status, attempt.reason = "accepted_candidate", "proof_and_existing_checks_passed"
        return workspace
