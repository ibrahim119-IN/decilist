"""Exercise command entry points without starting a runtime or downloading models."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "packages" / "python" / "src"


def invoke(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(SOURCE), environment.get("PYTHONPATH", "")) if part
    )
    return subprocess.run(
        [sys.executable, *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_module_runtime_help_preserves_existing_command():
    completed = invoke("-m", "decilist", "runtime", "--help")
    assert completed.returncode == 0, completed.stderr
    assert "{start,run,status,setup}" in completed.stdout


def test_module_triage_help_exposes_saved_log_interface():
    completed = invoke("-m", "decilist", "triage", "--help")
    assert completed.returncode == 0, completed.stderr
    for option in ("--log", "--project", "--mode", "--format", "--output"):
        assert option in completed.stdout


def test_top_level_help_includes_both_commands():
    completed = invoke("-m", "decilist", "--help")
    assert completed.returncode == 0, completed.stderr
    assert "runtime" in completed.stdout
    assert "triage" in completed.stdout


def test_runtime_arguments_after_separator_are_forwarded_unchanged():
    arguments = [
        "runtime", "run", "--provider", "demo", "--", "python", "-c",
        "print('two words')", "--application-option", "two words", "triage",
    ]
    script = """
import json
import sys
from decilist.cli import main
import decilist.runtime as runtime

def record(arguments):
    print(json.dumps(arguments))
    return 17

runtime.main = record
raise SystemExit(main(sys.argv[1:]))
"""
    completed = invoke("-c", script, *arguments)
    assert completed.returncode == 17, completed.stderr
    assert json.loads(completed.stdout) == arguments


def test_triage_arguments_are_forwarded_without_reparsing():
    arguments = ["--log", "failure with spaces.log", "--project", ".", "--", "--sentinel"]
    script = """
import json
import sys
import types
from decilist.cli import main

helper = types.ModuleType('decilist.triage_assistant')
def record(arguments):
    print(json.dumps(arguments))
    return 19
helper.main = record
sys.modules[helper.__name__] = helper
raise SystemExit(main(sys.argv[1:]))
"""
    completed = invoke("-c", script, "triage", *arguments)
    assert completed.returncode == 19, completed.stderr
    assert json.loads(completed.stdout) == arguments


def test_runtime_and_top_level_help_do_not_import_triage():
    script = """
import sys
from decilist.cli import main
assert 'decilist.triage_assistant' not in sys.modules
try:
    main(['runtime', '--help'])
except SystemExit as result:
    assert result.code == 0
assert 'decilist.triage_assistant' not in sys.modules
assert main(['--help']) == 0
assert 'decilist.triage_assistant' not in sys.modules
"""
    completed = invoke("-c", script)
    assert completed.returncode == 0, completed.stderr
