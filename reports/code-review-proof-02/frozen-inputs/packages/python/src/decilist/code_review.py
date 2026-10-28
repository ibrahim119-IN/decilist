"""Optional, advisory code review with a pinned specialist. No imports load weights."""
from __future__ import annotations

import asyncio
import json
from importlib.resources import files
from time import perf_counter
from typing import Annotated, Literal, Protocol

from pydantic import Field, StringConstraints, field_validator, model_validator

from .contracts import Contract
from .providers import LiquidLocalProvider

REVIEW_MODEL = "yannelli/d1-3b-code-review-decisions"
REVIEW_REVISION = "047a67d224c6d90767dddea3712ee8af434eaff3"
DATASET_REVISION = "5c3f3923f4a4278e249731ac3d3b613861efe3e4"
REVIEW_FILES = ("api.py", "chat_template.jinja", "config.json", "generation_config.json", "hybrid.py",
                "lfm2_vl.py", "LICENSE", "modeling_d1.py", "processor_config.json", "prompt.py", "README.md",
                "runner.py", "temperatures.json", "tokenizer_config.json", "tokenizer.json", "model.safetensors")
Rule = Literal["unclear_naming", "noise_comments", "dead_code", "inconsistent_style",
               "over_abstracted", "too_clever", "duplicated_logic", "error_handling_problem",
               "type_safety_hole", "unsafe_input"]
DEFAULT_RULES: tuple[Rule, ...] = ("error_handling_problem", "type_safety_hole", "unsafe_input")
Language = Literal["JavaScript", "JSX", "TypeScript", "TSX"]
Code = Annotated[str, StringConstraints(min_length=1, max_length=65536)]
Task = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class ReviewPolicy(Contract):
    rules: list[Rule] = Field(default_factory=lambda: list(DEFAULT_RULES), min_length=1, max_length=10)
    threshold: float = Field(default=0.9, ge=0.5, le=1)
    timeout_ms: int = Field(default=30000, ge=1, le=120000)

    @field_validator("rules")
    @classmethod
    def distinct(cls, value: list[Rule]) -> list[Rule]:
        if len(set(value)) != len(value):
            raise ValueError("review rules must be distinct")
        return value


class ReviewRequest(Contract):
    code: Code
    task: Task
    language: Language = "TypeScript"
    policy: ReviewPolicy = Field(default_factory=ReviewPolicy)

    @field_validator("code")
    @classmethod
    def bounded(cls, value: str) -> str:
        if not value.strip() or "\0" in value or len(value.splitlines()) > 2000:
            raise ValueError("provide nonempty text without NUL, at most 2000 lines")
        return value


class ReviewWindow(Contract):
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    probabilities: dict[Rule, Annotated[float, Field(ge=0, le=1)]]


class ReviewFinding(Contract):
    rule: Rule
    probability: float = Field(ge=0, le=1)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class ReviewResult(Contract):
    schema_version: Literal["code-review.1"] = "code-review.1"
    status: Literal["flagged", "no_flags", "unavailable"]
    advisory: Literal[True] = True
    model: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    rules: list[Rule] = Field(min_length=1, max_length=10)
    threshold: float = Field(ge=0.5, le=1)
    total_lines: int = Field(ge=1, le=2000)
    reviewed_lines: int = Field(ge=0, le=2000)
    windows: list[ReviewWindow]
    findings: list[ReviewFinding]
    latency_ms: float = Field(ge=0)
    error: Literal["timeout", "invalid_output", "provider_error"] | None = None

    @model_validator(mode="after")
    def consistent(self):
        if len(set(self.rules)) != len(self.rules):
            raise ValueError("duplicate result rules")
        covered = set()
        expected = []
        for index, window in enumerate(self.windows):
            start = index * 68 + 1
            end = min(start + 79, self.total_lines)
            if len(covered) == self.total_lines or start > self.total_lines or (window.start_line, window.end_line) != (start, end):
                raise ValueError("invalid review coverage")
            if set(window.probabilities) != set(self.rules):
                raise ValueError("result rules mismatch")
            covered.update(range(start, end + 1))
            for rule in self.rules:
                probability = window.probabilities[rule]
                if probability >= self.threshold:
                    expected.append(ReviewFinding(rule=rule, probability=probability, start_line=start, end_line=end))
        if self.findings != expected or len(covered) != self.reviewed_lines:
            raise ValueError("findings or coverage do not match probabilities")
        status = "unavailable" if self.error else "flagged" if self.findings else "no_flags"
        if self.status != status or (not self.error and self.reviewed_lines != self.total_lines):
            raise ValueError("incomplete or inconsistent review status")
        return self


