"""Define a question once, so its log lines, threshold, check and costs stay together."""
from __future__ import annotations

from collections.abc import Callable
from enum import Enum
import re
from typing import TYPE_CHECKING, Any

from .contracts import Choose, Decision, Policy, Predict, Question, Score, State, _encode_options, encode_state, labels_for
from .costs import CostedDecision, Costs, apply_costs
from .decision_log import DecisionLog, _log_cost
from .verified import VerifiedDecision, _validate_callbacks, _verify_decision

if TYPE_CHECKING:
    from .shadow import Shadow

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


class NamedDecision:
    """One named question. Call it with the state; create it with ``decisions.define``."""

    def __init__(self, owner: Any, name: str, instructions: str, *, options: dict[str, str] | type[Enum] | None = None,
                 levels: list[str] | None = None, policy: Policy | None = None, costs: Costs | None = None,
                 verify: Callable[[str], Any] | None = None, fallback: Callable[[], Any] | None = None, max_tries: int = 3) -> None:
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError("name must be 1 to 64 letters, digits, '_', '.' or '-', starting with a letter or digit")
        if options is not None and levels is not None:
            raise ValueError("give options for a choice or levels for a score, not both")
        self.owner, self.name, self.instructions = owner, name, instructions
        self.options = None if options is None else dict(_encode_options(options))
        self.levels = None if levels is None else list(levels)
        self.kind = "choose" if options is not None else "score" if levels is not None else "predict"
        self.policy = None if policy is None else Policy.model_validate(policy.model_dump())
        labels = labels_for(self.question("state"))  # Also validates the instructions, options and levels now.
        if costs is not None:
            if not isinstance(costs, Costs):
                raise TypeError("costs must be a Costs")
            if self.kind == "score":
                raise ValueError("costs apply to choose and predict, not score")
            costs.check_labels(labels)
        if verify is None:
            if fallback is not None:
                raise ValueError("fallback needs verify")
        else:
            if self.kind != "choose":
                raise ValueError("verify applies to a choice: give options")
            _validate_callbacks(verify, fallback, max_tries)
        self.costs, self.verify, self.fallback, self.max_tries = costs, verify, fallback, max_tries

    def question(self, state: State) -> Question:
        """Build this decision's question for one state."""
        text = encode_state(state)
        if self.kind == "choose":
            return Choose(state=text, instructions=self.instructions, options=self.options)
        if self.kind == "score":
            return Score(state=text, instructions=self.instructions, levels=self.levels)
        return Predict(state=text, instructions=self.instructions)

    async def __call__(self, state: State, *, policy: Policy | None = None) -> Decision | CostedDecision | VerifiedDecision:
        """Decide for one state. The result type follows the definition: plain, with costs, or with verify."""
        used = policy or self.policy or self.owner.policy
        if self.costs is not None:
            # The cost rule needs every probability, so the confidence threshold is not applied.
            used = used.model_copy(update={"min_confidence": 0.0})
        decision = await self.owner.decide(self.question(state), used, name=self.name)
        log = self.owner.log
        costed = None
        if self.costs is not None:
            costed = apply_costs(decision, self.costs)
            if log is not None:
                _log_cost(log, self.name, costed)
        if self.verify is None:
            return decision if costed is None else costed
        ordered = None
        if costed is not None:
            ordered = self.costs.ranked(decision.probabilities) if decision.status == "accepted" else []
        return await _verify_decision(decision, self.verify, self.fallback, self.max_tries, log, ordered=ordered, costed=costed)

    def sync(self, state: State, *, policy: Policy | None = None) -> Decision | CostedDecision | VerifiedDecision:
        """Decide synchronously. Needs a Decilist; a Client is async only."""
        run = getattr(self.owner, "_run_sync", None)
        if run is None:
            raise TypeError("sync needs a Decilist; a Client is async only")
        return run(self.__call__, state, policy=policy)

    def record_outcome(self, result: Decision | CostedDecision | VerifiedDecision | str, *, correct: bool | None = None,
                       expected: str | bool | int | float | None = None) -> None:
        """Record what turned out to be right for one of this decision's results."""
        log = self.owner.log
        if not isinstance(log, DecisionLog):
            raise ValueError("recording outcomes needs a DecisionLog on the decisions object")
        if self.costs is not None and correct is not None:
            raise ValueError("with costs, record the expected answer: the cost pick can differ from the model's top answer")
        request_id = result if isinstance(result, str) else getattr(result, "request_id", None) or result.decision.request_id
        log.record_outcome(request_id, correct=correct, expected=expected)

    def observe(self, shadow: Shadow, state: State, *, current: Any) -> bool:
        """Start a background shadow comparison under this decision's name. With costs, the cost rule's pick is compared."""
        return shadow.observe(self.question(state), current=current, policy=self.policy, name=self.name, costs=self.costs)

    async def compare(self, shadow: Shadow, state: State, *, current: Any) -> Decision | CostedDecision | None:
        """Wait for a shadow comparison under this decision's name. With costs, the cost result is returned."""
        decision = await shadow.compare(self.question(state), current=current, policy=self.policy, name=self.name, costs=self.costs)
        return decision if decision is None or self.costs is None else apply_costs(decision, self.costs)


def _define(owner: Any, name: str, instructions: str, **kwargs: Any) -> NamedDecision:
    definition = NamedDecision(owner, name, instructions, **kwargs)
    if name in owner._definitions:
        raise ValueError(f"a decision named {name!r} is already defined")
    owner._definitions[name] = definition
    return definition
