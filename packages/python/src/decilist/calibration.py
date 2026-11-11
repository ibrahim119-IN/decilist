"""Correct a model's probabilities from recorded outcomes, so a stated 0.9 is right about nine times in ten."""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

from .contracts import Decision
from .decision_log import DecisionLog, _table

_FLOOR = 1e-6
_MIN_TEMPERATURE, _MAX_TEMPERATURE = 0.25, 10.0


def _scale(probabilities: Mapping[str, float], power: float) -> dict[str, float]:
    """Raise each probability to a power and renormalise. Zero stays zero."""
    logs = {label: power * math.log(p) for label, p in probabilities.items() if p > 0}
    top = max(logs.values())
    weights = {label: math.exp(value - top) for label, value in logs.items()}
    total = sum(weights.values())
    return {label: weights.get(label, 0.0) / total for label in probabilities}


@dataclass(frozen=True)
class Calibration:
    """A fitted correction for one question and one model.

    ``temperature`` above 1 makes the model less sure of itself and below 1 more
    sure. It never changes which answer is most probable. ``min_confidence`` is
    the threshold chosen for ``target_accuracy`` on the corrected probabilities.
    """
    temperature: float
    kind: str
    labels: tuple[str, ...]
    model: str | None = None
    name: str | None = None
    min_confidence: float | None = None
    target_accuracy: float | None = None
    fitted_on: int = 0
    created: str | None = None

    def __post_init__(self) -> None:
        if type(self.temperature) not in (int, float) or not math.isfinite(self.temperature) or self.temperature <= 0:
            raise ValueError("temperature must be a finite number above 0")
        if self.kind not in ("choose", "predict"):
            raise ValueError("calibration applies to choose and predict, not score")
        labels = tuple(self.labels)
        if len(labels) < 2 or len(set(labels)) != len(labels) or any(type(label) is not str or not label for label in labels):
            raise ValueError("labels must be at least two distinct nonempty strings")
        object.__setattr__(self, "labels", tuple(sorted(labels)))
        if self.min_confidence is not None and (type(self.min_confidence) not in (int, float) or not 0 <= self.min_confidence <= 1):
            raise ValueError("min_confidence must be in [0, 1]")

    def applies_to(self, model: str | None) -> bool:
        """A calibration fitted on one model is not applied to another."""
        return self.model is None or self.model == model

    def check_labels(self, labels: list[str]) -> None:
        if tuple(sorted(labels)) != self.labels:
            raise ValueError("this calibration was fitted for different answers than the question has")

    def apply(self, probabilities: Mapping[str, float]) -> dict[str, float]:
        """Return corrected probabilities for the same labels."""
        self.check_labels(list(probabilities))
        return dict(probabilities) if self.temperature == 1 else _scale(probabilities, 1 / self.temperature)

    def to_dict(self) -> dict:
        return {"calibration_version": 1, "method": "temperature", "temperature": self.temperature, "kind": self.kind,
                "labels": list(self.labels), "model": self.model, "name": self.name, "min_confidence": self.min_confidence,
                "target_accuracy": self.target_accuracy, "fitted_on": self.fitted_on, "created": self.created}

    @classmethod
    def from_dict(cls, data: Mapping) -> Calibration:
        if not isinstance(data, Mapping) or data.get("calibration_version") != 1 or data.get("method") != "temperature":
            raise ValueError("not a version 1 temperature calibration")
        try:
            return cls(temperature=data["temperature"], kind=data["kind"], labels=tuple(data["labels"]), model=data.get("model"),
                       name=data.get("name"), min_confidence=data.get("min_confidence"), target_accuracy=data.get("target_accuracy"),
                       fitted_on=data.get("fitted_on", 0), created=data.get("created"))
        except (KeyError, TypeError) as exc:
            raise ValueError(f"calibration is missing or has a malformed field: {exc}") from exc

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    @classmethod
    def load(cls, path: str | Path) -> Calibration:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _check_question(calibration: Calibration, kind: str, labels: list[str]) -> None:
    """Refuse a calibration fitted for another kind of question or other answers, before any model runs."""
    if not isinstance(calibration, Calibration):
        raise TypeError("calibration must be a Calibration")
    if kind == "score":
        raise ValueError("calibration applies to choose and predict, not score")
    if calibration.kind != kind:
        raise ValueError(f"this calibration was fitted for a {calibration.kind} question, not {kind}")
    calibration.check_labels(labels)


