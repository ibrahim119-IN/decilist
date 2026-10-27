"""Run the frozen authored pilot against the base and specialist local weights.

This measures target-rule classification and runtime, not developer time saved.
No model download, code execution, or external API requests take place here.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import gc
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
from time import perf_counter

from decilist.code_review import CodeReviewer, LiquidCodeReviewProvider, ReviewPolicy
from decilist.providers import LiquidLocalProvider


class BaseReviewer(LiquidCodeReviewProvider):
    model = LiquidLocalProvider.model
    revision = LiquidLocalProvider.revision


async def measure(args):
    os.environ["HF_HUB_OFFLINE"] = os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from verify_liquid import block_python_network
    raw = args.cases.read_bytes()
    cases = json.loads(raw)["cases"]
    results = {}
    with block_python_network() as attempts:
        for label, provider_type in (("base", BaseReviewer), ("specialist", LiquidCodeReviewProvider)):
            if label not in args.models:
                continue
            began = perf_counter()
            provider = provider_type.load(allow_model_code=True, device=args.device, offline=True)
            loaded = perf_counter() - began
            reviewer = CodeReviewer(provider, ReviewPolicy(threshold=args.threshold))
            first = await reviewer.review("export const enabled = true;", task="Export a feature flag.")
            if first.status == "unavailable":
                raise RuntimeError(f"{label} warmup failed: {first.error}")
            rows = []
            for case in cases:
                result = await reviewer.review(case["code"], task=case["task"])
                p = result.windows[0].probabilities[case["target_rule"]] if result.status != "unavailable" else None
                rows.append({"id": case["id"], "target_rule": case["target_rule"], "expected": case["expected"],
                             "predicted": None if p is None else p >= args.threshold, "probability": p,
                             "result": result.model_dump(mode="json")})
                print(json.dumps({"model": label, "case": case["id"], "p": p}), flush=True)
            valid = [r for r in rows if r["predicted"] is not None]
            tp = sum(r["expected"] and r["predicted"] for r in valid)
            tn = sum(not r["expected"] and not r["predicted"] for r in valid)
            fp = sum(not r["expected"] and r["predicted"] for r in valid)
            fn = sum(r["expected"] and not r["predicted"] for r in valid)
            results[label] = {"model": provider.model, "revision": provider.revision,
                              "attention_backend": provider.attention_backend, "load_seconds": loaded,
                              "warmup_ms": first.latency_ms, "threshold": args.threshold,
                              "metrics": {"cases": len(cases), "unavailable": len(rows)-len(valid), "tp":tp,"tn":tn,"fp":fp,"fn":fn,
                                          "target_accuracy_all_cases":(tp+tn)/len(rows),
                                          "target_f1":2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0,
                                          "median_ms": statistics.median(r["result"]["latency_ms"] for r in rows)}, "rows": rows}
            del reviewer, provider
            gc.collect()
            torch.cuda.empty_cache() if args.device.startswith("cuda") else None
    return {"schema_version": "code-review-pilot.1", "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "purpose": "18 newly authored cases scored only on their target rule; not independent human labels, natural bugs, or a developer time study.",
            "cases_sha256": hashlib.sha256(raw).hexdigest(), "device":args.device,
            "gpu":torch.cuda.get_device_name(args.device) if args.device.startswith("cuda") else None,
            "python_socket_connections_blocked":True,"blocked_connection_attempts":len(attempts),"results":results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("code_review_cases.json"))
    parser.add_argument("--device", default="cuda:1")
    parser.add_argument("--threshold", type=float, default=0.9)
    parser.add_argument("--models", nargs="+", choices=["base", "specialist"], default=["base", "specialist"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(measure(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({name: result["metrics"] for name,result in report["results"].items()}))


if __name__ == "__main__":
    main()
