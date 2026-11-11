"""Local decision records and calibration from observed outcomes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
from pathlib import Path
import threading
from typing import TYPE_CHECKING, Iterable, Iterator

from .contracts import Decision, Policy, Question, validate_decision

if TYPE_CHECKING:
    from .costs import CostedDecision
    from .evaluation import Case


def _wilson_lower(correct: int, n: int) -> float | None:
    if not n:
        return None
    rate = correct / n
    z = 1.96
    return (rate + z*z/(2*n) - z*math.sqrt(rate*(1-rate)/n + z*z/(4*n*n))) / (1 + z*z/n)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class DecisionLog:
    """Append JSONL records locally; use one process per file. No rotation is provided."""

    def __init__(self, path: str | Path, *, include_state: bool = False) -> None:
        self.path = Path(path)
        self.include_state = include_state
        self.malformed_lines = 0
        self._lock = threading.Lock()

    def _write(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8", newline="\n") as file:
                file.write(line)

    def _decision(self, question: Question, policy: Policy, decision: Decision, name: str | None = None, temperature: float | None = None) -> None:
        data = decision.model_dump()
        record = {"type": "decision", "log_version": 1, "ts": datetime.now(timezone.utc).isoformat(),
                  **{key: data[key] for key in ("request_id", "kind", "status", "value", "confidence", "probabilities", "provider", "model", "latency_ms", "attempts")},
                  "min_confidence": policy.min_confidence, "state_sha256": _hash(question.state),
                  "instructions_sha256": _hash(question.instructions)}
        if name is not None:
            record["name"] = name
        if temperature is not None:
            # The stored probabilities are the corrected ones; the temperature lets a later fit recover the model's own.
            record["temperature"] = temperature
        if self.include_state:
            record["state"] = question.state
        self._write(record)

    def record_outcome(self, request_id: str, *, correct: bool | None = None, expected: str | bool | int | float | None = None, source: str = "app") -> None:
        """Append one outcome without looking up the decision."""
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("request_id must be a nonempty string")
        if (correct is None) == (expected is None):
            raise ValueError("provide exactly one of correct and expected")
        if correct is not None and type(correct) is not bool:
            raise ValueError("correct must be a bool")
        if expected is not None and (type(expected) not in (str, bool, int, float) or (type(expected) is float and not math.isfinite(expected))):
            raise ValueError("expected must be a string, bool, int, or finite float")
        record = {"type": "outcome", "log_version": 1, "ts": datetime.now(timezone.utc).isoformat(),
                  "request_id": request_id, "source": source}
        record["correct" if correct is not None else "expected"] = correct if correct is not None else expected
        self._write(record)

    def record_shadow(self, request_id: str, current: str | bool | int | float | None, agreed: bool | None, *,
                      status: str | None = None, value: str | bool | None = None) -> None:
        """Append a comparison with the application's current answer. status and value hold a cost rule's answer."""
        record = {"type": "shadow", "log_version": 1, "ts": datetime.now(timezone.utc).isoformat(),
                  "request_id": request_id, "current": current, "agreed": agreed}
        if status is not None:
            record.update(status=status, value=value)
        self._write(record)

    def record_cost(self, request_id: str, *, name: str | None, status: str, value: str | bool | None, expected_cost: float | None, abstain_cost: float | None) -> None:
        """Append the answer a cost rule picked, which can differ from the model's top answer."""
        self._write({"type": "cost", "log_version": 1, "ts": datetime.now(timezone.utc).isoformat(), "request_id": request_id,
                     "name": name, "status": status, "value": value, "expected_cost": expected_cost, "abstain_cost": abstain_cost})

    def _shadow_error(self, exc: Exception) -> None:
        self._write({"type": "shadow_error", "log_version": 1, "ts": datetime.now(timezone.utc).isoformat(),
                     "error": type(exc).__name__})

    def read(self) -> Iterator[dict]:
        """Yield objects in file order, counting malformed lines for this read."""
        self.malformed_lines = 0
        with self.path.open("rb") as file:
            for line in file:
                try:
                    obj = json.loads(line.decode("utf-8"))
                    if not isinstance(obj, dict):
                        raise ValueError("line is not an object")
                except (UnicodeDecodeError, ValueError):
                    self.malformed_lines += 1
                    continue
                yield obj


def _warn_write(log: DecisionLog, exc: Exception) -> None:
    logging.getLogger("decilist").warning("Decision log write failed (%s) at %s", type(exc).__name__, log.path)


def _log_decision(log: DecisionLog, question: Question, policy: Policy, decision: Decision, name: str | None = None, temperature: float | None = None) -> None:
    try:
        log._decision(question, policy, decision, name, temperature)
    except Exception as exc:
        _warn_write(log, exc)


def _log_cost(log: DecisionLog, name: str | None, costed: CostedDecision) -> None:
    try:
        log.record_cost(costed.request_id, name=name, status=costed.status, value=costed.value, expected_cost=costed.expected_cost, abstain_cost=costed.abstain_cost)
    except Exception as exc:
        _warn_write(log, exc)


