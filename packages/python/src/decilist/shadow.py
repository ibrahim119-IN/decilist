"""Run decisions alongside an application's current answers and report comparisons."""
from __future__ import annotations

import argparse
import asyncio
from enum import Enum
import json
import logging
import math
from pathlib import Path
from typing import Any, Callable

from .client import Client
from .contracts import Choose, Decision, Policy, Predict, Question, Score, State, _encode_options, encode_state
from .decision_log import DecisionLog, _wilson_lower
from .engine import Decilist


class Shadow:
    """Run bounded background comparisons without changing application decisions."""

    def __init__(self, decisions: Decilist | Client, *, max_pending: int = 32) -> None:
        if not isinstance(getattr(decisions, "log", None), DecisionLog):
            raise ValueError("decisions must have a DecisionLog")
        if type(max_pending) is not int or max_pending < 1:
            raise ValueError("max_pending must be a positive integer")
        self.decisions = decisions
        self.max_pending = max_pending
        self.errors = 0
        self.dropped = 0
        self._tasks: set[asyncio.Task] = set()
        self._warned_no_loop = False

    def _error(self, exc: Exception, *, before_decision: bool) -> None:
        self.errors += 1
        logging.getLogger("decilist").warning("Shadow observation failed (%s)", type(exc).__name__)
        if before_decision:
            try:
                self.decisions.log._shadow_error(exc)
            except Exception as write_exc:
                logging.getLogger("decilist").warning("Shadow error log failed (%s)", type(write_exc).__name__)

    def _start(self, build: Callable[[], Question], current: Any, policy: Policy | None) -> asyncio.Task | None:
        try:
            if isinstance(current, Enum):
                current = current.name
            if type(current) not in (str, bool, int, float, type(None)) or (type(current) is float and not math.isfinite(current)):
                raise TypeError("invalid current answer")
        except Exception as exc:
            self._error(exc, before_decision=True)
            return None
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self.dropped += 1
            if not self._warned_no_loop:
                logging.getLogger("decilist").warning("Shadow observation dropped: no running event loop")
                self._warned_no_loop = True
            return None
        self._tasks.difference_update(task for task in self._tasks.copy() if task.done())
        if len(self._tasks) >= self.max_pending:
            self.dropped += 1
            return None
        try:
            question = build()
            if policy is not None:
                policy = policy.model_copy(deep=True)
        except Exception as exc:
            self._error(exc, before_decision=True)
            return None
        coroutine = self._run(question, current, policy)
        try:
            task = asyncio.create_task(coroutine)
        except Exception as exc:
            coroutine.close()
            self._error(exc, before_decision=True)
            return None
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _run(self, question: Question, current: Any, policy: Policy | None) -> Decision | None:
        decision = None
        try:
            decision = await self.decisions.decide(question, policy=policy)
            for attempt in decision.attempts:
                if attempt.outcome in ("error", "timeout"):
                    self.errors += 1
                    logging.getLogger("decilist").warning("Shadow provider attempt failed (%s)", attempt.outcome)
            agreed = None
            if decision.status == "accepted" and current is not None:
                if decision.kind == "choose":
                    agreed = decision.value == current
                elif decision.kind == "predict" and type(current) is bool:
                    agreed = decision.value is current
                elif decision.kind == "score" and type(current) in (int, float):
                    agreed = abs(decision.value - current) <= .5
            self.decisions.log.record_shadow(decision.request_id, current, agreed)
            return decision
        except Exception as exc:
            self._error(exc, before_decision=decision is None)
            return None

    def choose(self, state: State, instructions: str, options: dict[str, str] | type[Enum], *, current: Any, policy: Policy | None = None) -> bool:
        """Start a choice comparison and return whether it started."""
        return self._start(lambda: Choose(state=encode_state(state), instructions=instructions, options=_encode_options(options)), current, policy) is not None

    def predict(self, state: State, instructions: str, *, current: Any, policy: Policy | None = None) -> bool:
        """Start a prediction comparison and return whether it started."""
        return self._start(lambda: Predict(state=encode_state(state), instructions=instructions), current, policy) is not None

    def score(self, state: State, instructions: str, levels: list[str], *, current: Any, policy: Policy | None = None) -> bool:
        """Start a score comparison and return whether it started."""
        return self._start(lambda: Score(state=encode_state(state), instructions=instructions, levels=levels), current, policy) is not None

    def observe(self, question: Question, *, current: Any, policy: Policy | None = None) -> bool:
        """Start a comparison for a question and return whether it started."""
        return self._start(lambda: question.model_copy(deep=True), current, policy) is not None

    async def _compare(self, build: Callable[[], Question], current: Any, policy: Policy | None) -> Decision | None:
        task = self._start(build, current, policy)
        return await task if task is not None else None

    async def compare_choose(self, state: State, instructions: str, options: dict[str, str] | type[Enum], *, current: Any, policy: Policy | None = None) -> Decision | None:
        """Wait for a choice comparison."""
        return await self._compare(lambda: Choose(state=encode_state(state), instructions=instructions, options=_encode_options(options)), current, policy)

    async def compare_predict(self, state: State, instructions: str, *, current: Any, policy: Policy | None = None) -> Decision | None:
        """Wait for a prediction comparison."""
        return await self._compare(lambda: Predict(state=encode_state(state), instructions=instructions), current, policy)

    async def compare_score(self, state: State, instructions: str, levels: list[str], *, current: Any, policy: Policy | None = None) -> Decision | None:
        """Wait for a score comparison."""
        return await self._compare(lambda: Score(state=encode_state(state), instructions=instructions, levels=levels), current, policy)

    async def compare(self, question: Question, *, current: Any, policy: Policy | None = None) -> Decision | None:
        """Wait for a comparison for a question."""
        return await self._compare(lambda: question.model_copy(deep=True), current, policy)

    async def drain(self, timeout: float | None = None) -> int:
        """Wait for current tasks without cancelling those that outlast the timeout."""
        tasks = set(self._tasks)
        if not tasks:
            return 0
        try:
            _, pending = await asyncio.wait(tasks, timeout=timeout)
        except Exception as exc:
            self._error(exc, before_decision=False)
            return sum(not task.done() for task in tasks)
        self._tasks.difference_update(task for task in tasks if task.done())
        return len(pending)


