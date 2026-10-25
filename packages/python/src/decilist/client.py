from __future__ import annotations

import asyncio
import math
import os
from time import monotonic
import httpx
from .contracts import Choose, Decision, DecisionRequest, Policy, Predict, Question, Score, validate_decision
from .runtime import DEFAULT_URL, RuntimeCompatibilityError, RuntimeConnectionError, validate_base_url, validate_runtime_health


class Client:
    """Async HTTP client for the same decision contract, including server policy traces."""

    def __init__(self, base_url: str | None = None, *, timeout: float = 130, policy: Policy | None = None):
        self.base_url = validate_base_url(base_url if base_url is not None else os.environ.get("DECILIST_URL", DEFAULT_URL))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self.policy = Policy.model_validate((policy or Policy()).model_dump())
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, trust_env=False, follow_redirects=False)
        self._ready = False

    @classmethod
    async def connect(cls, base_url: str | None = None, *, timeout: float = 130, wait_seconds: float = 10, policy: Policy | None = None) -> Client:
        """Connect once, waiting for readiness. This never starts or downloads a runtime."""
        client = cls(base_url, timeout=timeout, policy=policy)
        try:
            await client.ready(wait_seconds=wait_seconds)
        except BaseException:
            await client.aclose()
            raise
        return client

    async def ready(self, *, wait_seconds: float = 0) -> dict:
        if not math.isfinite(wait_seconds) or wait_seconds < 0:
            raise ValueError("wait_seconds must be finite and nonnegative")
        deadline = monotonic() + wait_seconds
        while True:
            try:
                response = await self._http.get("/health", timeout=max(.01, min(1, deadline - monotonic())) if wait_seconds else 1)
                if response.status_code == 503:
                    raise RuntimeConnectionError("The Decilist runtime is loading. Wait for it to become ready.")
                if response.status_code != 200:
                    raise RuntimeCompatibilityError(f"The health endpoint returned HTTP {response.status_code}. Check the runtime URL.")
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise RuntimeCompatibilityError("The endpoint did not return Decilist health JSON. Check the runtime URL.") from exc
                health = validate_runtime_health(payload)
                self._ready = True
                return health
            except RuntimeCompatibilityError:
                raise
            except (httpx.HTTPError, RuntimeConnectionError) as exc:
                if monotonic() >= deadline:
                    raise RuntimeConnectionError(f"Decilist is not ready at {self.base_url}. Start it with: decilist runtime start --provider demo") from exc
                await asyncio.sleep(min(.1, max(0, deadline - monotonic())))

    async def aclose(self) -> None:
        """Close this client's connections; the runtime remains under its owner's control."""
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()

    async def decide(self, question: Question, policy: Policy | None = None) -> Decision:
        request = DecisionRequest(question=question, policy=policy or self.policy)
        if not self._ready:
            await self.ready()
        try:
            response = await self._http.post("/v1/decisions", json=request.model_dump())
        except httpx.HTTPError as exc:
            self._ready = False
            raise RuntimeConnectionError(f"The Decilist request failed at {self.base_url}. Check that the runtime is still running.") from exc
        response.raise_for_status()
        return validate_decision(question, Decision.model_validate(response.json()))

    async def choose(self, state: str, instructions: str, options: dict[str, str], *, policy: Policy | None = None) -> Decision:
        return await self.decide(Choose(state=state, instructions=instructions, options=options), policy)

    async def predict(self, state: str, instructions: str, *, policy: Policy | None = None) -> Decision:
        return await self.decide(Predict(state=state, instructions=instructions), policy)

    async def score(self, state: str, instructions: str, levels: list[str], *, policy: Policy | None = None) -> Decision:
        return await self.decide(Score(state=state, instructions=instructions, levels=levels), policy)