def _log_outcome(log: DecisionLog, request_id: str, correct: bool) -> None:
    try:
        log.record_outcome(request_id, correct=correct, source="verify")
    except Exception as exc:
        _warn_write(log, exc)


def _table(records: list[dict], outcomes: dict[str, dict], *, thresholds, target_accuracy, min_samples: int,
           skipped_score: int, orphan_outcomes: int, malformed_lines: int, model: str | None, has_verify: bool = False) -> dict:
    minimum = max((r.get("min_confidence", 0) for r in records), default=0)
    requested = [i / 20 for i in range(21)] if thresholds is None else list(thresholds)
    if any(type(t) not in (int, float) or not math.isfinite(t) or not 0 <= t <= 1 for t in requested):
        raise ValueError("thresholds must be finite numbers in [0, 1]")
    if target_accuracy is not None and (type(target_accuracy) not in (int, float) or not math.isfinite(target_accuracy) or not 0 <= target_accuracy <= 1):
        raise ValueError("target_accuracy must be in [0, 1]")
    if type(min_samples) is not int or min_samples < 1:
        raise ValueError("min_samples must be a positive integer")
    warnings = []
    if minimum > 0:
        warnings.append("To see lower thresholds, run at min_confidence=0.")
    if thresholds is not None and any(t < minimum for t in requested):
        warnings.append("Requested thresholds below the highest logged min_confidence were omitted.")
    providers = {r["provider"] for r in records if r.get("provider") is not None}
    providers.update(a["provider"] for r in records for a in r.get("attempts", []) if a.get("provider") is not None and a.get("outcome") != "blocked")
    if model is None and (len(providers) > 1 or len({r.get("model") for r in records if r.get("model") is not None}) > 1):
        warnings.append("A table across providers or models does not describe any one of them.")
    if any(sum(a.get("outcome") != "blocked" for a in r.get("attempts", [])) > 1 for r in records):
        warnings.append("A fallback provider answered; a different threshold could change the provider, which this table cannot show.")
    if len({r.get("instructions_sha256") for r in records if r.get("instructions_sha256") is not None}) > 1 or len({r.get("kind") for r in records}) > 1:
        warnings.append("This table mixes different questions or kinds.")
    labelled = [r for r in records if r.get("request_id") in outcomes]
    if len(labelled) < len(records):
        warnings.append("Only some decisions have outcomes; accuracy may be off if those labelled are not a fair sample.")
    if has_verify:
        warnings.append("Verify labels mean passed the check.")

    def is_correct(record: dict) -> bool:
        outcome = outcomes[record["request_id"]]
        return outcome["correct"] if type(outcome.get("correct")) is bool else record.get("value") == outcome.get("expected")

    rows = []
    for threshold in sorted(set(t for t in requested if t >= minimum)):
        covered = [r for r in records if r.get("confidence") is not None and r["confidence"] >= threshold]
        known = [r for r in covered if r.get("request_id") in outcomes]
        n = len(known)
        correct = sum(is_correct(r) for r in known)
        accuracy = correct / n if n else None
        lower = _wilson_lower(correct, n)
        rows.append({"threshold": threshold, "covered": len(covered), "coverage": len(covered)/len(records) if records else 0.0,
                     "labelled": n, "correct": correct, "accuracy": accuracy, "wilson_lower": lower, "small_sample": n < min_samples})
    recommended = next((r["threshold"] for r in rows if target_accuracy is not None and not r["small_sample"] and r["wilson_lower"] is not None and r["wilson_lower"] >= target_accuracy), None)
    reason = ("No target accuracy was requested." if target_accuracy is None else
              "No threshold meets the target and minimum sample size." if recommended is None else
              "This is the lowest threshold meeting the target and minimum sample size.")
    reliability = []
    for i in range(10):
        group = [r for r in labelled if r.get("confidence") is not None and min(int(r["confidence"] * 10), 9) == i]
        if group:
            reliability.append({"low": i/10, "high": (i+1)/10, "labelled": len(group),
                                "mean_confidence": sum(r["confidence"] for r in group)/len(group),
                                "accuracy": sum(is_correct(r) for r in group)/len(group)})
    return {"decisions": len(records), "labelled": len(labelled), "unlabelled": len(records)-len(labelled),
            "abstained": sum(r.get("status") == "abstained" for r in records), "skipped_score": skipped_score,
            "orphan_outcomes": orphan_outcomes, "malformed_lines": malformed_lines, "min_logged_confidence": minimum,
            "rows": rows, "recommended": recommended, "recommended_reason": reason, "reliability": reliability, "warnings": warnings}