def _number(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _matches(kind: str, answer: Any, expected: Any) -> bool:
    if kind == "choose":
        return type(answer) is str and type(expected) is str and answer == expected
    if kind == "predict":
        return type(answer) is bool and type(expected) is bool and answer == expected
    if kind == "score":
        return _number(answer) and _number(expected) and abs(answer - expected) <= .5
    return False


def _scalar(value: Any) -> bool:
    return value is None or type(value) in (str, bool) or _number(value)


def _valid(record: dict) -> bool:
    kind = record.get("type")
    if kind == "shadow_error":
        return isinstance(record.get("error"), str) and bool(record["error"])
    request_id = record.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        return False
    if kind == "shadow":
        return "current" in record and _scalar(record["current"]) and "agreed" in record and (record["agreed"] is None or type(record["agreed"]) is bool)
    if kind == "outcome":
        correct = type(record.get("correct")) is bool
        expected = "expected" in record and record["expected"] is not None and _scalar(record["expected"])
        return correct or expected
    attempts = record.get("attempts", [])
    if not isinstance(attempts, list) or any(not isinstance(attempt, dict) for attempt in attempts):
        return False
    confidence = record.get("confidence")
    latency = record.get("latency_ms")
    status = record.get("status")
    value = record.get("value")
    question_kind = record.get("kind")
    valid_value = (value is None if status == "abstained" else
                   type(value) is str if question_kind == "choose" else
                   type(value) is bool if question_kind == "predict" else _number(value))
    return (question_kind in ("choose", "predict", "score") and status in ("accepted", "abstained")
            and valid_value
            and (confidence is None or (_number(confidence) and 0 <= confidence <= 1))
            and _number(latency) and latency >= 0
            and (record.get("model") is None or isinstance(record["model"], str))
            and (record.get("state_sha256") is None or isinstance(record["state_sha256"], str)))


def _counts(rows: list[tuple[dict, dict]]) -> dict:
    n = len(rows)
    accepted = sum(d["status"] == "accepted" for d, _ in rows)
    compared = sum(s["agreed"] is not None for _, s in rows)
    agreements = sum(s["agreed"] is True for _, s in rows)
    failed_attempts = sum(attempt.get("outcome") in ("error", "timeout") for d, _ in rows for attempt in d.get("attempts", []))
    rate = agreements / compared if compared else None
    lower = _wilson_lower(agreements, compared)
    return {"observations": n, "accepted": accepted, "abstained": n-accepted, "coverage": accepted/n if n else 0.0,
            "compared": compared, "agreements": agreements, "agreement_rate": rate, "agreement_wilson_lower": lower,
            "disagreements_total": compared-agreements, "failed_attempts": failed_attempts}


def shadow_report(path: str | Path, *, max_disagreements: int = 20, model: str | None = None) -> dict:
    """Summarize shadow comparisons and matching outcomes from a decision log."""
    if type(max_disagreements) is not int or max_disagreements < 0:
        raise ValueError("max_disagreements must be a nonnegative integer")
    log = DecisionLog(path)
    lines = list(log.read())
    malformed = log.malformed_lines
    decisions, shadows, outcomes = {}, [], {}
    errors = 0
    for record in lines:
        kind = record.get("type")
        if kind not in ("decision", "shadow", "shadow_error", "outcome"):
            continue
        if not _valid(record):
            malformed += 1
            continue
        if kind == "shadow_error":
            errors += 1
        elif kind == "decision":
            decisions[record["request_id"]] = record
        elif kind == "shadow":
            shadows.append(record)
        else:
            outcomes[record["request_id"]] = record
    rows = []
    unnamed = 0
    for shadow in shadows:
        decision = decisions.get(shadow["request_id"])
        if decision is None:
            malformed += 1
        elif model is None or decision.get("model") == model:
            rows.append((decision, shadow))
        elif decision.get("model") is None:
            unnamed += 1
    result = _counts(rows)
    result.update(errors=errors + result["failed_attempts"], malformed_lines=malformed)
    result["by_kind"] = {kind: _counts([row for row in rows if row[0]["kind"] == kind]) for kind in sorted({d["kind"] for d, _ in rows})}
    result["disagreements"] = [{"request_id": d["request_id"], "kind": d["kind"], "value": d.get("value"),
                                "current": s["current"], "confidence": d.get("confidence"), "state_sha256": d.get("state_sha256")}
                               for d, s in reversed(rows) if s["agreed"] is False][:max_disagreements]
    latency = sorted(d["latency_ms"] for d, _ in rows)
    result["latency_ms"] = {"p50": latency[math.ceil(len(latency)*.5)-1] if latency else None,
                            "p95": latency[math.ceil(len(latency)*.95)-1] if latency else None,
                            "max": latency[-1] if latency else None}
    labelled = [(d, s, outcomes[d["request_id"]]) for d, s in rows if d["request_id"] in outcomes]
    if labelled:
        accepted = [(d, s, o) for d, s, o in labelled if d["status"] == "accepted"]
        expected = [(d, s, o) for d, s, o in labelled if "expected" in o and o["expected"] is not None and _scalar(o["expected"])]
        current = [(d, s, o) for d, s, o in expected if s["current"] is not None]
        both = [(d, s, o) for d, s, o in current if d["status"] == "accepted"]
        def model_correct(d: dict, o: dict) -> bool:
            return o["correct"] if type(o.get("correct")) is bool else _matches(d["kind"], d["value"], o["expected"])
        mc = sum(model_correct(d, o) for d, _, o in accepted)
        cc = sum(_matches(d["kind"], s["current"], o["expected"]) for d, s, o in current)
        result["outcomes"] = {"labelled": len(labelled), "model_correct": mc, "model_accuracy": mc/len(accepted) if accepted else None,
                              "current_correct": cc, "current_accuracy": cc/len(current) if current else None,
                              "both_labelled": len(both), "model_correct_on_both": sum(model_correct(d, o) for d, _, o in both),
                              "current_correct_on_both": sum(_matches(d["kind"], s["current"], o["expected"]) for d, s, o in both)}
    warnings = []
    if model is None and len({d.get("model") for d, _ in rows if d.get("model") is not None}) > 1:
        warnings.append("The report mixes more than one model.")
    if unnamed:
        warnings.append(f"{unnamed} observations name no model and were left out, so coverage and failed attempts only count observations this model answered.")
    if result["compared"] < 30:
        warnings.append("Fewer than 30 observations were compared.")
    if len(labelled) < len(rows):
        warnings.append("Only some observations have outcomes.")
    if not labelled:
        warnings.append("Agreement is not accuracy.")
    warnings.append("The application's current answer may itself be wrong.")
    result["warnings"] = warnings
    return result


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="decilist shadow-report", description="Report local shadow comparisons")
    parser.add_argument("path")
    parser.add_argument("--model")
    parser.add_argument("--max-disagreements", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = shadow_report(args.path, model=args.model, max_disagreements=args.max_disagreements)
    except FileNotFoundError:
        print(f"Log file not found: {args.path}")
        return 2
    except (OSError, ValueError) as exc:
        print(f"Cannot report: {exc}")
        return 2
    if not report["observations"]:
        print("No shadow observations found in the log for this selection.")
        if report["errors"] or report["malformed_lines"]:
            print(f'Errors: {report["errors"]}; failed attempts: {report["failed_attempts"]}; malformed lines: {report["malformed_lines"]}')
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f'Observations: {report["observations"]}; coverage: {report["coverage"]:.1%}; compared: {report["compared"]}; agreements: {report["agreements"]}')
        print(f'Disagreements: {report["disagreements_total"]}; errors: {report["errors"]}; failed attempts: {report["failed_attempts"]}; malformed lines: {report["malformed_lines"]}')
        def percent(value: float | None) -> str:
            return f"{value:.1%}" if value is not None else "-"
        print(f'Agreement rate: {percent(report["agreement_rate"])}; Wilson lower: {percent(report["agreement_wilson_lower"])}')
        latency = report["latency_ms"]
        print(f'Latency ms (p50/p95/max): {latency["p50"]:.1f} / {latency["p95"]:.1f} / {latency["max"]:.1f}')
        if "outcomes" in report:
            outcomes = report["outcomes"]
            print(f'Labelled: {outcomes["labelled"]}; model accuracy: {percent(outcomes["model_accuracy"])}; current accuracy: {percent(outcomes["current_accuracy"])}')
        for disagreement in report["disagreements"]:
            print(f'Disagreement {disagreement["request_id"]}: {disagreement["kind"]}, model={disagreement["value"]!r}, current={disagreement["current"]!r}')
        for warning in report["warnings"]:
            print(f"Warning: {warning}")
    return 0
