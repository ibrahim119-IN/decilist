"""Choose by expected cost instead of by the largest probability."""
from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Literal

from .contracts import Decision
from .decision_log import DecisionLog


def _label(value: Any) -> str:
    """Use the decision contract's labels: predict answers are "true" and "false"."""
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is str and value:
        return value
    raise TypeError("cost labels must be nonempty strings or booleans")


def _cost(value: Any, what: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{what} must be a finite number of at least 0")
    return float(value)


class Costs:
    """What each answer costs when the truth turns out to be something else.

    ``wrong[answered][actual]`` is the cost of answering ``answered`` when
    ``actual`` was right. Pairs you leave out cost ``default`` when the answer
    is wrong and 0 when it is right. ``abstain`` is the cost of asking a person;
    ``None`` means the decision never abstains because of cost.
    """

    def __init__(self, wrong: Mapping[Any, Mapping[Any, float]] | None = None, *, abstain: float | None, default: float = 1.0) -> None:
        if wrong is not None and not isinstance(wrong, Mapping):
            raise TypeError("wrong must be a mapping of answered -> actual -> cost")
        table: dict[str, dict[str, float]] = {}
        for answered, row in (wrong or {}).items():
            if not isinstance(row, Mapping):
                raise TypeError("wrong must be a mapping of answered -> actual -> cost")
            table[_label(answered)] = {_label(actual): _cost(cost, "each cost") for actual, cost in row.items()}
        self.wrong = table
        self.default = _cost(default, "default")
        self.abstain = None if abstain is None else _cost(abstain, "abstain")

    @classmethod
    def from_dict(cls, data: Mapping) -> Costs:
        """Build from the JSON shape that ``to_dict`` returns."""
        if not isinstance(data, Mapping) or not set(data) <= {"wrong", "abstain", "default"} or "abstain" not in data:
            raise ValueError('costs must be an object with "abstain" and optional "wrong" and "default"')
        return cls(data.get("wrong"), abstain=data["abstain"], default=data.get("default", 1.0))

    def to_dict(self) -> dict:
        return {"wrong": {answered: dict(row) for answered, row in self.wrong.items()}, "default": self.default, "abstain": self.abstain}

    def labels(self) -> set[str]:
        return set(self.wrong) | {actual for row in self.wrong.values() for actual in row}

    def check_labels(self, labels: list[str]) -> None:
        """Reject a cost for a label the question does not have: a typo here is silent otherwise."""
        unknown = sorted(self.labels() - set(labels))
        if unknown:
            raise ValueError(f"costs name labels the question does not have: {', '.join(unknown)}")

    def of(self, answered: str, actual: str) -> float:
        """The cost of one answer given the truth."""
        row = self.wrong.get(answered, {})
        if actual in row:
            return row[actual]
        return 0.0 if answered == actual else self.default

    def expected(self, probabilities: Mapping[str, float]) -> dict[str, float]:
        """Expected cost of each answer under the given probabilities."""
        return {answered: sum(p * self.of(answered, actual) for actual, p in probabilities.items()) for answered in probabilities}

    def pick(self, probabilities: Mapping[str, float]) -> tuple[str | None, dict[str, float]]:
        """Return the cheapest answer, or None when asking a person is cheaper, with every expected cost."""
        expected = self.expected(probabilities)
        best = min(expected, key=lambda label: (expected[label], -probabilities[label]))
        if self.abstain is not None and expected[best] > self.abstain:
            return None, expected
        return best, expected

    def ranked(self, probabilities: Mapping[str, float]) -> list[str]:
        """Answers that are no costlier than asking a person, cheapest first."""
        expected = self.expected(probabilities)
        ordered = sorted(expected, key=lambda label: (expected[label], -probabilities[label]))
        return [label for label in ordered if self.abstain is None or expected[label] <= self.abstain]


@dataclass(frozen=True)
class CostedDecision:
    """The cheapest answer under your costs, or an abstention when asking a person is cheaper.

    ``decision`` is what the model returned. Its ``value`` is the most probable
    answer, which can differ from ``value`` here.
    """
    status: Literal["accepted", "abstained"]
    value: str | bool | None
    expected_cost: float | None
    expected_costs: dict[str, float]
    abstain_cost: float | None
    decision: Decision

    @property
    def request_id(self) -> str:
        return self.decision.request_id


def apply_costs(decision: Decision, costs: Costs) -> CostedDecision:
    """Apply costs to a choose or predict decision that carries probabilities."""
    if decision.kind == "score":
        raise ValueError("costs apply to choose and predict, not score")
    if decision.status != "accepted":
        return CostedDecision("abstained", None, None, {}, costs.abstain, decision)
    label, expected = costs.pick(decision.probabilities)
    if label is None:
        return CostedDecision("abstained", None, None, expected, costs.abstain, decision)
    value: str | bool = label == "true" if decision.kind == "predict" else label
    return CostedDecision("accepted", value, expected[label], expected, costs.abstain, decision)


def _expected_label(kind: str, expected: Any) -> str | None:
    if kind == "predict":
        return _label(expected) if type(expected) is bool else None
    return expected if type(expected) is str and expected else None


def _summary(picks: list[tuple[str | None, str]], costs: Costs) -> dict:
    abstain = costs.abstain or 0.0
    answered = [(pick, actual) for pick, actual in picks if pick is not None]
    total = sum(costs.of(pick, actual) for pick, actual in answered) + abstain * (len(picks) - len(answered))
    return {"answered": len(answered), "abstained": len(picks) - len(answered),
            "wrong": sum(pick != actual for pick, actual in answered),
            "total_cost": total, "mean_cost": total / len(picks) if picks else None}


def cost_report(path: str | Path, costs: Costs, *, name: str | None = None, model: str | None = None, min_confidence: float = 0.7) -> dict:
    """Replay labelled decisions from a log under two rules and compare what each would have cost.

    The cost rule picks the cheapest expected answer. The confidence rule picks
    the most probable answer when its probability reaches ``min_confidence``.
    Only decisions with stored probabilities and an ``expected`` outcome can be replayed.
    """
    if not isinstance(costs, Costs):
        raise TypeError("costs must be a Costs")
    if type(min_confidence) not in (int, float) or not 0 <= min_confidence <= 1:
        raise ValueError("min_confidence must be in [0, 1]")
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
    replayable: list[tuple[dict, str]] = []
    no_probabilities = no_expected = unknown_labels = 0
    for record in decisions:
        outcome = outcomes.get(record["request_id"])
        probabilities = record.get("probabilities")
        valid = (isinstance(probabilities, dict) and probabilities and
                 all(type(p) in (int, float) and math.isfinite(p) and 0 <= p <= 1 for p in probabilities.values()))
        actual = None if outcome is None else _expected_label(record["kind"], outcome.get("expected"))
        if actual is None:
            no_expected += outcome is not None
        elif not valid:
            no_probabilities += 1
        elif actual not in probabilities or not costs.labels() <= set(probabilities):
            unknown_labels += 1
        else:
            replayable.append((record, actual))
    cost_picks, confidence_picks = [], []
    mistakes: Counter[tuple[str, str]] = Counter()
    for record, actual in replayable:
        probabilities = record["probabilities"]
        pick, _ = costs.pick(probabilities)
        cost_picks.append((pick, actual))
        if pick is not None and pick != actual:
            mistakes[(pick, actual)] += 1
        top = max(probabilities, key=probabilities.__getitem__)
        confidence_picks.append((top if probabilities[top] >= min_confidence else None, actual))
    warnings = []
    if costs.abstain is None:
        warnings.append("No abstain cost was given, so abstentions are counted at cost 0 and the cost rule never abstains.")
    if len(replayable) < 30:
        warnings.append("Fewer than 30 decisions could be replayed.")
    if no_probabilities:
        warnings.append(f"{no_probabilities} labelled decisions have no probabilities and were left out. Decisions made with costs store them; plain calls need min_confidence=0.")
    if no_expected:
        warnings.append(f"{no_expected} outcomes give only correct, not the expected answer, and cannot be replayed.")
    if unknown_labels:
        warnings.append(f"{unknown_labels} decisions do not use the labels in these costs and were left out.")
    if len({r.get("instructions_sha256") for r, _ in replayable if r.get("instructions_sha256") is not None}) > 1:
        warnings.append("The replay mixes different questions. Use the decision name to report on one.")
    if len(replayable) < len(decisions):
        warnings.append("Only some decisions could be replayed; the result may be off if those are not a fair sample.")
    warnings.append("A replay describes past decisions. The cost rule is only as good as the model's probabilities.")
    return {"decisions": len(decisions), "replayed": len(replayable), "malformed_lines": log.malformed_lines,
            "min_confidence": min_confidence, "abstain_cost": costs.abstain,
            "cost_rule": _summary(cost_picks, costs), "confidence_rule": _summary(confidence_picks, costs),
            "always_ask": {"total_cost": (costs.abstain or 0.0) * len(replayable), "mean_cost": (costs.abstain or 0.0) if replayable else None},
            "costliest_mistakes": [{"answered": answered, "actual": actual, "count": count, "cost": costs.of(answered, actual) * count}
                                   for (answered, actual), count in sorted(mistakes.items(), key=lambda item: -costs.of(*item[0]) * item[1])[:10]],
            "warnings": warnings}


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="decilist cost-report", description="Replay labelled decisions under a cost rule and a confidence rule")
    parser.add_argument("path")
    parser.add_argument("--costs", required=True, help='JSON file: {"wrong": {answered: {actual: cost}}, "default": 1, "abstain": 2}')
    parser.add_argument("--decision", help="Only decisions defined with this name")
    parser.add_argument("--model")
    parser.add_argument("--min-confidence", type=float, default=0.7)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        costs = Costs.from_dict(json.loads(Path(args.costs).read_text(encoding="utf-8")))
        report = cost_report(args.path, costs, name=args.decision, model=args.model, min_confidence=args.min_confidence)
    except FileNotFoundError as exc:
        print(f"File not found: {exc.filename}")
        return 2
    except (OSError, ValueError, TypeError) as exc:
        print(f"Cannot report costs: {exc}")
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["replayed"] else 1
    if not report["replayed"]:
        print("No labelled decisions could be replayed.")
        for warning in report["warnings"]:
            print(f"Warning: {warning}")
        return 1
    print(f'Replayed {report["replayed"]} of {report["decisions"]} decisions')
    print("Rule                      Answered  Abstained  Wrong  Total cost  Mean cost")
    rows = [("Cost rule", report["cost_rule"]), (f'Confidence >= {report["min_confidence"]:.2f}', report["confidence_rule"])]
    for title, row in rows:
        print(f'{title:<24}  {row["answered"]:8d}  {row["abstained"]:9d}  {row["wrong"]:5d}  {row["total_cost"]:10.2f}  {row["mean_cost"]:9.3f}')
    print(f'Always ask a person: total cost {report["always_ask"]["total_cost"]:.2f}')
    for mistake in report["costliest_mistakes"]:
        print(f'Cost-rule mistake: answered {mistake["answered"]!r} when it was {mistake["actual"]!r}, {mistake["count"]} times, cost {mistake["cost"]:.2f}')
    for warning in report["warnings"]:
        print(f"Warning: {warning}")
    return 0
