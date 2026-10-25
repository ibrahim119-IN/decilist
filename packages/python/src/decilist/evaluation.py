"""Reproducible small-sample evaluation, with coverage separate from accuracy."""
from __future__ import annotations

import math
from dataclasses import dataclass
from .contracts import Decision, Question, Score, labels_for
from .engine import Decilist


@dataclass(frozen=True)
class Case:
    id: str
    question: Question
    expected: str | bool | float


def summarize(cases: list[Case], results: list[Decision]) -> dict:
    if not cases or len(cases) != len(results):
        raise ValueError("evaluation needs nonempty, equally sized cases and results")
    accepted = 0
    correct = 0
    errors = []
    for case, result in zip(cases, results):
        if isinstance(case.question, Score):
            raise ValueError("classification report does not support score; use a numeric error metric")
        if case.question.kind == "predict":
            if type(case.expected) is not bool:
                raise ValueError("predicate labels must be booleans")
        elif case.expected not in labels_for(case.question):
            raise ValueError("expected label is not in choices")
        if result.kind != case.question.kind:
            raise ValueError("question/result kind mismatch")
        if result.status == "accepted":
            accepted += 1
            correct += result.value == case.expected
        if result.status != "accepted" or result.value != case.expected:
            errors.append({"id": case.id, "expected": case.expected, "value": result.value, "status": result.status})
    latency = sorted(result.latency_ms for result in results)
    n = len(cases)
    return {
        "cases": n, "accepted": accepted, "coverage": accepted/n,
        "accuracy_all": correct/n,
        "accuracy_accepted": correct/accepted if accepted else None,
        "abstention_rate": (n-accepted)/n,
        "p50_ms": latency[math.ceil(n*0.50)-1], "p95_ms": latency[math.ceil(n*0.95)-1],
        "invalid_output_attempts": sum(a.outcome == "invalid_output" for r in results for a in r.attempts),
        "timeout_attempts": sum(a.outcome == "timeout" for r in results for a in r.attempts),
        "cost_usd": None, "errors": errors,
    }


async def evaluate(engine: Decilist, cases: list[Case]) -> dict:
    if not cases or len({c.id for c in cases}) != len(cases):
        raise ValueError("case IDs must be unique and nonempty")
    results = [await engine.decide(case.question) for case in cases]
    return {"metrics": summarize(cases, results), "results": [r.model_dump() for r in results]}
