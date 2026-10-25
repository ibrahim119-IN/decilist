"""Add read-only triage to an existing app with a saved test/CI failure log.

Run after installing the Python SDK:
    python -m examples.triage_in_existing_app --log failure.log --project .

Use --mode rules for a model-free pass. In auto mode, only evidence without a
direct rules result can reach an already running learned local runtime.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from decilist.triage_assistant import diagnose
from decilist.triage_evidence import prepare_evidence


async def inspect_saved_failure(log: str, project: Path, *, mode: str = "auto") -> dict:
    """Call this from an app's existing failure-handling path; it runs no repairs."""
    evidence = prepare_evidence(log, project=project)
    return await diagnose(evidence, project=project, mode=mode)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--project", type=Path, default=Path("."))
    parser.add_argument("--mode", choices=["auto", "rules", "model"], default="auto")
    args = parser.parse_args()
    try:
        with args.log.open(encoding="utf-8", errors="replace") as saved:
            log = saved.read(1_048_577)
        if len(log) > 1_048_576:
            raise ValueError("Saved log exceeds 1 MiB; provide a smaller failure excerpt")
        report = asyncio.run(inspect_saved_failure(log, args.project, mode=args.mode))
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Triage: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report["status"] == "suggested" else 1


if __name__ == "__main__":
    raise SystemExit(main())
