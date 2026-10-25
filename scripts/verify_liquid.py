"""Reproducible cached-model smoke check; never downloads a model.

Run after explicit setup with the optional Liquid dependencies installed.
This checks compatibility and the three contracts, not broad model quality.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import socket
import statistics
from time import perf_counter


@contextmanager
def block_python_network():
    """Block Python socket connection attempts, not an OS firewall assertion."""
    attempts: list[str] = []
    originals = {name: getattr(socket.socket, name) for name in ("connect", "connect_ex")}
    def blocked(*args, **kwargs):
        attempts.append("blocked")
        raise RuntimeError("Network connection attempted during offline verification")
    try:
        for name in originals:
            setattr(socket.socket, name, blocked)
        yield attempts
    finally:
        for name, fn in originals.items():
            setattr(socket.socket, name, fn)


async def verify(device: str, repetitions: int) -> dict:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    import torch
    from decilist import Decilist, LiquidLocalProvider, Policy
    from decilist.contracts import Choose, Predict, Score, validate_decision

    questions = [
        Choose(state="Please summarize the attached document in three bullet points.",
               instructions="Which action is explicitly requested?", options={
                   "summarize": "Summarize a document", "translate": "Translate text", "compare": "Compare documents"}),
        Predict(state="Please translate this paragraph from English into Arabic.",
                instructions="Does the request explicitly specify a target language?"),
        Score(state="The request includes an action, an input document, and a requested output format.",
              instructions="Rate completeness of the request using only the stated fields.",
              levels=["No fields are present", "One or two fields are present", "All three fields are present"]),
    ]
    with block_python_network() as blocked_attempts:
        start = perf_counter()
        provider = LiquidLocalProvider.load(allow_model_code=True, device=device, offline=True)
        load_seconds = perf_counter() - start
        # Compatibility requires valid distributions, not a selected confidence
        # threshold. The separate frozen benchmark measures the normal 0.7 policy.
        engine = Decilist([provider], Policy(min_confidence=0, timeout_ms=120000))
        if device.startswith("cuda"):
            torch.cuda.reset_peak_memory_stats(device)
        start = perf_counter()
        warmup = await engine.decide(questions[0])
        warmup_ms = (perf_counter() - start) * 1000
        if warmup.status != "accepted":
            # Expose the actual local error rather than treating abstention as success.
            await provider.infer(questions[0])
            raise RuntimeError(f"Smoke warmup abstained: {warmup.model_dump_json()}")
        rows = []
        for question in questions:
            results = []
            for _ in range(repetitions):
                decision = await engine.decide(question)
                validate_decision(question, decision)
                if decision.status != "accepted":
                    await provider.infer(question)
                    raise RuntimeError(f"Smoke {question.kind} abstained")
                results.append(decision)
            rows.append({"question": question.model_dump(), "last_result": results[-1].model_dump(mode="json"),
                         "latency_ms": [item.latency_ms for item in results],
                         "median_ms": statistics.median(item.latency_ms for item in results)})
        memory = {"allocated_bytes": torch.cuda.memory_allocated(device),
                  "peak_allocated_bytes": torch.cuda.max_memory_allocated(device),
                  "reserved_bytes": torch.cuda.memory_reserved(device)} if device.startswith("cuda") else None
    return {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "purpose": "Compatibility and typed-contract smoke check; not a quality benchmark",
            "policy": {"local_only": True, "min_confidence": 0, "timeout_ms": 120000},
            "model": provider.model, "revision": provider.revision, "device": device,
            "attention_backend": getattr(provider, "attention_backend", "vendor-default"),
            "platform": platform.system(), "python": platform.python_version(),
            "versions": {key: importlib.metadata.version(key) for key in ("torch", "torchvision", "transformers", "huggingface-hub", "decilist")},
            "cuda_runtime": torch.version.cuda, "gpu": torch.cuda.get_device_name(device) if device.startswith("cuda") else None,
            "cached_load_seconds": load_seconds, "first_inference_ms": warmup_ms,
            "repetitions_per_kind": repetitions, "gpu_memory": memory,
            "offline_check": {"hub_offline": True, "python_socket_connections_blocked": True, "attempt_count": len(blocked_attempts),
                              "scope": "Python socket connect/connect_ex only; not process-level packet capture"}, "results": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    result = asyncio.run(verify(args.device, args.repetitions))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "cached_load_seconds": result["cached_load_seconds"],
                      "medians_ms": {row["question"]["kind"]: row["median_ms"] for row in result["results"]},
                      "blocked_network_attempts": result["offline_check"]["attempt_count"]}))


if __name__ == "__main__":
    main()