def threshold_table(path: str | Path, *, thresholds: Iterable[float] | None = None, target_accuracy: float | None = None, min_samples: int = 30, model: str | None = None, name: str | None = None) -> dict:
    """Calculate calibration statistics from an application's decision log."""
    log = DecisionLog(path)
    lines = list(log.read())
    decisions, outcome_lines = [], []
    malformed = log.malformed_lines
    for record in lines:
        kind = record.get("type")
        if kind not in ("decision", "outcome"):
            continue
        request_id = record.get("request_id")
        confidence = record.get("confidence")
        minimum = record.get("min_confidence", 0)
        if (not isinstance(request_id, str) or not request_id.strip() or
            (kind == "decision" and confidence is not None and
             (type(confidence) not in (int, float) or not 0 <= confidence <= 1)) or
            (kind == "decision" and (type(minimum) not in (int, float) or not 0 <= minimum <= 1)) or
            (kind == "outcome" and type(record.get("correct")) is not bool and "expected" not in record)):
            malformed += 1
            continue
        (decisions if kind == "decision" else outcome_lines).append(record)
    ids = {r["request_id"] for r in decisions}
    orphans = sum(r.get("request_id") not in ids for r in outcome_lines)
    names = sorted({r["name"] for r in decisions if isinstance(r.get("name"), str)})
    if name is not None:
        decisions = [r for r in decisions if r.get("name") == name]
    selected = [r for r in decisions if model is None or r.get("model") == model]
    records = [r for r in selected if r.get("kind") in ("choose", "predict")]
    selected_ids = {r.get("request_id") for r in records}
    outcomes = {r["request_id"]: r for r in outcome_lines if r.get("request_id") in selected_ids}
    table = _table(records, outcomes, thresholds=thresholds, target_accuracy=target_accuracy, min_samples=min_samples,
                   skipped_score=sum(r.get("kind") == "score" for r in selected), orphan_outcomes=orphans,
                   malformed_lines=malformed, model=model,
                   has_verify=any(r.get("source") == "verify" for r in outcomes.values()))
    omitted = sum(r.get("kind") in ("choose", "predict") and r.get("status") == "abstained" and r.get("model") is None for r in decisions) if model is not None else 0
    table["name"], table["names"] = name, names
    if name is None and len(names) > 1:
        table["warnings"].append(f"The log holds {len(names)} named decisions ({', '.join(names)}). Report on one with its name.")
    if name is not None and name not in names:
        table["warnings"].append(f"No decision named {name!r} is in the log.")
    if omitted:
        table["warnings"].append(f"{omitted} abstained decisions name no model and were left out, so coverage only counts decisions this model answered.")
    return table


def threshold_table_from_cases(cases: list[Case], results: list[Decision], *, thresholds: Iterable[float] | None = None, target_accuracy: float | None = None, min_samples: int = 30) -> dict:
    """Calculate the same calibration statistics from labelled evaluation cases."""
    from .evaluation import validate_cases
    validate_cases(cases)
    if len(cases) != len(results):
        raise ValueError("cases and results must have equal lengths")
    records, outcomes = [], {}
    skipped = 0
    for case, result in zip(cases, results):
        result = validate_decision(case.question, Decision.model_validate(result.model_dump()))
        if case.question.kind == "score":
            skipped += 1
            continue
        record = result.model_dump()
        record.update(min_confidence=0, instructions_sha256=_hash(case.question.instructions))
        records.append(record)
        outcomes[result.request_id] = {"expected": case.expected, "source": "app"}
    table = _table(records, outcomes, thresholds=thresholds, target_accuracy=target_accuracy, min_samples=min_samples,
                   skipped_score=skipped, orphan_outcomes=0, malformed_lines=0, model=None)
    table["name"], table["names"] = None, []
    if any(r.get("status") == "abstained" and any(a.get("outcome") == "low_confidence" for a in r.get("attempts", [])) for r in records):
        table["warnings"].append("Some decisions abstained on low confidence, so rows below the threshold used are incomplete. Evaluate at min_confidence=0 for the full table.")
    return table


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="decilist calibrate", description="Calibrate from local decision outcomes")
    parser.add_argument("path")
    parser.add_argument("--target-accuracy", type=float)
    parser.add_argument("--min-samples", type=int, default=30)
    parser.add_argument("--model")
    parser.add_argument("--decision", help="Only decisions defined with this name")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        table = threshold_table(args.path, target_accuracy=args.target_accuracy, min_samples=args.min_samples, model=args.model, name=args.decision)
    except FileNotFoundError:
        print(f"Log file not found: {args.path}")
        return 2
    except (OSError, ValueError) as exc:
        print(f"Cannot calibrate: {exc}")
        return 2
    if table["decisions"] == 0 and table["skipped_score"] == 0:
        print("No decisions found in the log.")
        return 1
    if args.json:
        print(json.dumps(table, ensure_ascii=False, indent=2))
    else:
        print("Threshold  Covered  Coverage  Labelled  Accuracy  Wilson lower")
        for row in table["rows"]:
            accuracy = "-" if row["accuracy"] is None else f'{row["accuracy"]:.3f}'
            lower = "-" if row["wilson_lower"] is None else f'{row["wilson_lower"]:.3f}'
            print(f'{row["threshold"]:9.2f}  {row["covered"]:7d}  {row["coverage"]:8.1%}  {row["labelled"]:8d}  {accuracy:>8}  {lower:>12}')
        print(f'Recommended: {table["recommended"]} - {table["recommended_reason"]}')
        for warning in table["warnings"]:
            print(f'Warning: {warning}')
    return 0
