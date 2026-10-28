"""Version 1 decision contract. Probabilities are estimates, not guarantees."""
from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Annotated, Literal, TypeAlias
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, field_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=65536)]
Label = Annotated[str, StringConstraints(min_length=1, max_length=256)]


def _json_key_order(keys: Iterable[str]) -> list[str]:
    """Match JS JSON object order: array indices first, then other keys in insertion order."""
    def order(key: str) -> tuple[int, int]:
        if key.isascii() and key.isdecimal() and (key == "0" or not key.startswith("0")):
            index = int(key)
            if index < 4294967295:
                return (0, index)
        return (1, 0)
    return sorted(keys, key=order)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class BaseQuestion(Contract):
    state: Text
    instructions: Text


class Choose(BaseQuestion):
    kind: Literal["choose"] = "choose"
    options: dict[Label, Text] = Field(min_length=2, max_length=64)

    @field_validator("options")
    @classmethod
    def exact_keys(cls, options: dict[str, str]) -> dict[str, str]:
        if any(not key.strip() or key != key.strip() for key in options):
            raise ValueError("option keys must not have surrounding whitespace")
        return {key: options[key] for key in _json_key_order(options)}


class Predict(BaseQuestion):
    kind: Literal["predict"] = "predict"


class Score(BaseQuestion):
    kind: Literal["score"] = "score"
    levels: list[Label] = Field(min_length=2, max_length=10)

    @field_validator("levels")
    @classmethod
    def unique_levels(cls, levels: list[str]) -> list[str]:
        if any(not level.strip() or level != level.strip() for level in levels):
            raise ValueError("levels must not have surrounding whitespace")
        if len(set(levels)) != len(levels):
            raise ValueError("score levels must be distinct")
        return levels


Question: TypeAlias = Annotated[Choose | Predict | Score, Field(discriminator="kind")]
question_adapter = TypeAdapter(Question)


class Policy(Contract):
    local_only: bool = True
    min_confidence: float = Field(default=0.7, ge=0, le=1)
    timeout_ms: int = Field(default=5000, ge=1, le=120000)


class DecisionRequest(Contract):
    question: Question
    policy: Policy = Field(default_factory=Policy)


class Attempt(Contract):
    provider: str
    outcome: Literal["accepted", "low_confidence", "invalid_output", "timeout", "error", "blocked"]
    latency_ms: float = Field(ge=0)


class Decision(Contract):
    schema_version: Literal["1"] = "1"
    request_id: str
    kind: Literal["choose", "predict", "score"]
    status: Literal["accepted", "abstained"]
    value: str | bool | float | None = None
    probabilities: dict[str, float] = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0, le=1)
    provider: str | None = None
    model: str | None = None
    latency_ms: float = Field(ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    attempts: list[Attempt]


def labels_for(question: Question) -> list[str]:
    if isinstance(question, Choose):
        # Also cover model_copy/model_construct, which intentionally bypass Pydantic validators.
        return _json_key_order(question.options)
    if isinstance(question, Predict):
        return ["false", "true"]
    return [str(i) for i in range(len(question.levels))]


def validate_probabilities(question: Question, raw: object) -> dict[str, float]:
    if not isinstance(raw, dict) or set(raw) != set(labels_for(question)):
        raise ValueError("provider must return exactly the requested labels")
    if any(type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1 for p in raw.values()):
        raise ValueError("probabilities must be finite numbers in [0,1]")
    if not math.isclose(sum(raw.values()), 1.0, abs_tol=1e-5):
        raise ValueError("probabilities must sum to one")
    return {label: float(raw[label]) for label in labels_for(question)}


def value_from(question: Question, probabilities: dict[str, float]) -> str | bool | float:
    if isinstance(question, Predict):
        return probabilities["true"] >= 0.5
    if isinstance(question, Score):
        return sum(int(k) * p for k, p in probabilities.items())
    return max(probabilities, key=probabilities.__getitem__)


def validate_decision(question: Question, result: Decision) -> Decision:
    if result.kind != question.kind:
        raise ValueError("response kind mismatch")
    if result.status == "abstained":
        if result.value is not None or result.confidence is not None or result.probabilities:
            raise ValueError("abstention must not include a decision")
        return result
    probabilities = validate_probabilities(question, result.probabilities)
    expected = value_from(question, probabilities)
    if type(expected) is float:
        valid_value = type(result.value) is float and math.isclose(expected, result.value, abs_tol=1e-5)
    else:
        valid_value = type(result.value) is type(expected) and result.value == expected
    if not valid_value or result.confidence is None or not math.isclose(result.confidence, max(probabilities.values()), abs_tol=1e-5):
        raise ValueError("response value/confidence does not match probabilities")
    if result.provider is None or result.model is None:
        raise ValueError("accepted decision must identify provider and model")
    return result
