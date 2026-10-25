import argparse
import asyncio
import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from decilist import Choose, Decilist, FunctionProvider, LiquidLocalProvider, Policy, UniformProvider
from decilist.evaluation import Case, evaluate
from examples.workflow import INSTRUCTIONS, OPTIONS, rules


async def run(args):
    dataset = Path(__file__).with_name("workflow.json")
    rows = json.loads(dataset.read_text())
    cases = [Case(row["id"], Choose(state=json.dumps(row["state"], sort_keys=True), instructions=INSTRUCTIONS, options=OPTIONS), row["expected"]) for row in rows]
    engines = {
        "uniform": Decilist([UniformProvider()], Policy(min_confidence=0)),
        "rules": Decilist([FunctionProvider(rules, name="workflow-rules")]),
        "policy-fallback": Decilist([UniformProvider(), FunctionProvider(rules, name="workflow-rules")]),
    }
    if args.provider == "liquid":
        engines = {"liquid": Decilist([LiquidLocalProvider.load(allow_model_code=args.allow_model_code)], Policy(timeout_ms=30000))}
    report = {"suite": "workflow-smoke-v1", "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
              "measured_at": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(), "platform": platform.system(),
              "limitations": "12 synthetic smoke cases. Rules encode the labeling policy; this is not evidence of learned model quality or production performance. Sequential warm calls; model load and one warmup excluded. Cost unmeasured.", "runs": {}}
    for name, engine in engines.items():
        await engine.decide(cases[0].question)
        report["runs"][name] = {"providers": [{"name": p.name, "model": p.model, "revision": getattr(p, "revision", None)} for p in engine.providers], "policy": engine.policy.model_dump(), **await evaluate(engine, cases)}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v["metrics"] for k,v in report["runs"].items()}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["baselines", "liquid"], default="baselines")
    parser.add_argument("--allow-model-code", action="store_true")
    parser.add_argument("--output", default="reports/local/workflow.json")
    asyncio.run(run(parser.parse_args()))
