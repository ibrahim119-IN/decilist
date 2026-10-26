"""Small command dispatcher; explicit helpers are imported lazily."""
from __future__ import annotations

import argparse
import sys


def _help() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="decilist",
        description="Local Decilist decisions, failure triage, and test debugging.",
    )
    parser.add_argument("--version", action="store_true", help="Show the installed package version")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("runtime", help="Set up, start, or inspect the local runtime")
    commands.add_parser("triage", help="Inspect a saved failure log and suggest the next check")
    commands.add_parser("debug", help="Debug an existing test in a disposable project copy")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Route explicit helpers and preserve the runtime's complete argument list."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["debug"]:
        from .triage_lines_v6 import main as debug_main

        return debug_main(arguments[1:])
    if arguments[:1] == ["triage"]:
        from .triage_assistant import main as triage_main

        return triage_main(arguments[1:])
    if arguments in (["--help"], ["-h"]):
        _help().print_help()
        return 0

    # Let the existing runtime parser own its options, errors, and application
    # separator. In particular, never parse or consume arguments after -- here.
    from .runtime import main as runtime_main

    return runtime_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