def calibrate_decision(decision: Decision, calibration: Calibration, *, min_confidence: float = 0.0) -> Decision:
    """Correct an accepted choose or predict decision, then apply a threshold to the corrected confidence.

    A decision from another model than the calibration's is not corrected, and the
    threshold is applied to it as it is. Ask with ``min_confidence=0`` first, so
    that this function sees every answer.
    """
    if decision.status != "accepted":
        return decision
    if decision.kind == "score":
        raise ValueError("calibration applies to choose and predict, not score")
    probabilities, confidence = decision.probabilities, decision.confidence
    if calibration.applies_to(decision.model):
        probabilities = calibration.apply(probabilities)
        confidence = max(probabilities.values())
    if confidence < min_confidence:
        attempts = [a.model_copy(update={"outcome": "low_confidence"}) if a.outcome == "accepted" else a for a in decision.attempts]
        return decision.model_copy(update={"status": "abstained", "value": None, "probabilities": {}, "confidence": None,
                                           "provider": None, "model": None, "attempts": attempts})
    return decision.model_copy(update={"probabilities": probabilities, "confidence": confidence})


def _metrics(examples: list[tuple[dict[str, float], str]]) -> dict:
    n = len(examples)
    log_loss = brier = confidence_sum = 0.0
    correct = 0
    bins: list[list[float]] = [[0, 0.0, 0] for _ in range(10)]
    for probabilities, actual in examples:
        log_loss -= math.log(max(probabilities[actual], _FLOOR))
        brier += sum((p - (label == actual)) ** 2 for label, p in probabilities.items())
        top = max(probabilities, key=probabilities.__getitem__)
        confidence = probabilities[top]
        confidence_sum += confidence
        correct += top == actual
        bucket = bins[min(int(confidence * 10), 9)]
        bucket[0] += 1
        bucket[1] += confidence
        bucket[2] += top == actual
    ece = sum(abs(total - right) for count, total, right in bins if count) / n
    return {"mean_confidence": confidence_sum / n, "accuracy": correct / n, "log_loss": log_loss / n, "brier": brier / n, "ece": ece}


def _fit_power(examples: list[tuple[dict[str, float], str]]) -> float:
    """The power 1/temperature with the lowest log loss. The loss is convex in the power, so a section search finds it."""
    logs = []
    for probabilities, actual in examples:
        floored = {label: max(p, _FLOOR) for label, p in probabilities.items()}
        total = sum(floored.values())
        logs.append(({label: math.log(p / total) for label, p in floored.items()}, actual))

    def loss(power: float) -> float:
        result = 0.0
        for values, actual in logs:
            top = max(values.values()) * power if power >= 0 else 0.0
            result += top + math.log(sum(math.exp(power * v - top) for v in values.values())) - power * values[actual]
        return result

    low, high = 1 / _MAX_TEMPERATURE, 1 / _MIN_TEMPERATURE
    ratio = (math.sqrt(5) - 1) / 2
    a, b = high - ratio * (high - low), low + ratio * (high - low)
    loss_a, loss_b = loss(a), loss(b)
    for _ in range(80):
        if loss_a <= loss_b:
            high, b, loss_b = b, a, loss_a
            a = high - ratio * (high - low)
            loss_a = loss(a)
        else:
            low, a, loss_a = a, b, loss_b
            b = low + ratio * (high - low)
            loss_b = loss(b)
    return (low + high) / 2


@dataclass(frozen=True)
class CalibrationFit:
    """The result of ``fit_calibration``. ``calibration`` is None when there was too little to fit on."""
    calibration: Calibration | None
    report: dict


