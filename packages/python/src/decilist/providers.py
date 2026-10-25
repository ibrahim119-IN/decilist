"""Small provider interface; no model downloads or network calls on import."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from .contracts import Choose, Predict, Question, labels_for


class FunctionProvider:
    """Explicit local rules/baseline. Function must be fast and non-blocking."""
    locality = "local"

    def __init__(self, fn: Callable[[Question], dict[str, float]], *, name: str = "rules", model: str = "rules-v1"):
        self.fn, self.name, self.model = fn, name, model

    async def infer(self, question: Question) -> dict[str, float]:
        return self.fn(question)


class UniformProvider:
    """Honest uninformative baseline; normally abstains under the default policy."""
    locality, name, model = "local", "uniform", "uniform-v1"

    async def infer(self, question: Question) -> dict[str, float]:
        labels = labels_for(question)
        return {label: 1 / len(labels) for label in labels}


class LiquidLocalProvider:
    locality = "local"
    name = "liquid-local"
    model = "LiquidAI/d1-3B"
    revision = "051bcc464b01b9f92942b364d9586b0ef5912432"

    def __init__(self, model: Any):
        self._model = model
        self._lock = asyncio.Lock()
        self._running: set[asyncio.Task] = set()

    @classmethod
    def load(cls, *, allow_model_code: bool = False, device: str = "cuda", offline: bool = True):
        if not allow_model_code:
            raise ValueError("d1 requires pinned vendor Python code; explicitly set allow_model_code=True after reviewing it")
        import torch
        from transformers import AutoModel
        from huggingface_hub import snapshot_download
        dtype = torch.float32 if device == "cpu" else torch.bfloat16
        # Resolve a pinned local snapshot so vendor lazy tokenizer loading cannot
        # silently use the repository's moving main branch or access the network.
        snapshot = snapshot_download(cls.model, revision=cls.revision, local_files_only=offline)
        model = AutoModel.from_pretrained(snapshot, trust_remote_code=True, local_files_only=True, dtype=dtype)
        model = model.to(device).eval()
        return cls(model)

    @staticmethod
    def question_payload(question: Question) -> dict:
        payload: dict = {"instructions": question.instructions}
        if isinstance(question, Choose):
            payload.update(type="choice", criteria=question.options)
        elif isinstance(question, Predict):
            payload.update(type="noul")
        else:
            payload.update(type="score", criteria=question.levels)
        return payload

    def _infer_sync(self, question: Question) -> dict[str, float]:
        import torch
        with torch.inference_mode():
            raw = self._model.system_one(question.state, {"decision": self.question_payload(question)})
        return self.decode_answer(question, raw)

    @staticmethod
    def decode_answer(question: Question, raw: dict) -> dict[str, float]:
        answer = raw["answers"]["decision"]
        if isinstance(question, Predict):
            p = answer["noul"]
            if type(p) not in (float, int):
                raise ValueError("invalid predicate probability")
            return {"false": 1-p, "true": p}
        probabilities = answer["probabilities"]
        if isinstance(probabilities, list):
            if len(probabilities) != len(labels_for(question)):
                raise ValueError("invalid probability count")
            return dict(zip(labels_for(question), probabilities))
        return probabilities

    async def infer(self, question: Question) -> dict[str, float]:
        # Acquire before spawning. A timed-out native inference retains the lock until
        # it finishes, preventing overlapping GPU jobs or an unbounded worker queue.
        await self._lock.acquire()
        task = asyncio.create_task(asyncio.to_thread(self._infer_sync, question))
        self._running.add(task)
        def done(completed: asyncio.Task):
            self._lock.release()
            self._running.discard(completed)
            if not completed.cancelled():
                completed.exception()
        task.add_done_callback(done)
        return await asyncio.shield(task)
