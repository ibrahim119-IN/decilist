"""Machine next-check evaluation on replayed project fixtures, not a human study."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import re
import subprocess
from time import perf_counter
from uuid import uuid4

from decilist import Client
from decilist.triage import ROUTES, TRIAGE_INSTRUCTIONS, diagnose
from decilist.triage_evidence import has_failure_signal, prepare_evidence
from .triage_lab import (BASE_REVISION, LAB_ROOT, REPO_ROOT, digest, get_case,
                         load_catalog, prepare_case_evidence, verify_fixture, verify_freeze)

BASELINE_NAME = "ordered-error-patterns-v1"
# Authored before model evaluation. Order is explicit; this is an automated
# regex comparator, never a claim about the workflow of a human developer.
PATTERNS = [
    ("dependency_environment", r"modulenotfounderror|no module named|importerror|compiled flash attention|cuda.*(?:unavailable|not available)|dll load failed"),
    ("decision_policy", r"(?:policy|confidence).*threshold|low_confidence|inclusive.*threshold|local.only|timeout.*attempt"),
    ("runtime_lifecycle", r"runtimecompatibilityerror|endpoint identity|runtime reuse|health.*(?:version|service)|not a decilist runtime"),
    ("contract_validation", r"validationerror|probabilities must sum|probability sum|invalid_output|json integer.key order|allowed labels|schema version"),
    ("application_logic", r"assertionerror|\bE\s+assert\b|\bFAILED\s+\S"),
]


def canonical_hash(value: object) -> str:
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def diagnosis_contract() -> dict:
    return {"routes": ROUTES, "instructions": TRIAGE_INSTRUCTIONS,
            "policy": load_catalog()["policy"]}


def baseline_route(evidence: dict) -> str:
    if not has_failure_signal(evidence["log"]):
        return "manual_review"
    text = evidence["log"] + "\n" + "\n".join(row["excerpt"] for row in evidence.get("references", []))
    for route, pattern in PATTERNS:
        if re.search(pattern, text, re.I):
            return route
    return "manual_review"


def prepare_fixture_evidence(case: dict, proof: dict, case_dir: Path | None = None) -> dict:
    observed = proof["mutation"]
    if case["evidence_mode"] == "truncated":
        # The actual exit is retained; the deliberately unavailable traceback
        # is an insufficient-evidence control, not a fabricated failure log.
        log = "Execution log was truncated before diagnostic details were retained."
        evidence = prepare_evidence(log, command=observed["command"])
        evidence["truncated"] = True
    else:
        if case_dir is None:
            raise ValueError("retained case directory is required for identical benchmark/study source evidence")
        evidence = prepare_case_evidence(case_dir, observed)
    evidence["exit_code"] = observed["exit_code"]
    return evidence


def summarize_rows(rows: list[dict]) -> dict:
    attempts = [row for row in rows if row["role"] == "regression"]
    # Quality uses the prespecified first observation of each unique fixture;
    # repeats measure timing/stability and are reported separately.
    regressions = [row for row in attempts if row.get("repetition", 1) == 1]
    accepted = [row for row in regressions if row["status"] == "suggested" and row["route"] != "manual_review"]
    correct = [row for row in accepted if row["route"] in row["acceptable_routes"]]
    n = len(regressions)
    return {"regression_cases": n, "accepted_next_checks": len(accepted), "correct_accepted_next_checks": len(correct),
        "coverage": len(accepted)/n if n else None,
        "accuracy_all": len(correct)/n if n else None,
        "accuracy_accepted": len(correct)/len(accepted) if accepted else None,
        "abstention_or_review_rate": (n-len(accepted))/n if n else None,
        "assistance_wall_ms": sum(row["assistance_wall_ms"] for row in attempts),
        "regression_attempts": len(attempts),
        "accepted_attempts": sum(row["status"] == "suggested" and row["route"] != "manual_review" for row in attempts),
        "correct_accepted_attempts": sum(row["status"] == "suggested" and row["route"] in row["acceptable_routes"] and row["route"] != "manual_review" for row in attempts),
        "changed_outcome_cases": [case_id for case_id in sorted({row["case_id"] for row in attempts})
            if len({(row["status"], row["route"]) for row in attempts if row["case_id"] == case_id}) > 1],
        "wrong_accepted": [{"case_id": row["case_id"], "route": row["route"], "acceptable_routes": row["acceptable_routes"]}
                           for row in accepted if row["route"] not in row["acceptable_routes"]],
        "controls": [{"case_id": row["case_id"], "role": row["role"], "status": row["status"], "route": row["route"],
                      "specific_next_check_suggested": row["status"] == "suggested" and row["route"] != "manual_review"}
                     for row in rows if row["role"] != "regression" and row.get("repetition", 1) == 1],
        "human_minutes_saved": None, "cost_usd": None}


def _checked_proof(proof: dict) -> None:
    catalog = load_catalog()
    expected = {case["id"] for case in catalog["cases"]}
    if {row["case_id"] for row in proof["cases"]} != expected or len(proof["cases"]) != len(expected):
        raise ValueError("proof must contain every unique frozen case")
    for row in proof["cases"]:
        if not row["verified"]:
            raise ValueError("unverified fixture cannot be evaluated")
        for run in (row["baseline"], row["mutation"], row["reference_repair"]["run"]):
            if digest(run["stdout"].encode("utf-8")) != run["run_sha256"] or not run["source_stable"]:
                raise ValueError("proof log/source hash mismatch")


async def evaluate_proof(proof: dict, *, provider: str, client: Client | None = None,
                         directories: dict[str, Path] | None = None, repetitions: int = 3) -> dict:
    """Evaluate identical actual evidence; expected routes never enter diagnose."""
    _checked_proof(proof)
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    rows = []
    for repetition, fixture in ((repetition, fixture) for repetition in range(1, repetitions + 1) for fixture in proof["cases"]):
        case = get_case(fixture["case_id"])
        directory = (directories or {}).get(case["id"])
        evidence = prepare_fixture_evidence(case, fixture, directory)
        start = perf_counter()
        raw = None
        if provider == "baseline":
            route = baseline_route(evidence)
            status = "needs_review" if route == "manual_review" else "suggested"
        elif provider == "liquid":
            try:
                raw = await diagnose(evidence, client=client)
            except ValueError:
                if case["role"] != "passing_control" or has_failure_signal(evidence["log"]):
                    raise
                raw = {"status": "no_failure", "route": "manual_review", "decision": None,
                       "message": "Passing control rejected before model inference."}
            route, status = raw["route"], raw["status"]
            decision = raw.get("decision")
            if decision is not None and decision.get("status") == "accepted" and decision.get("provider") != "liquid-local":
                raise ValueError("the requested Liquid comparison returned another provider")
        else:
            raise ValueError("unsupported provider")
        elapsed = (perf_counter()-start)*1000
        rows.append({"case_id": case["id"], "repetition": repetition, "split": case["split"], "role": case["role"],
            "provenance": case["provenance"], "acceptable_routes": case["acceptable_routes"],
            "status": status, "route": route, "assistance_wall_ms": elapsed,
            "input_sha256": canonical_hash(evidence), "evidence": evidence,
            "output": raw if provider == "liquid" else {"name": BASELINE_NAME, "route": route,
                "confidence": None, "rule_patterns": PATTERNS},
            "mutation_log_sha256": fixture["mutation"]["run_sha256"]})
    return {"provider": BASELINE_NAME if provider == "baseline" else "existing-local-liquid-runtime",
        "repetitions": repetitions, "unique_cases": len(proof["cases"]), "attempts": len(rows),
        "splits": {split: summarize_rows([row for row in rows if row["split"] == split]) for split in ("dev", "evaluation")},
        "results": rows}


async def run(args: argparse.Namespace) -> dict:
    freeze = verify_freeze()
    if freeze["diagnosis_contract_sha256"] != canonical_hash(diagnosis_contract()):
        raise ValueError("frozen diagnosis instructions/routes/policy changed")
    if args.provider in {"liquid", "both"} and not args.allow_model_evaluation:
        raise ValueError("actual model evaluation requires explicit --allow-model-evaluation after the frozen source is reviewed")
    output = Path(args.output)
    if output.exists():
        raise ValueError("output already exists; preserve previous evidence with a new filename")
    directories = {}
    if args.proof:
        proof_path = Path(args.proof)
        proof = json.loads(proof_path.read_text(encoding="utf-8"))
        directories = {case["id"]: proof_path.parent / case["id"] for case in load_catalog()["cases"]}
    else:
        root = LAB_ROOT / ("machine-" + uuid4().hex[:12])
        proof = {"suite": "decilist-triage-v1", "cases": []}
        for case in load_catalog()["cases"]:
            directories[case["id"]] = root / case["id"]
            fixture = verify_fixture(case["id"], directories[case["id"]])
            proof["cases"].append(fixture)
            print(json.dumps({"fixture": case["id"], "verified": fixture["verified"]}), flush=True)
        proof_path = root / "proof.json"
        proof_path.write_bytes((json.dumps(proof, indent=2) + "\n").encode("utf-8"))
    _checked_proof(proof)
    report = {"suite": "decilist-triage-v1", "measured_at": datetime.now(timezone.utc).isoformat(),
        "base_revision": BASE_REVISION, "freeze": freeze, "diagnosis_contract_sha256": canonical_hash(diagnosis_contract()),
        "fixture_proof_sha256": digest(proof_path.read_bytes()), "python": platform.python_version(),
        "platform": platform.platform(), "runs": {},
        "limitations": ["Ten authored fixtures in one real project: historical reverts, clearly seeded bugs, and two controls; no representative or independently labeled benchmark claim.",
            "Controls are separate from regression accuracy, coverage, and wrong-accepted counts. Multiple acceptable next checks were frozen before model outputs.",
            "The comparator is an ordered automated error-pattern baseline, not a human developer's usual workflow.",
            "Quality denominators use the first observation of each unique case. Repeated calls are separate timing/stability observations, not independent examples.",
            "Assistance wall time measures only routing calls. It excludes project repair, human effort, setup, model loading, and test execution; no human minutes-saved claim.",
            "The existing local runtime owns model loading; no model is downloaded or silently substituted. Probability thresholds are uncalibrated.",
            "No model-generated repair is applied. Reference repairs are source-only corpus checks and never enter model or participant evidence."],
        "unmeasured": {"human_time_saved": None, "model_load_ms": None, "cost_usd": None, "calibration": None, "network_observed": None}}
    try:
        report["source_revision"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.strip()
        report["source_dirty"] = bool(subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        report.update(source_revision=None, source_dirty=None)
    providers = ("baseline", "liquid") if args.provider == "both" else (args.provider,)
    client = Client(args.url) if "liquid" in providers else None
    try:
        for provider in providers:
            report["runs"][provider] = await evaluate_proof(proof, provider=provider, client=client, directories=directories, repetitions=args.repetitions)
    finally:
        if client is not None:
            await client.aclose()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps({name: run["splits"] for name, run in report["runs"].items()}, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("baseline", "liquid", "both"), default="baseline")
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--allow-model-evaluation", action="store_true")
    parser.add_argument("--proof", help="Reuse verified local proof JSON with its retained sibling case folders")
    parser.add_argument("--repetitions", type=int, default=3, help="Timing/stability repetitions; quality uses unique first observations")
    parser.add_argument("--output", default="reports/local/triage-machine.json")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
