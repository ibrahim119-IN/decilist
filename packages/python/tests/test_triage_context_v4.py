"""V4 source selection is verified against actual, isolated pytest execution."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from decilist.triage_context_v4 import build_context
from decilist.triage_trace_v4 import TraceRecorder


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    return root


def write(project, path, text):
    target = project / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def run_trace(project, *, trusted_text=False, extra_args=()):
    output = project.parent / "trace.json"
    source = Path(__file__).resolve().parents[1] / "src"
    env = dict(os.environ, DECILIST_TRACE_ROOT=str(project), DECILIST_TRACE_OUTPUT=str(output),
               PYTHONPATH=os.pathsep.join((str(source), str(project / "src"))),
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONHASHSEED="0")
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("DECILIST_TRACE_FAILURE_TEXT", None)
    if trusted_text:
        env["DECILIST_TRACE_FAILURE_TEXT"] = "1"
    result = subprocess.run([sys.executable, "-m", "pytest", "-p", "decilist.triage_trace_v4", "-q", "-o", "addopts=", *extra_args, "tests"],
                            cwd=project, env=env, capture_output=True, text=True, timeout=40)
    assert result.returncode == 1, result.stdout + result.stderr
    return json.loads(output.read_text(encoding="utf-8"))


def make_cross_module(project):
    write(project, "src/value.py", "def compute(value):\n    return value + 1\n")
    write(project, "src/worker.py", "from value import compute\n\nclass Worker:\n    def __init__(self, initial):\n        self.initial = initial\n\n    @property\n    def result(self):\n        return compute(self.initial)\n")
    body = "from worker import Worker\n\ndef test_failure():\n    initial = 3\n    worker = Worker(initial)\n    observed = worker.result\n    assert observed == 8\n"
    write(project, "tests/test_failure.py", body)
    return body


def test_runtime_cross_module_constructor_property_and_full_test_setup(project):
    body = make_cross_module(project)
    trace = run_trace(project)
    context = build_context(project, trace)
    by_symbol = {row["symbol"]: row for row in context["functions"]}
    assert {"test_failure", "Worker.__init__", "Worker.result", "compute"} <= by_symbol.keys()
    test = by_symbol["test_failure"]
    assert test["source"] == body[body.index("def test_failure"):]
    assert test["complete"] and not test["editable"]
    assert "initial = 3" in test["source"]
    assert by_symbol["Worker.result"]["source"].startswith("    @property\n")
    assert "return compute(self.initial)" in by_symbol["Worker.result"]["source"]
    names = {row["id"]: row["symbol"] for row in context["functions"]}
    observed = {(names[row["caller"]], names[row["callee"]]) for row in context["call_graph"]}
    assert {("test_failure", "Worker.__init__"), ("test_failure", "Worker.result"), ("Worker.result", "compute")} <= observed
    assert all(row["executed_lines"] for row in context["functions"])
    assert all("test" not in row["path"] for row in context["edit_allowlist"])
    assert set(context["evidence_ids"]) == {row["evidence_id"] for key in ("functions", "module_context", "events", "failures") for row in context[key]}
    assert context["failures"][0]["exception_type"] == "AssertionError"
    assert context["failures"][0]["phase"] == "call"
    assert trace["tests"][0]["outcomes"] == {"setup": "passed", "call": "failed", "teardown": "passed"}


def test_trace_and_context_are_deterministic_for_the_same_execution(project):
    make_cross_module(project)
    first = run_trace(project)
    second = run_trace(project)
    assert first == second
    assert build_context(project, first) == build_context(project, second)


def test_fixture_setup_is_observed_and_never_editable(project):
    write(project, "src/calculation.py", "def value():\n    return 2\n")
    write(project, "tests/conftest.py", "import pytest\nfrom calculation import value\n\n@pytest.fixture\ndef prepared():\n    first = value()\n    return first + 1\n")
    write(project, "tests/test_failure.py", "def test_failure(prepared):\n    assert prepared == 8\n")
    context = build_context(project, run_trace(project))
    fixture = next(row for row in context["functions"] if row["symbol"] == "prepared")
    assert fixture["source"].startswith("@pytest.fixture\n")
    assert "first = value()" in fixture["source"]
    assert "fixture" in fixture["roles"] and "setup" in fixture["runtime_phases"]
    assert not fixture["editable"]


def test_async_cancellation_observation_preserves_suspend_resume_exception_order(project):
    write(project, "src/operation.py", "import asyncio\n\nasync def child(ready):\n    ready.set()\n    try:\n        await asyncio.sleep(60)\n    except asyncio.CancelledError:\n        raise\n\nasync def run():\n    ready = asyncio.Event()\n    task = asyncio.create_task(child(ready))\n    await ready.wait()\n    task.cancel()\n    try:\n        await task\n    except asyncio.CancelledError:\n        pass\n    return False\n")
    write(project, "tests/test_failure.py", "import asyncio\nfrom operation import run\n\ndef test_failure():\n    actual = asyncio.run(run())\n    assert actual is True\n")
    context = build_context(project, run_trace(project))
    child = next(row for row in context["functions"] if row["symbol"] == "child")
    events = [row for row in context["events"] if row["function_id"] == child["id"]]
    kinds = [row["kind"] for row in events]
    cancellation = next(row for row in events if row.get("exception_type") == "CancelledError")
    assert "suspend" in kinds and "resume" in kinds
    assert next(row["seq"] for row in events if row["kind"] == "suspend") < cancellation["seq"]
    assert next(row["seq"] for row in events if row["kind"] == "resume") < cancellation["seq"]
    assert cancellation["seq"] < next(row["seq"] for row in events if row["kind"] == "return")
    assert events[0]["invocation"] == cancellation["invocation"]
    assert "task.cancel()" in next(row["source"] for row in context["functions"] if row["symbol"] == "run")


def test_default_failure_text_does_not_capture_dynamic_values_or_locals(project):
    write(project, "tests/test_failure.py", "import os\n\ndef test_failure():\n    leaked = 'dynamic-' + 'private-value'\n    os.environ['V4_SECRET_EXAMPLE'] = leaked\n    assert False, leaked\n")
    trace = run_trace(project, extra_args=("--showlocals",))
    serialized = json.dumps(trace)
    assert "dynamic-private-value" not in serialized
    assert "V4_SECRET_EXAMPLE" not in serialized
    assert "leaked" not in trace["failures"][0]["longrepr"]
    assert "omitted" in trace["failures"][0]["longrepr"]
    assert not trace["failures"][0]["failure_text_captured"]
    assert all(not any(key in row for key in ("locals", "args", "return_value", "environment")) for row in trace["events"])


def test_trusted_failure_text_has_observed_diff_and_redacts_common_credentials(project):
    write(project, "tests/test_failure.py", "def test_failure():\n    assert {'status': 500} == {'status': 200}, 'token=sk-abcdefghijklmnopqrst'\n")
    trace = run_trace(project, trusted_text=True)
    failure = trace["failures"][0]
    assert "500" in failure["longrepr"] and "200" in failure["longrepr"]
    assert "sk-abcdefghijklmnopqrst" not in failure["longrepr"]
    assert "[REDACTED]" in failure["longrepr"]
    assert failure["failure_text_captured"]


def test_source_redaction_disables_edits_but_preserves_benign_route_strings(project):
    write(project, "src/app.py", "def route():\n    token = 'sk-abcdefghijklmnopqrst'\n    endpoint = '/health'\n    return endpoint\n")
    write(project, "tests/test_failure.py", "from app import route\ndef test_failure():\n    assert route() == '/wrong'\n")
    context = build_context(project, run_trace(project))
    row = next(row for row in context["functions"] if row["symbol"] == "route")
    assert "'/health'" in row["source"]
    assert "sk-abcdefghijklmnopqrst" not in json.dumps(context)
    assert not row["editable"] and "source_redacted" in row["guard_reasons"]


def fake_trace(path, *, symbol="outside", line=1):
    identifier = f"{path}:{line}:{symbol}"
    return {"schema_version": 4, "functions": [{"id": identifier, "path": path, "symbol": symbol,
             "first_line": line, "last_line": line + 2, "executed_lines": [line + 1],
             "test_ids": ["tests/test_failure.py::test_failure"], "phases": ["call"]}],
            "edges": [], "events": [], "failures": [], "truncation": {"truncated": False}}


@pytest.mark.parametrize("path", ["../outside.py", "tests/../../outside.py", "//server/outside.py", "file:///outside.py", "C:outside.py"])
def test_source_path_traversal_and_network_paths_are_rejected(project, path):
    write(project.parent, "outside.py", "def outside():\n    return 'OUTSIDE_CONTENT'\n")
    context = build_context(project, fake_trace(path))
    assert not context["functions"] and not context["edit_allowlist"]
    assert context["missing"][0]["reason"] == "path_not_project_local_python"
    assert "OUTSIDE_CONTENT" not in json.dumps(context)


def test_symlink_escape_cannot_be_read_or_traced(project):
    outside = write(project.parent, "outside.py", "def outside():\n    return 'OUTSIDE_CONTENT'\n")
    link = project / "linked.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Host does not allow creation of test symlinks")
    context = build_context(project, fake_trace("linked.py"))
    assert not context["functions"] and "OUTSIDE_CONTENT" not in json.dumps(context)
    recorder = TraceRecorder(project)
    code = compile(outside.read_text(), str(link), "exec")
    assert recorder._function(code.co_consts[0]) is None


def test_stdlib_site_packages_and_instrumentation_are_not_source_candidates(project):
    assert TraceRecorder(project)._function(json.dumps.__code__) is None
    for path in ("site-packages/external.py", "src/triage_trace_v4.py", "src/triage_context_v4.py"):
        file = write(project, path, "def outside():\n    return 1\n")
        recorder = TraceRecorder(project)
        code = compile(file.read_text(), str(file), "exec")
        assert recorder._function(code.co_consts[0]) is None


def test_runtime_and_context_payload_limits_are_explicit_and_whole_body_safe(project):
    file = write(project, "load.py", "def outside():\n    total = 0\n    for value in range(100):\n        total += value\n    return total\n")
    namespace = {}
    exec(compile(file.read_text(), str(file), "exec"), namespace)
    recorder = TraceRecorder(project, max_events=4, max_lines_per_function=2)
    recorder.nodeid = "tests/test_failure.py::test_failure"
    recorder.phase = "call"
    recorder.start()
    try:
        namespace["outside"]()
    finally:
        recorder.stop()
    trace = recorder.as_dict()
    assert len(trace["events"]) == 4
    assert {"event_limit", "executed_line_limit"} <= set(trace["truncation"]["reasons"])
    assert trace["truncation"]["dropped_events"] > 0
    assert len(trace["functions"][0]["executed_lines"]) == 2
    assert len(json.dumps(trace, separators=(",", ":"))) <= trace["limits"]["max_output_chars"]
    # An oversized whole function must never become an editable partial body.
    source = "def outside():\n" + "    value = '" + "x" * 5000 + "'\n    return value\n"
    write(project, "load.py", source)
    context = build_context(project, fake_trace("load.py"), max_chars=3000)
    assert len(json.dumps(context)) <= 3000
    assert context["truncation"]["truncated"]
    assert not context["edit_allowlist"]
    assert context["functions"][0]["source"] == ""
    assert not context["functions"][0]["complete"]
    assert "whole_function_omitted_by_character_limit" in context["functions"][0]["guard_reasons"]


def test_context_never_executes_or_imports_source(project):
    write(project, "untrusted.py", "raise RuntimeError('MUST_NOT_RUN')\n\ndef outside():\n    return 1\n")
    context = build_context(project, fake_trace("untrusted.py", line=3))
    assert context["functions"][0]["source"] == "def outside():\n    return 1\n"
    assert "MUST_NOT_RUN" in context["module_context"][0]["snippets"][0]["source"]


def test_unexecuted_decoy_is_not_selected_by_source_names(project):
    write(project, "src/operations.py", "def observed():\n    return 2\n\ndef obvious_fix_target():\n    return 10\n")
    write(project, "tests/test_failure.py", "from operations import observed\ndef test_failure():\n    assert observed() == 9\n")
    context = build_context(project, run_trace(project))
    assert "observed" in {row["symbol"] for row in context["functions"]}
    assert "obvious_fix_target" not in json.dumps(context)


def test_observed_methods_retrieve_class_setup_without_sibling_method_bodies(project):
    write(project, "src/settings.py", "class Service:\n    route_name = '/health'\n\n    def route(self):\n        return self.route_name\n\n    def unexecuted_decoy(self):\n        return 'HIDDEN_SIBLING_BODY'\n")
    write(project, "tests/test_failure.py", "from settings import Service\ndef test_failure():\n    assert Service().route() == '/expected'\n")
    context = build_context(project, run_trace(project))
    setup = "".join(snippet["source"] for row in context["module_context"] if row["path"] == "src/settings.py" for snippet in row["snippets"])
    assert "class Service:" in setup
    assert "route_name = '/health'" in setup
    assert "HIDDEN_SIBLING_BODY" not in json.dumps(context)


@pytest.mark.parametrize("options", [{"max_chars": 100}, {"max_chars": True}, {"max_functions": 0}, {"max_depth": 9}])
def test_invalid_context_limits_fail_closed(project, options):
    with pytest.raises(ValueError):
        build_context(project, fake_trace("unused.py"), **options)


def test_oversized_input_fails_before_source_reads(project):
    trace = fake_trace("unused.py")
    trace["untrusted_padding"] = "x" * 600001
    with pytest.raises(ValueError, match="input character limit"):
        build_context(project, trace)


@pytest.mark.parametrize("field", ["functions", "edges", "events", "failures"])
def test_malformed_runtime_record_containers_fail_closed(project, field):
    trace = fake_trace("unused.py")
    trace[field] = {"malformed": True}
    with pytest.raises(ValueError, match="record lists"):
        build_context(project, trace)


def test_call_depth_limits_observed_descendants_and_marks_omissions(project):
    write(project, "src/chain.py", "def outer():\n    return middle()\n\ndef middle():\n    return leaf()\n\ndef leaf():\n    return 1\n")
    write(project, "tests/test_failure.py", "from chain import outer\ndef test_failure():\n    assert outer() == 9\n")
    trace = run_trace(project)
    context = build_context(project, trace, max_depth=1)
    assert {row["symbol"] for row in context["functions"]} == {"test_failure", "outer"}
    assert "call_depth_limit" in context["truncation"]["reasons"]
    assert len(context["call_graph"]) == 1


def test_plugin_can_load_standalone_without_importing_snapshot_package(project):
    write(project, "decilist/__init__.py", "raise RuntimeError('SNAPSHOT_PACKAGE_MUST_NOT_IMPORT')\n")
    write(project, "tests/test_failure.py", "def test_failure():\n    assert 1 == 2\n")
    output = project.parent / "standalone.json"
    plugin = Path(__file__).resolve().parents[1] / "src/decilist/triage_trace_v4.py"
    script = "import importlib.util,sys,pytest\nspec=importlib.util.spec_from_file_location('owned_trace_plugin',sys.argv[1])\nmodule=importlib.util.module_from_spec(spec)\nsys.modules[spec.name]=module\nspec.loader.exec_module(module)\nraise SystemExit(pytest.main(['-q','tests'],plugins=[module]))\n"
    env = dict(os.environ, DECILIST_TRACE_ROOT=str(project), DECILIST_TRACE_OUTPUT=str(output),
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONPATH=str(project))
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("DECILIST_TRACE_FAILURE_TEXT", None)
    result = subprocess.run([sys.executable, "-c", script, str(plugin)], cwd=project,
                            env=env, capture_output=True, text=True, timeout=40)
    assert result.returncode == 1, result.stdout + result.stderr
    trace = json.loads(output.read_text(encoding="utf-8"))
    assert trace["failures"][0]["exception_type"] == "AssertionError"
    assert len(trace["functions"]) == 1


def test_opt_in_failure_text_rejects_showlocals(project):
    write(project, "tests/test_failure.py", "def test_failure():\n    assert False\n")
    source = Path(__file__).resolve().parents[1] / "src"
    env = dict(os.environ, DECILIST_TRACE_ROOT=str(project), DECILIST_TRACE_OUTPUT=str(project.parent / "trace.json"),
               DECILIST_TRACE_FAILURE_TEXT="1", PYTHONPATH=str(source), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    env.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run([sys.executable, "-m", "pytest", "-p", "decilist.triage_trace_v4", "--showlocals", "-q", "tests"],
                            cwd=project, env=env, capture_output=True, text=True, timeout=40)
    assert result.returncode == 4
    assert "cannot be combined with --showlocals" in result.stderr


def test_long_runtime_trace_under_24k_preserves_full_test_and_deeper_data_helper(project):
    # Many real constructors execute before a request traverses several small
    # wrappers. The failed callback reads mapping fields, not a named target.
    pieces = []
    for number in range(26):
        assignments = "".join(f"        self.part_{index} = {index}\n" for index in range(45))
        pieces.append(f"class Startup{number}:\n    def __init__(self):\n{assignments}\n")
    pieces.append("STARTUP = [" + ",".join(f"Startup{number}" for number in range(26)) + "]\n\n")
    pieces.append("def push(callback, headers):\n    return carrier0(callback, headers)\n\n")
    for number in range(4):
        target = f"carrier{number + 1}" if number < 3 else "dispatch"
        pieces.append(f"def carrier{number}(callback, headers):\n    return {target}(callback, headers)\n\n")
    pieces.append("def dispatch(callback, headers):\n    callback({'headers': combine_mapping(headers)})\n\ndef combine_mapping(headers):\n    merged = dict(headers)\n    merged.update({'mode': 'default', 'keep': 'yes'})\n    return merged\n")
    write(project, "src/recording.py", "".join(pieces))
    test_source = "import pytest\nfrom recording import STARTUP, push\n\n@pytest.mark.parametrize('mode', ['first', 'second', 'third'])\ndef test_records(mode):\n    for constructor in STARTUP:\n        constructor()\n    records = []\n    def capture(record):\n        records.append((record['headers']['mode'], record['headers']['keep']))\n    push(capture, {'mode': mode})\n    assert records == [(mode, 'yes')]\n"
    write(project, "tests/test_records.py", test_source)
    trace = run_trace(project, trusted_text=True)
    assert len(trace["functions"]) >= 34
    assert trace["truncation"]["truncated"]
    context = build_context(project, trace, max_chars=24000)
    assert len(json.dumps(context)) <= 24000
    test = next(row for row in context["functions"] if row["symbol"] == "test_records")
    assert test["complete"] and test["source"] == test_source[test_source.index("@pytest.mark.parametrize"):]
    helper = next(row for row in context["functions"] if row["symbol"] == "combine_mapping")
    assert helper["complete"] and helper["editable"]
    assert "merged.update" in helper["source"]
    assert len(context["edit_allowlist"]) >= 2
    assert all(row["source"] and row["complete"] for row in context["functions"])


def test_tight_context_removes_low_priority_rows_before_high_priority_bodies(project):
    pieces = []
    for number in range(20):
        body = "".join(f"    unused_{index} = {index}\n" for index in range(20))
        pieces.append(f"def noise{number}():\n{body}    return 1\n\n")
    pieces.append("NOISE = [" + ",".join(f"noise{number}" for number in range(20)) + "]\n\ndef normalize_record(record):\n    return {'status': record['status'] + 1}\n")
    write(project, "src/records.py", "".join(pieces))
    write(project, "tests/test_records.py", "from records import NOISE, normalize_record\ndef test_records():\n    for function in NOISE:\n        function()\n    actual = normalize_record({'status': 200})\n    assert actual['status'] == 200\n")
    context = build_context(project, run_trace(project), max_chars=12000)
    assert len(json.dumps(context)) <= 12000
    assert any(row["symbol"] == "test_records" and row["complete"] and row["source"] for row in context["functions"])
    assert any(row["symbol"] == "normalize_record" and row["editable"] for row in context["functions"])
    assert all(row["source"] for row in context["functions"])
    assert context["truncation"]["omitted_functions"]