def fit_calibration(path: str | Path, *, name: str | None = None, model: str | None = None, target_accuracy: float | None = None,
                    min_samples: int = 30, folds: int = 5) -> CalibrationFit:
    """Fit a temperature from a decision log's labelled choose or predict decisions.

    The before and after figures are cross-validated: each decision is scored with a
    temperature fitted on the other folds. If that does not lower the log loss, the
    returned calibration keeps temperature 1 and leaves the probabilities alone.
    """
    if type(min_samples) is not int or min_samples < 2:
        raise ValueError("min_samples must be an integer of at least 2")
    if type(folds) is not int or folds < 2:
        raise ValueError("folds must be an integer of at least 2")
    if target_accuracy is not None and (type(target_accuracy) not in (int, float) or not 0 <= target_accuracy <= 1):
        raise ValueError("target_accuracy must be in [0, 1]")
    log = DecisionLog(path)
    decisions, outcomes = [], {}
    for record in log.read():
        request_id = record.get("request_id")
        if not isinstance(request_id, str) or not request_id.strip():
            continue
        if record.get("type") == "decision" and record.get("kind") in ("choose", "predict"):
            if (name is None or record.get("name") == name) and (model is None or record.get("model") == model):
                decisions.append(record)
        elif record.get("type") == "outcome":
            outcomes[request_id] = record
    models = sorted({r["model"] for r in decisions if isinstance(r.get("model"), str)})
    if model is None and len(models) > 1:
        raise ValueError(f"the log holds several models ({', '.join(models)}); a calibration is for one, so pass model")
    kinds = {r["kind"] for r in decisions}
    if len(kinds) > 1:
        raise ValueError("the log mixes choose and predict decisions; pass the decision name to fit one question")

    usable: list[tuple[dict, dict[str, float], str]] = []
    no_probabilities = no_label = 0
    for record in decisions:
        outcome = outcomes.get(record["request_id"])
        if outcome is None:
            continue
        probabilities = record.get("probabilities")
        if not (isinstance(probabilities, dict) and len(probabilities) >= 2 and
                all(type(p) in (int, float) and math.isfinite(p) and 0 <= p <= 1 for p in probabilities.values()) and sum(probabilities.values()) > 0):
            no_probabilities += 1
            continue
        expected = outcome.get("expected")
        if record["kind"] == "predict" and type(expected) is bool:
            actual: Any = "true" if expected else "false"
        elif record["kind"] == "choose" and type(expected) is str:
            actual = expected
        elif outcome.get("correct") is True and record.get("value") is not None:
            actual = ("true" if record["value"] else "false") if record["kind"] == "predict" else record["value"]
        elif outcome.get("correct") is False and record["kind"] == "predict" and type(record.get("value")) is bool:
            actual = "false" if record["value"] else "true"
        else:
            actual = None
        if actual not in probabilities:
            no_label += 1
            continue
        # A decision that was already calibrated stores its temperature, so the model's own probabilities can be recovered.
        applied = record.get("temperature")
        raw = _scale(probabilities, applied) if type(applied) in (int, float) and math.isfinite(applied) and applied > 0 and applied != 1 else dict(probabilities)
        usable.append((record, raw, actual))
    label_sets = {tuple(sorted(raw)) for _, raw, _ in usable}
    if len(label_sets) > 1:
        raise ValueError("the labelled decisions do not share one set of answers; pass the decision name to fit one question")

    warnings = []
    report: dict = {"name": name, "model": model or (models[0] if models else None), "decisions": len(decisions), "usable": len(usable),
                    "malformed_lines": log.malformed_lines, "temperature": None, "temperature_fitted": None, "helps": None,
                    "before": None, "after": None, "threshold": None, "warnings": warnings}
    if no_probabilities:
        warnings.append(f"{no_probabilities} labelled decisions have no probabilities and were left out. Abstentions do not store them; log with min_confidence=0 to keep every answer.")
    if no_label:
        warnings.append(f"{no_label} outcomes do not give the answer that was right and were left out. Record expected rather than correct.")
    if len(usable) < min_samples:
        warnings.append(f"Only {len(usable)} labelled decisions can be used; at least {min_samples} are needed.")
        return CalibrationFit(None, report)

    examples = [(raw, actual) for _, raw, actual in usable]
    k = min(folds, len(examples))
    # Spread decisions over the folds without a period, so a log that alternates between cases does not line up with them.
    fold_of = [int((index * 0.6180339887498949) % 1 * k) for index in range(len(examples))]
    held_out: list[tuple[dict[str, float], str]] = []
    for fold in range(k):
        training = [example for index, example in enumerate(examples) if fold_of[index] != fold]
        power = _fit_power(training) if training else 1.0
        held_out.extend((_scale(raw, power), actual) for index, (raw, actual) in enumerate(examples) if fold_of[index] == fold)
    before, after = _metrics(examples), _metrics(held_out)
    fitted = 1 / _fit_power(examples)
    helps = after["log_loss"] < before["log_loss"] - 1e-9
    temperature = fitted if helps else 1.0
    report.update(temperature=temperature, temperature_fitted=fitted, helps=helps, folds=k, before=before, after=after)

    if not helps:
        warnings.append("Calibration did not lower the cross-validated log loss, so the temperature is left at 1 and the probabilities are unchanged.")
    elif fitted < 1:
        warnings.append("The fitted temperature is below 1, which makes the model more sure of itself. Check that the labelled decisions are a fair sample.")
    if fitted <= _MIN_TEMPERATURE * 1.001 or fitted >= _MAX_TEMPERATURE * 0.999:
        warnings.append("The fitted temperature is at the edge of its allowed range; the probabilities may need more than one number to correct.")
    if len(usable) < 100:
        warnings.append("Fewer than 100 labelled decisions were used; treat the temperature as a rough estimate.")
    if len(usable) < len(decisions):
        warnings.append("Only some decisions could be used; the fit may be off if those are not a fair sample.")
    if any(r.get("min_confidence", 0) > 0 for r, _, _ in usable):
        warnings.append("Some decisions were logged with a threshold above 0, so less confident answers are missing from the fit.")
    if len({r.get("instructions_sha256") for r, _, _ in usable if r.get("instructions_sha256") is not None}) > 1:
        warnings.append("The fit mixes different questions. Use the decision name to fit one.")

    calibrated = [{**record, "probabilities": (scaled := _scale(raw, 1 / temperature)), "confidence": max(scaled.values()), "min_confidence": 0}
                  for record, raw, _ in usable]
    labelled = {record["request_id"]: {"expected": (actual == "true") if record["kind"] == "predict" else actual} for record, _, actual in usable}
    min_confidence = None
    if target_accuracy is not None:
        table = _table(calibrated, labelled, thresholds=None, target_accuracy=target_accuracy, min_samples=min_samples,
                       skipped_score=0, orphan_outcomes=0, malformed_lines=0, model=model)
        min_confidence = table["recommended"]
        report["threshold"] = {"target_accuracy": target_accuracy, "min_confidence": min_confidence, "reason": table["recommended_reason"], "rows": table["rows"]}
        if min_confidence is not None:
            warnings.append("The threshold was chosen on the same decisions the temperature was fitted on, so its accuracy is somewhat optimistic.")
    warnings.append("A calibration describes past decisions. Fit it again when the model, the question or the data changes.")
    labels = next(iter(label_sets))
    calibration = Calibration(temperature=temperature, kind=usable[0][0]["kind"], labels=labels, model=report["model"], name=name,
                              min_confidence=min_confidence, target_accuracy=target_accuracy, fitted_on=len(usable),
                              created=datetime.now(timezone.utc).isoformat())
    return CalibrationFit(calibration, report)


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="decilist fit-calibration", description="Fit a probability calibration from labelled decisions in a local log")
    parser.add_argument("path")
    parser.add_argument("--decision", help="Only decisions defined with this name")
    parser.add_argument("--model")
    parser.add_argument("--target-accuracy", type=float, help="Also choose a threshold for this accuracy on the corrected probabilities")
    parser.add_argument("--min-samples", type=int, default=30)
    parser.add_argument("--output", help="Write the calibration to this JSON file")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        fit = fit_calibration(args.path, name=args.decision, model=args.model, target_accuracy=args.target_accuracy, min_samples=args.min_samples)
        if fit.calibration is not None and args.output:
            fit.calibration.save(args.output)
    except FileNotFoundError:
        print(f"Log file not found: {args.path}")
        return 2
    except (OSError, ValueError) as exc:
        print(f"Cannot fit a calibration: {exc}")
        return 2
    report = fit.report
    if args.json:
        print(json.dumps({**report, "calibration": None if fit.calibration is None else fit.calibration.to_dict()}, ensure_ascii=False, indent=2))
        return 0 if fit.calibration is not None else 1
    if fit.calibration is None:
        print("No calibration was fitted.")
        for warning in report["warnings"]:
            print(f"Warning: {warning}")
        return 1
    print(f'Fitted on {report["usable"]} of {report["decisions"]} decisions, model {report["model"]}')
    direction = "left at 1: no change" if not report["helps"] else "above 1: the model was too sure" if report["temperature"] > 1 else "below 1: the model was too cautious"
    print(f'Temperature: {report["temperature"]:.3f} ({direction})')
    print("                 Mean confidence  Accuracy  Log loss   Brier     ECE")
    for title, row in (("Before", report["before"]), (f'After ({report["folds"]}-fold)', report["after"])):
        print(f'{title:<16} {row["mean_confidence"]:15.3f}  {row["accuracy"]:8.3f}  {row["log_loss"]:8.3f}  {row["brier"]:6.3f}  {row["ece"]:6.3f}')
    if report["threshold"] is not None:
        chosen = report["threshold"]["min_confidence"]
        print(f'Threshold for {report["threshold"]["target_accuracy"]:.2f} accuracy: {"none" if chosen is None else f"{chosen:.2f}"} - {report["threshold"]["reason"]}')
    if args.output:
        print(f"Wrote {args.output}")
    for warning in report["warnings"]:
        print(f"Warning: {warning}")
    return 0
