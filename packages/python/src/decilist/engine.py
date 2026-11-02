from __future__ import annotations

import asyncio
import threading
import weakref
from enum import Enum
from time import perf_counter
from typing import Any, Callable, Protocol
from uuid import uuid4
from .contracts import Attempt, Choose, Decision, Policy, Predict, Question, Score, State, _encode_options, encode_state, question_adapter, validate_probabilities, value_from
from .verified import VerifiedDecision, _validate_callbacks, _verify_decision
from .decision_log import DecisionLog, _log_decision


class Provider(Protocol):
    name: str
    model: str
    locality: str

    async def infer(self, question: Question) -> dict[str, float]: ...


class Decilist:
    """Ordered providers; all failed/uncertain/blocked attempts end in abstention."""

    def __init__(self, providers: list[Provider], policy: Policy | None = None, *, log: DecisionLog | None = None):
        if not providers or len({p.name for p in providers}) != len(providers):
            raise ValueError("provide at least one provider with unique names")
        self.providers = tuple(providers)
        self.policy = policy or Policy()
        self.log = log
        self._sync_lock = threading.Lock()
        self._runner: asyncio.Runner | None = None

    async def decide(self, question: Question, policy: Policy | None = None) -> Decision:
        if self.log is None:
            return await self._decide(question, policy)
        question = question_adapter.validate_python(question.model_dump())
        used_policy = Policy.model_validate((policy or self.policy).model_dump())
        decision = await self._decide(question, used_policy)
        _log_decision(self.log, question, used_policy, decision)
        return decision

    async def _decide(self, question: Question, policy: Policy | None = None) -> Decision:
        question = question_adapter.validate_python(question.model_dump())
        policy = Policy.model_validate((policy or self.policy).model_dump())
        started = perf_counter()
        attempts: list[Attempt] = []
        result = dict(request_id=str(uuid4()), kind=question.kind, attempts=attempts)
        for provider in self.providers:
            tick = perf_counter()
            if policy.local_only and provider.locality != "local":
                attempts.append(Attempt(provider=provider.name, outcome="blocked", latency_ms=0))
                continue
            try:
                raw = await asyncio.wait_for(provider.infer(question), policy.timeout_ms / 1000)
                try:
                    probabilities = validate_probabilities(question, raw)
                except ValueError:
                    outcome = "invalid_output"
                else:
                    confidence = max(probabilities.values())
                    if confidence >= policy.min_confidence:
                        attempts.append(Attempt(provider=provider.name, outcome="accepted", latency_ms=(perf_counter()-tick)*1000))
                        return Decision(**result, status="accepted", value=value_from(question, probabilities),
                                        probabilities=probabilities, confidence=confidence, provider=provider.name,
                                        model=provider.model, latency_ms=(perf_counter()-started)*1000)
                    outcome = "low_confidence"
            except TimeoutError:
                outcome = "timeout"
            except Exception:
                # Deliberately omit provider exception text: it can contain inputs or credentials.
                outcome = "error"
            attempts.append(Attempt(provider=provider.name, outcome=outcome, latency_ms=(perf_counter()-tick)*1000))
        return Decision(**result, status="abstained", latency_ms=(perf_counter()-started)*1000)

    async def choose(self, state: State, instructions: str, options: dict[str, str] | type[Enum], *, policy: Policy | None = None) -> Decision:
        return await self.decide(Choose(state=encode_state(state), instructions=instructions, options=_encode_options(options)), policy)

    async def choose_verified(self, state: State, instructions: str, options: dict[str, str] | type[Enum], *, verify: Callable[[str], Any], fallback: Callable[[], Any] | None = None, max_tries: int = 3, policy: Policy | None = None) -> VerifiedDecision:
        """Check ranked options; policy timeout covers the model, not verify or fallback."""
        _validate_callbacks(verify, fallback, max_tries)
        decision = await self.choose(state, instructions, options, policy=policy)
        return await _verify_decision(decision, verify, fallback, max_tries, self.log)

    async def predict(self, state: State, instructions: str, *, policy: Policy | None = None) -> Decision:
        return await self.decide(Predict(state=encode_state(state), instructions=instructions), policy)

    async def score(self, state: State, instructions: str, levels: list[str], *, policy: Policy | None = None) -> Decision:
        return await self.decide(Score(state=encode_state(state), instructions=instructions, levels=levels), policy)

    def _run_sync(self, method, *args, **kwargs) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("A loop is already running; use await decisions.choose(...) instead")
        with self._sync_lock:
            if self._runner is None:
                self._runner = asyncio.Runner()
                weakref.finalize(self, self._runner.close)
            return self._runner.run(method(*args, **kwargs))

    def decide_sync(self, question: Question, policy: Policy | None = None) -> Decision:
        """Run synchronously; use sync or async per instance, as providers may be loop-bound."""
        return self._run_sync(self.decide, question, policy)

    def choose_sync(self, state: State, instructions: str, options: dict[str, str] | type[Enum], *, policy: Policy | None = None) -> Decision:
        """Run synchronously; use sync or async per instance, as providers may be loop-bound."""
        return self._run_sync(self.choose, state, instructions, options, policy=policy)

    def choose_verified_sync(self, state: State, instructions: str, options: dict[str, str] | type[Enum], *, verify: Callable[[str], Any], fallback: Callable[[], Any] | None = None, max_tries: int = 3, policy: Policy | None = None) -> VerifiedDecision:
        """Run the checked choice synchronously; checks and fallback have no timeout."""
        return self._run_sync(self.choose_verified, state, instructions, options, verify=verify, fallback=fallback, max_tries=max_tries, policy=policy)

    def predict_sync(self, state: State, instructions: str, *, policy: Policy | None = None) -> Decision:
        """Run synchronously; use sync or async per instance, as providers may be loop-bound."""
        return self._run_sync(self.predict, state, instructions, policy=policy)

    def score_sync(self, state: State, instructions: str, levels: list[str], *, policy: Policy | None = None) -> Decision:
        """Run synchronously; use sync or async per instance, as providers may be loop-bound."""
        return self._run_sync(self.score, state, instructions, levels, policy=policy)
