from __future__ import annotations

import asyncio
from enum import Enum
import math
import os
from time import monotonic
from typing import Any, Callable
import httpx
from .contracts import Choose, Decision, DecisionRequest, Policy, Predict, Question, Score, State, _encode_options, encode_state, validate_decision
from .runtime import DEFAULT_URL, RuntimeCompatibilityError, RuntimeConnectionError, validate_base_url, validate_runtime_health
from .code_review import Language, ReviewPolicy, ReviewRequest, ReviewResult
from .verified import VerifiedDecision, _validate_callbacks, _verify_decision
from .decision_log import DecisionLog, _log_decision


class Client:
    """Async HTTP client for the same decision contract, including server policy traces."""

    def __init__(self, base_url: str | None = None, *, timeout: float = 130, policy: Policy | None = None, log: DecisionLog | None = None):
        self.base_url = validate_base_url(base_url if base_url is not None else os.environ.get("DECILIST_URL", DEFAULT_URL))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self.policy = Policy.model_validate((policy or Policy()).model_dump())
        self.log = log
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, trust_env=False, follow_redirects=False)
        self._ready = False

    @classmethod
    async def connect(cls, base_url: str | None = None, *, timeout: float = 130, wait_seconds: float = 10, policy: Policy | None = None, log: DecisionLog | None = None) -> Client:
        """Connect once, waiting for readiness. This never starts or downloads a runtime."""
        client = cls(base_url, timeout=timeout, policy=policy, log=log)
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
        decision = validate_decision(request.question, Decision.model_validate(response.json()))
        if self.log is not None:
            _log_decision(self.log, request.question, request.policy, decision)
        return decision

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

    async def review(self, code: str, *, task: str, language: Language = "TypeScript", policy: ReviewPolicy | None = None) -> ReviewResult:
        body = ReviewRequest(code=code, task=task, language=language, policy=policy or ReviewPolicy())
        if not self._ready:
            await self.ready()
        try:
            response = await self._http.post("/v1/code-review", json=body.model_dump())
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeConnectionError("Code review is unavailable. Start decilist review serve and connect to its port.") from exc
        result = ReviewResult.model_validate(response.json())
        if result.rules != body.policy.rules or result.threshold != body.policy.threshold or result.total_lines != len(body.code.splitlines()):
            raise ValueError("review response does not match the request")
        return result
