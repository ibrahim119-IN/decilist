"""Reproducible small-sample evaluation, with coverage separate from accuracy."""
from __future__ import annotations

import math
from dataclasses import dataclass
from .contracts import Decision, Question, Score, labels_for, question_adapter, validate_decision
from .engine import Decilist


@dataclass(frozen=True)
class Case:
    id: str
    question: Question
    expected: str | bool | float


def validate_cases(cases: list[Case]) -> None:
    """Reject mislabeled or malformed evaluation data before any provider runs."""
    if not cases or any(not isinstance(c.id, str) or not c.id.strip() for c in cases):
        raise ValueError("evaluation needs nonempty case IDs and cases")
    if len({c.id for c in cases}) != len(cases):
        raise ValueError("case IDs must be unique")
    for case in cases:
        question_adapter.validate_python(case.question.model_dump())
        if isinstance(case.question, Score):
            if (type(case.expected) not in (int, float) or not math.isfinite(case.expected)
                    or not 0 <= case.expected <= len(case.question.levels) - 1):
                raise ValueError("score labels must be finite numbers in the rubric range")
        elif case.question.kind == "predict":
            if type(case.expected) is not bool:
                raise ValueError("predicate labels must be booleans")
        elif type(case.expected) is not str or case.expected not in labels_for(case.question):
            raise ValueError("expected label is not in choices")


def summarize(cases: list[Case], results: list[Decision]) -> dict:
    if not cases or len(cases) != len(results):
        raise ValueError("evaluation needs nonempty, equally sized cases and results")
    validate_cases(cases)
    accepted = correct = discrete_cases = discrete_accepted = score_cases = 0
    score_errors = []
    errors = []
    for case, result in zip(cases, results):
        result = Decision.model_validate(result.model_dump())
        validate_decision(case.question, result)
        numeric = isinstance(case.question, Score)
        score_cases += numeric
        discrete_cases += not numeric
        if result.status == "accepted":
            accepted += 1
            if numeric:
                score_errors.append(abs(result.value - case.expected))
            else:
                discrete_accepted += 1
                correct += result.value == case.expected
        matches = False
        if result.status == "accepted":
            matches = math.isclose(result.value, case.expected, abs_tol=1e-5) if numeric else result.value == case.expected
        if not matches:
            errors.append({"id": case.id, "kind": case.question.kind, "expected": case.expected,
                           "value": result.value, "status": result.status,
                           "absolute_error": abs(result.value-case.expected) if numeric and result.status == "accepted" else None})
    latency = sorted(result.latency_ms for result in results)
    n = len(cases)
    attempts = [attempt for result in results for attempt in result.attempts]
    attempted = sum(a.outcome != "blocked" for a in attempts)
    valid_outputs = sum(a.outcome in ("accepted", "low_confidence") for a in attempts)
    fallbacks = sum(len(r.attempts) > 1 for r in results)
    return {
        "cases": n, "accepted": accepted, "coverage": accepted/n,
        "classification_cases": discrete_cases, "classification_accepted": discrete_accepted,
        "accuracy_all": correct/discrete_cases if discrete_cases else None,
        "accuracy_accepted": correct/discrete_accepted if discrete_accepted else None,
        "score_cases": score_cases, "score_accepted": len(score_errors),
        "score_coverage": len(score_errors)/score_cases if score_cases else None,
        "score_mae": sum(score_errors)/len(score_errors) if score_errors else None,
        "abstention_rate": (n-accepted)/n,
        "p50_ms": latency[math.ceil(n*0.50)-1], "p95_ms": latency[math.ceil(n*0.95)-1],
        "provider_attempts": attempted, "valid_output_attempts": valid_outputs,
        "valid_output_rate": valid_outputs/attempted if attempted else None,
        "invalid_output_attempts": sum(a.outcome == "invalid_output" for a in attempts),
        "timeout_attempts": sum(a.outcome == "timeout" for a in attempts),
        "error_attempts": sum(a.outcome == "error" for a in attempts),
        "blocked_attempts": sum(a.outcome == "blocked" for a in attempts),
        "low_confidence_attempts": sum(a.outcome == "low_confidence" for a in attempts),
        "fallback_cases": fallbacks, "fallback_rate": fallbacks/n,
        "fallback_successes": sum(r.status == "accepted" and len(r.attempts) > 1 for r in results),
        "cost_usd": None, "errors": errors,
    }


async def evaluate(engine: Decilist, cases: list[Case]) -> dict:
    validate_cases(cases)
    results = [await engine.decide(case.question) for case in cases]
    return {"metrics": summarize(cases, results), "results": [r.model_dump() for r in results]}