class ReviewProvider(Protocol):
    model: str
    revision: str
    locality: str

    async def review(self, state: dict, questions: dict) -> dict: ...


class LiquidCodeReviewProvider(LiquidLocalProvider):
    """Same pinned Liquid runtime, separately licensed specialist weights."""
    name = "liquid-code-review"
    model = REVIEW_MODEL
    revision = REVIEW_REVISION
    snapshot_files = REVIEW_FILES

    async def review(self, state: dict, questions: dict) -> dict:
        def run():
            import torch
            with torch.inference_mode():
                return self._model.system_one(state, questions)
        return await self._run_locked(run)


def review_questions(rules: list[Rule]) -> dict:
    # Unchanged question definitions from Ryan Yannelli's CC BY 4.0 dataset.
    questions = json.loads(files("decilist").joinpath("data/code_review_questions.json").read_text(encoding="utf-8"))
    return {name: questions[name] for name in rules}


class InvalidReviewOutput(ValueError):
    pass


def _decode(raw: object, rules: list[Rule]) -> dict[Rule, float]:
    import math
    if not isinstance(raw, dict) or not isinstance(raw.get("answers"), dict) or set(raw["answers"]) != set(rules):
        raise InvalidReviewOutput("review answer names do not match the requested rules")
    scores = {}
    for name in rules:
        answer = raw["answers"][name]
        if not isinstance(answer, dict) or answer.get("type") != "noul":
            raise InvalidReviewOutput("expected a noul answer")
        probability = answer.get("noul")
        if type(probability) not in (float, int) or not math.isfinite(probability) or not 0 <= probability <= 1:
            raise InvalidReviewOutput("invalid review probability")
        scores[name] = float(probability)
    return scores


class CodeReviewer:
    """Load once, then review code already available to an editor, app, or CI job.

    Findings identify an 80-line context window, not a localized proven defect.
    No finding is not an approval. The caller retains its tests and review policy.
    """

    def __init__(self, provider: ReviewProvider, policy: ReviewPolicy | None = None):
        if provider.locality != "local":
            raise ValueError("code review requires a local provider")
        self.provider = provider
        self.policy = ReviewPolicy.model_validate((policy or ReviewPolicy()).model_dump())

    @classmethod
    def load(cls, *, device: str = "cuda", allow_model_code: bool = False, policy: ReviewPolicy | None = None):
        # Setup/download is a separate, explicit command. No network at review time.
        return cls(LiquidCodeReviewProvider.load(device=device, allow_model_code=allow_model_code, offline=True), policy)

    async def review(self, code: str, *, task: str, language: Language = "TypeScript",
                     policy: ReviewPolicy | None = None) -> ReviewResult:
        request = ReviewRequest(code=code, task=task, language=language, policy=policy or self.policy)
        request = ReviewRequest.model_validate(request.model_dump())
        started = perf_counter()
        lines = request.code.splitlines()
        windows: list[ReviewWindow] = []
        findings: list[ReviewFinding] = []
        covered: set[int] = set()
        error = None
        questions = review_questions(request.policy.rules)
        try:
            async with asyncio.timeout(request.policy.timeout_ms / 1000):
                # Twelve lines overlap; long files are fully covered, never silently truncated.
                for start in range(0, len(lines), 68):
                    end = min(start + 80, len(lines))
                    state = {"task": request.task, "language": request.language, "code": "\n".join(lines[start:end])}
                    raw = await self.provider.review(state, questions)
                    scores = _decode(raw, request.policy.rules)
                    windows.append(ReviewWindow(start_line=start + 1, end_line=end, probabilities=scores))
                    covered.update(range(start, end))
                    for rule, probability in scores.items():
                        if probability >= request.policy.threshold:
                            findings.append(ReviewFinding(rule=rule, probability=probability, start_line=start + 1, end_line=end))
                    if end == len(lines):
                        break
        except TimeoutError:
            error = "timeout"
        except InvalidReviewOutput:
            error = "invalid_output"
        except Exception:
            # Exceptions may contain the user's code. Keep it out of logs and responses.
            error = "provider_error"
        return ReviewResult(status="unavailable" if error else "flagged" if findings else "no_flags",
                            model=self.provider.model, revision=self.provider.revision,
                            rules=request.policy.rules, threshold=request.policy.threshold,
                            total_lines=len(lines), reviewed_lines=len(covered), windows=windows,
                            findings=findings, latency_ms=(perf_counter() - started) * 1000, error=error)
