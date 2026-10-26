"""V5 collector bounds and standalone pytest loading."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from decilist.triage_trace_v5 import TraceRecorder


def test_prefix_recent_function_capacity_preserves_late_callee_and_active_caller(tmp_path):
    path = tmp_path / "application.py"
    source = "".join(f"def setup{index}():\n    return {index}\n" for index in range(90))
    source += "FUNCTIONS = [" + ",".join(f"setup{index}" for index in range(90)) + "]\n"
    source += "def validate(record):\n    return record['value']\ndef run():\n    for function in FUNCTIONS:\n        function()\n    return validate({'value': 1})\n"
    path.write_text(source, encoding="utf-8")
    namespace = {}
    exec(compile(source, str(path), "exec"), namespace)
    recorder = TraceRecorder(tmp_path, max_functions=32)
    recorder.nodeid, recorder.phase = "tests/test_app.py::test_app", "call"
    recorder.start()
    try:
        assert namespace["run"]() == 1
    finally:
        recorder.stop()
    trace = recorder.as_dict()
    rows = {row["symbol"]: row for row in trace["functions"]}
    assert len(rows) <= 32 and recorder.function_evictions > 0
    assert "run" in rows and "validate" in rows
    assert any(edge["caller"] == rows["run"]["id"] and edge["callee"] == rows["validate"]["id"] for edge in trace["edges"])
    assert trace["coverage"]["function_omission_examples"]


def test_long_nodeid_output_clipping_is_bounded_and_retains_recent_evidence(tmp_path):
    path = tmp_path / "application.py"
    path.write_text("def exercise():\n    for value in range(3000):\n        total = value + 1\n    raise ValueError('dynamic-' + 'private-value')\n", encoding="utf-8")
    namespace = {}
    exec(compile(path.read_text(), str(path), "exec"), namespace)
    recorder = TraceRecorder(tmp_path, max_output_chars=24000)
    recorder.nodeid, recorder.phase = "tests/test_app.py::test_app[" + "x" * 900 + "]", "call"
    recorder.start()
    try:
        with pytest.raises(ValueError):
            namespace["exercise"]()
    finally:
        recorder.stop()
    started = time.perf_counter()
    trace = recorder.as_dict()
    elapsed = time.perf_counter() - started
    print(f"V5 stress serialization: {elapsed:.4f}s for 2000 bounded events")
    assert len(json.dumps(trace)) <= 24000
    assert len(trace["events"]) <= 2000
    assert trace["coverage"]["output_events_omitted"] > 0
    assert any(event.get("exception_type") == "ValueError" for event in trace["events"])
    assert "dynamic-private-value" not in json.dumps(trace)
    assert trace["events"] == sorted(trace["events"], key=lambda event: event["seq"])


def test_plugin_loads_by_file_without_importing_snapshot_decilist(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "decilist").mkdir()
    (tmp_path / "decilist/__init__.py").write_text("raise RuntimeError('DO_NOT_IMPORT')\n")
    (tmp_path / "tests/test_failure.py").write_text("def test_failure():\n    assert False\n")
    output = tmp_path / "trace.json"
    plugin = Path(__file__).resolve().parents[1] / "src/decilist/triage_trace_v5.py"
    script = "import importlib.util,sys,pytest\nspec=importlib.util.spec_from_file_location('owned_v5_trace',sys.argv[1])\nmodule=importlib.util.module_from_spec(spec)\nsys.modules[spec.name]=module\nspec.loader.exec_module(module)\nraise SystemExit(pytest.main(['-q','tests'],plugins=[module]))\n"
    env = dict(os.environ, DECILIST_TRACE_ROOT=str(tmp_path), DECILIST_TRACE_OUTPUT=str(output),
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONPATH=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("DECILIST_TRACE_FAILURE_TEXT", None)
    result = subprocess.run([sys.executable, "-c", script, str(plugin)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 1, result.stdout + result.stderr
    trace = json.loads(output.read_text())
    assert trace["schema_version"] == 5 and trace["failures"][0]["exception_type"] == "AssertionError"


def test_symlink_and_site_packages_are_not_trace_candidates(tmp_path):
    external = tmp_path.parent / (tmp_path.name + "-outside.py")
    external.write_text("def outside():\n    return 1\n")
    link = tmp_path / "linked.py"
    try:
        link.symlink_to(external)
    except OSError:
        pytest.skip("Host cannot create a test symlink")
    code = compile(external.read_text(), str(link), "exec").co_consts[0]
    assert TraceRecorder(tmp_path)._function(code) is None
    assert TraceRecorder(tmp_path)._function(json.dumps.__code__) is None
