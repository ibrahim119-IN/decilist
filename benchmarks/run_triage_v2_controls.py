"""Run the three frozen v2 controls against the same frozen assistant candidate.

This dedicated entry point verifies the existing candidate, then calls the
original evaluator with split='controls'. Its output keeps controls separate
from regression quality and retains the original variants, timing repetitions,
saved proof, local runtime URL, and exclusive output-file behavior.
"""
from __future__ import annotations

import argparse
import asyncio

from .run_triage_v2 import VARIANTS, run, verify_candidate


async def run_controls(args: argparse.Namespace) -> dict:
    verify_candidate()
    selected = argparse.Namespace(**{**vars(args), "split": "controls"})
    return await run(selected)


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proof", required=True)
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=list(VARIANTS))
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", default="reports/local/triage-v2-controls.json")
    return asyncio.run(run_controls(parser.parse_args(argv)))


if __name__ == "__main__":
    main()
