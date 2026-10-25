"""Bounded text benchmark: run from the repository root, after explicit model setup."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from time import perf_counter

from decilist import Decilist, LiquidLocalProvider, Policy, UniformProvider
from decilist.contracts import question_adapter
from decilist.evaluation import Case, summarize, validate_cases

HERE = Path(__file__).resolve().parent
DATASET = HERE / "text_requests.json"
POLICY = HERE / "text_policy.json"
FREEZE = HERE / "text_freeze.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_document(document: dict) -> list[tuple[dict, Case]]:
    """Validate authored labels, fixed split sizes, and paraphrase isolation."""
    if document.get("suite") != "request-text-v1" or document.get("version") != 1:
        raise ValueError("unsupported dataset/version")
    definitions = document["definitions"]
    if set(definitions) != {"choose", "predict", "score"}:
        raise ValueError("dataset needs all three operation definitions")
    common = document["common_instructions"]
    if not isinstance(common, str) or not common.strip():
        raise ValueError("common instructions must be nonempty")
    rows = document["cases"]
    if Counter(row["split"] for row in rows) != {"dev": 20, "heldout": 40}:
        raise ValueError("frozen dataset requires dev20 and heldout40")
    pairs = []
    for row in rows:
        operation = row["operation"]
        if operation not in definitions:
            raise ValueError("unknown operation")
        if not isinstance(row.get("rationale"), str) or not row["rationale"].strip():
            raise ValueError("every label needs an authored rationale")
        if not isinstance(row.get("tags"), list) or not row["tags"] or any(not isinstance(t, str) or not t for t in row["tags"]):
            raise ValueError("every case needs nonempty string tags")
        definition = definitions[operation]
        question = question_adapter.validate_python({
            "kind": operation, "state": row["state"],
            **definition, "instructions": common + "\n\n" + definition["instructions"],
        })
        pairs.append((row, Case(row["id"], question, row["expected"])))
    validate_cases([case for _, case in pairs])
    by_id = {row["id"]: row for row in rows}
    for row in rows:
        parent = row.get("paraphrase_of")
        if parent is not None:
            if parent not in by_id or parent == row["id"]:
                raise ValueError("invalid paraphrase reference")
            reference = by_id[parent]
            if any(row[key] != reference[key] for key in ("split", "operation", "expected")):
                raise ValueError("paraphrases must share split, operation, and ground truth")
    for split in ("dev", "heldout"):
        if {row["operation"] for row in rows if row["split"] == split} != set(definitions):
            raise ValueError("each split needs every operation")
    return pairs


def load_frozen_dataset() -> tuple[dict, list[tuple[dict, Case]], dict, Policy]:
    document = json.loads(DATASET.read_text(encoding="utf-8"))
    pairs = validate_document(document)
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    if sha256(DATASET) != freeze["dataset_sha256"] or sha256(POLICY) != freeze["policy_sha256"]:
        raise ValueError("dataset/policy differs from frozen manifest; version changes require disclosure")
    split_ids = {split: [row["id"] for row, _ in pairs if row["split"] == split] for split in ("dev", "heldout")}
    if split_ids != freeze["split_ids"]:
        raise ValueError("case membership differs from frozen split")
    policy = Policy.model_validate(json.loads(POLICY.read_text(encoding="utf-8")))
    return document, pairs, freeze, policy


def runtime_metadata() -> dict:
    packages = {}
    for name in ("decilist", "pydantic", "torch", "transformers", "huggingface-hub", "numpy"):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    result = {"python": sys.version, "executable": Path(sys.executable).name, "platform": platform.platform(),
              "machine": platform.machine(), "cpu": platform.processor() or None,
              "logical_cpus": os.cpu_count(), "packages": packages,
              "gpu": None, "cuda_runtime": None, "gpu_peak_allocated_bytes": None,
              "process_rss_bytes": None, "process_peak_rss_bytes": None,
              "memory_measurement": "Current process RSS and peak when psutil exposes it; CUDA allocator peak excludes non-PyTorch allocations."}
    try:
        import psutil
        memory = psutil.Process().memory_info()
        result.update(process_rss_bytes=memory.rss, process_peak_rss_bytes=getattr(memory, "peak_wset", None))
    except (ImportError, OSError):
        pass
    torch = sys.modules.get("torch")
    if torch is not None:
        result["cuda_runtime"] = torch.version.cuda
        if torch.cuda.is_available():
            result["gpu"] = [{"name": torch.cuda.get_device_name(i), "total_memory_bytes": torch.cuda.get_device_properties(i).total_memory}
                             for i in range(torch.cuda.device_count())]
            result["gpu_peak_allocated_bytes"] = [torch.cuda.max_memory_allocated(i) for i in range(torch.cuda.device_count())]
    for key, command in (("git_revision", ["git", "rev-parse", "HEAD"]), ("git_dirty", ["git", "status", "--porcelain"])):
        try:
            output = subprocess.run(command, cwd=HERE.parent, capture_output=True, text=True, timeout=5, check=True).stdout.strip()
            result[key] = bool(output) if key == "git_dirty" else output
        except (OSError, subprocess.SubprocessError):
            result[key] = None
    return result


async def run_split(engine: Decilist, pairs: list[tuple[dict, Case]], repetitions: int) -> dict:
    repeated_cases, decisions, records = [], [], []
    for repetition in range(1, repetitions + 1):
        for row, case in pairs:
            result = await engine.decide(case.question)
            repeated_cases.append(replace(case, id=f"{case.id}:r{repetition}"))
            decisions.append(result)
            records.append({"id": case.id, "repetition": repetition, "tags": row["tags"],
                            "expected": case.expected, "result": result.model_dump()})
            if len(records) % 10 == 0:
                print(json.dumps({"completed_calls": len(records), "total_calls": len(pairs) * repetitions,
                                  "last_case": case.id, "status": result.status}), flush=True)
    metrics = summarize(repeated_cases, decisions)
    by_operation = {}
    for operation in ("choose", "predict", "score"):
        indices = [i for i, case in enumerate(repeated_cases) if case.question.kind == operation]
        by_operation[operation] = summarize([repeated_cases[i] for i in indices], [decisions[i] for i in indices])
    return {"unique_cases": len(pairs), "repetitions": repetitions, "metrics": metrics,
            "by_operation": by_operation, "results": records}


async def run(args: argparse.Namespace) -> dict:
    if args.repetitions < 1 or args.warmup < 0:
        raise ValueError("repetitions must be positive and warmup nonnegative")
    document, pairs, freeze, policy = load_frozen_dataset()
    output = Path(args.output)
    if output.exists():
        raise ValueError("output already exists; choose a new path to preserve previous evidence")
    tick = perf_counter()
    if args.provider == "liquid":
        provider = LiquidLocalProvider.load(allow_model_code=args.allow_model_code, device=args.device, offline=True)
    else:
        provider = UniformProvider()
    load_ms = (perf_counter() - tick) * 1000
    engine = Decilist([provider], policy)
    report = {
        "suite": document["suite"], "measured_at": datetime.now(timezone.utc).isoformat(),
        "purpose": document["purpose"], "authorship": document["authorship"],
        "dataset": {"path": DATASET.relative_to(HERE.parent).as_posix(), "sha256": sha256(DATASET), "freeze": freeze},
        "providers": [{"name": provider.name, "model": provider.model, "revision": getattr(provider, "revision", None), "device_requested": args.device if args.provider == "liquid" else None}],
        "policy": policy.model_dump(), "policy_sha256": sha256(POLICY),
        "cold_provider_load_ms": load_ms, "warmup": [], "splits": {},
        "unsupported_baselines": {"workflow-rules": "Rules only accept the original complete/valid/transient state workflow. They do not implement this text task, so no numeric comparison is reported."},
        "unmeasured": {"cost_usd": None, "energy_joules": None, "network_observed": None, "system_memory_peak_bytes": None,
                       "confidence_calibration": None, "concurrent_throughput": None},
        "limitations": [
            "Synthetic authored English text, one narrow rubric, no independent human label review; not evidence of general model superiority or production readiness.",
            "Fixed policy was chosen before model results and is not calibrated. A probability threshold does not guarantee correctness.",
            "Sequential request latency includes engine validation, thread scheduling and policy. Nearest-rank p50/p95 pools stated repetitions; these are not production tail latency claims.",
            "Provider loading and separate warmup are excluded from scored latency. Process/filesystem caches are not flushed, so this is process-level loading rather than a clean-machine cold start.",
            "Native inference cannot be cancelled by the soft timeout; a timed-out call may continue and cause later lock-wait timeouts. Attempt outcomes remain visible.",
            "Offline loading is requested, but network activity is unobserved by this runner; null is not evidence of zero network access.",
            "Uniform probabilities are deterministic and ties use the first label; this baseline is not random sampling.",
        ],
    }
    # Fresh inputs outside both splits; no held-out label is used for warmup.
    for index in range(args.warmup):
        operation = ("choose", "predict", "score")[index % 3]
        definition = document["definitions"][operation]
        question = question_adapter.validate_python({"kind": operation, **definition,
            "instructions": document["common_instructions"] + "\n\n" + definition["instructions"],
            "state": "Request: Proofread for a note. Source text: The welcome desk are open."})
        result = await engine.decide(question)
        report["warmup"].append({"operation": operation, "result": result.model_dump()})
    splits = ("dev", "heldout") if args.split == "all" else (args.split,)
    for split in splits:
        selected = [(row, case) for row, case in pairs if row["split"] == split]
        report["splits"][split] = await run_split(engine, selected, args.repetitions)
        print(json.dumps({"completed_split": split, "metrics": {key: value for key, value in report["splits"][split]["metrics"].items() if key != "errors"}}), flush=True)
    report["runtime"] = runtime_metadata()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also protects evidence if a competing process chose the same path.
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write("\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("uniform", "liquid"), default="uniform")
    parser.add_argument("--allow-model-code", action="store_true", help="Explicitly permit already reviewed pinned vendor code")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--split", choices=("dev", "heldout", "all"), default="all")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--output", default="reports/local/request-text-uniform.json")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
