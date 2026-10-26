"""Exploratory post-evaluation replay: replace only the failing test excerpt.

This is not a fresh holdout and does not alter the frozen primary experiment.
Run after timed repair participants finish to avoid model queue interference.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
from time import perf_counter

from decilist import triage_diagnostic, triage_v3
from decilist.triage_evidence import _sanitize, _project_pattern
from . import run_triage_v3 as matrix


async def run(args):
    output = Path(args.output)
    private = Path(args.private_output)
    if output.exists() or private.exists():
        raise ValueError("Preserve previous replay observations; choose new paths")
    config_path = Path(args.model_config)
    candidate = matrix.verify_candidate(matrix.ROOT / "benchmarks/triage_v3_assistant_freeze.json",
        config_path, liquid_url="http://127.0.0.1:8765", qwen_url="http://127.0.0.1:8792")
    primary_path = Path(args.primary_report)
    primary = json.loads(primary_path.read_text(encoding="utf-8"))
    manifest_sha = matrix.sha((matrix.ROOT / "benchmarks/triage_v3_assistant_freeze.json").read_bytes())
    if not primary["valid_frozen_comparison"] or primary["candidate_manifest_sha256"] != manifest_sha:
        raise ValueError("Expected intact frozen primary evaluation")
    raw_bytes = Path(args.primary_raw_trace).read_bytes()
    if matrix.sha(raw_bytes) != primary["private_trace_record"]["compressed_sha256"]:
        raise ValueError("Primary raw trace hash mismatch")
    decoded = gzip.decompress(raw_bytes)
    if matrix.sha(decoded) != primary["private_trace_record"]["decoded_json_sha256"]:
        raise ValueError("Primary decoded trace hash mismatch")
    original = json.loads(decoded)["report"]
    rows = [row for row in original["rows"] if row["cell"] == "qwen_diagnostic" and row["repetition"] == 1]
    if [row["case_id"] for row in rows] != ["h02", "h03", "h04", "h06", "h07", "h08"]:
        raise ValueError("Replay all six evaluated cases without selection")
    sources_before = matrix._source_hashes()
    preparations = {row["case_id"]: row for row in primary["preparations"]}
    result_rows = []
    config = triage_v3.load_qwen_config(config_path)
    async with matrix.TracedQwen(base_url="http://127.0.0.1:8792", **config) as provider:
        matrix._check_ready("qwen", await provider.ready(), candidate, config)
        for row in rows:
            case_id = row["case_id"]
            project = Path(args.proof_directory) / case_id
            expected_sources = preparations[case_id]["editable_source_sha256"]
            if {name: matrix.sha((project/name).read_bytes()) for name in expected_sources} != expected_sources:
                raise ValueError("Case production no longer matches primary observation")
            plan = triage_v3.prepare_plan(row["input_evidence"], project=project, design="action")
            original_payload = matrix._payload(plan, diagnostic=True)
            if matrix.fingerprint(original_payload) != row["input_payload_sha256"]:
                raise ValueError("Reconstructed primary input differs; do not silently change the comparison")
            plan = deepcopy(plan)
            refs = plan["inspected"]["facts"]["source_references"]
            test_refs = [ref for ref in refs if ref["path"] == ".triage/test_case.py"]
            if len(test_refs) != 1:
                raise ValueError("Expected exactly one test reference")
            test_path = project / ".triage/test_case.py"
            data = test_path.read_bytes()
            if len(data) > 16000:
                raise ValueError("Full test exceeds this exploratory replay bound")
            text, redactions = _sanitize(data.decode("utf-8"), _project_pattern(project.resolve()))
            excerpt = "\n".join(f"{index} | {line}" for index, line in enumerate(text.splitlines(), 1))
            original_excerpt = test_refs[0]["excerpt"]
            test_refs[0].update(line=1, excerpt=excerpt)
            payload = matrix._payload(plan, diagnostic=True)
            # Assert the sole prompt change: one test source entry's excerpt and line.
            old_user = json.loads(original_payload["messages"][1]["content"])
            new_user = json.loads(payload["messages"][1]["content"])
            restored = deepcopy(new_user)
            for old_ref, new_ref in zip(old_user["source_catalog"], restored["source_catalog"], strict=True):
                if old_ref["path"] == ".triage/test_case.py":
                    new_ref.update(line=old_ref["line"], excerpt=old_ref["excerpt"])
            if restored != old_user or original_payload["messages"][0] != payload["messages"][0]:
                raise ValueError("Unexpected prompt change beyond full test context")
            tick = perf_counter()
            report = await triage_diagnostic.diagnose_plan(plan, provider=provider)
            result_rows.append({"case_id": case_id, "elapsed_ms": (perf_counter()-tick)*1000,
                "full_test_sha256": matrix.sha(data), "test_redactions": redactions,
                "original_test_excerpt": original_excerpt, "full_test_excerpt": excerpt,
                "primary_input_payload_sha256": row["input_payload_sha256"],
                "input_payload": payload, "input_payload_sha256": matrix.fingerprint(payload),
                "only_test_excerpt_and_line_changed": True, "report": report})
            print(json.dumps({"case_id": case_id, "status": report["status"], "hypothesis": report.get("hypothesis")} ), flush=True)
        traces = provider.traces
    sources_after = matrix._source_hashes()
    if sources_before != sources_after:
        raise ValueError("Frozen source drift during replay")
    result = {"suite": "triage-v3-full-test-context-replay", "fresh_holdout": False,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "primary_report_sha256": matrix.sha(primary_path.read_bytes()),
        "candidate_manifest_sha256": primary["candidate_manifest_sha256"],
        "script_sha256": matrix.sha(Path(__file__).read_bytes()),
        "sources_stable": True, "rows": result_rows,
        "limitations": ["Exploratory replay after inspecting all primary outcomes; not a new holdout.",
            "Same model, system instruction, schema and generation settings; only one test excerpt is expanded.",
            "Implementation excerpts, extracted facts and failure rendering are unchanged.",
            "No labels, repair patches or oracle facts are included in model inputs.",
            "One attempt per case; causal judgments are separate from valid citations.",
            "Not a shipped evidence-policy change or a human debugging-speed measurement."]}
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_bytes(gzip.compress(matrix._json_bytes({"report": result, "decoded_transport_traces": traces})))
    public = matrix._public(result)
    public["private_trace_sha256"] = matrix.sha(private.read_bytes())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(matrix._json_bytes(public))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary-report", required=True)
    parser.add_argument("--primary-raw-trace", required=True)
    parser.add_argument("--proof-directory", required=True)
    parser.add_argument("--model-config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--private-output", required=True)
    asyncio.run(run(parser.parse_args()))
