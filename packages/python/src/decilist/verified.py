"""Check ranked choices with application code."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal

from .contracts import Decision
from .providers import _call_function
from .decision_log import DecisionLog, _log_outcome


@dataclass(frozen=True)
class TriedOption:
    """The outcome and elapsed time of one application check."""
    label: str
    ok: bool
    latency_ms: float
    error: str | None = None


@dataclass(frozen=True)
class VerifiedDecision:
    """The checked choice, fallback result, or unresolved outcome."""
    status: Literal["verified", "fallback", "unresolved"]
    value: str | None
    result: Any
    decision: Decision
    tried: tuple[TriedOption, ...]


def _validate_callbacks(verify: Callable, fallback: Callable | None, max_tries: int) -> None:
    if isinstance(max_tries, bool) or not isinstance(max_tries, int) or max_tries < 1:
        raise ValueError("max_tries must be an integer of at least 1")
    if not callable(verify):
        raise TypeError("verify must be callable")
    if fallback is not None and not callable(fallback):
        raise TypeError("fallback must be callable or None")


async def _verify_decision(decision: Decision, verify: Callable, fallback: Callable | None, max_tries: int, log: DecisionLog | None = None) -> VerifiedDecision:
    tried: list[TriedOption] = []
    if decision.status == "accepted":
        # A remote response may preserve a different probability key order from the request.
        ordered = sorted(decision.probabilities, key=lambda label: (-decision.probabilities[label], label != decision.value))
        for label in ordered[:max_tries]:
            tick = perf_counter()
            error = None
            result = None
            try:
                result = await _call_function(verify, label)
                ok = bool(result)
            except Exception as exc:
                ok = False
                error = type(exc).__name__
            tried.append(TriedOption(label, ok, (perf_counter() - tick) * 1000, error))
            if len(tried) == 1 and error is None and log is not None:
                _log_outcome(log, decision.request_id, ok)
            if ok:
                return VerifiedDecision("verified", label, result, decision, tuple(tried))
    if fallback is not None:
        return VerifiedDecision("fallback", None, await _call_function(fallback), decision, tuple(tried))
    return VerifiedDecision("unresolved", None, None, decision, tuple(tried))
