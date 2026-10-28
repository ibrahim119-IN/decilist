from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Protocol
from uuid import uuid4
from .contracts import Attempt, Choose, Decision, Policy, Predict, Question, Score, question_adapter, validate_probabilities, value_from


class Provider(Protocol):
    name: str
    model: str
    locality: str

    async def infer(self, question: Question) -> dict[str, float]: ...


class Decilist:
    """Ordered providers; all failed/uncertain/blocked attempts end in abstention."""

    def __init__(self, providers: list[Provider], policy: Policy | None = None):
        if not providers or len({p.name for p in providers}) != len(providers):
            raise ValueError("provide at least one provider with unique names")
        self.providers = tuple(providers)
        self.policy = policy or Policy()

    async def decide(self, question: Question, policy: Policy | None = None) -> Decision:
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

    async def choose(self, state: str, instructions: str, options: dict[str, str], *, policy: Policy | None = None) -> Decision:
        return await self.decide(Choose(state=state, instructions=instructions, options=options), policy)

    async def predict(self, state: str, instructions: str, *, policy: Policy | None = None) -> Decision:
        return await self.decide(Predict(state=state, instructions=instructions), policy)

    async def score(self, state: str, instructions: str, levels: list[str], *, policy: Policy | None = None) -> Decision:
        return await self.decide(Score(state=state, instructions=instructions, levels=levels), policy)
