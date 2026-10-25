from __future__ import annotations

import httpx
from .contracts import Choose, Decision, DecisionRequest, Policy, Predict, Question, Score, validate_decision


class Client:
    """Async HTTP client for the same decision contract, including server policy traces."""

    def __init__(self, base_url: str = "http://127.0.0.1:8765", *, timeout: float = 130):
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, trust_env=False)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self._http.aclose()

    async def decide(self, question: Question, policy: Policy | None = None) -> Decision:
        request = DecisionRequest(question=question, policy=policy or Policy())
        response = await self._http.post("/v1/decisions", json=request.model_dump())
        response.raise_for_status()
        return validate_decision(question, Decision.model_validate(response.json()))

    async def choose(self, state: str, instructions: str, options: dict[str, str], *, policy: Policy | None = None) -> Decision:
        return await self.decide(Choose(state=state, instructions=instructions, options=options), policy)

    async def predict(self, state: str, instructions: str, *, policy: Policy | None = None) -> Decision:
        return await self.decide(Predict(state=state, instructions=instructions), policy)

    async def score(self, state: str, instructions: str, levels: list[str], *, policy: Policy | None = None) -> Decision:
        return await self.decide(Score(state=state, instructions=instructions, levels=levels), policy)
