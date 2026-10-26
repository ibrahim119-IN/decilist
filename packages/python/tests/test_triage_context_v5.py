"""Generic V5 execution/source regressions, independent of measured corpora."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from decilist.triage_context_v5 import _tokens, build_context


def write(root, path, source):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8", newline="")
    return target


def run_trace(project, *, trusted=False):
    output = project.parent / "trace-v5.json"
    package = Path(__file__).resolve().parents[1] / "src"
    env = dict(os.environ, DECILIST_TRACE_ROOT=str(project), DECILIST_TRACE_OUTPUT=str(output),
               PYTHONPATH=os.pathsep.join((str(package), str(project / "src"))),
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0")
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("DECILIST_TRACE_FAILURE_TEXT", None)
    if trusted:
        env["DECILIST_TRACE_FAILURE_TEXT"] = "1"
    result = subprocess.run([sys.executable, "-m", "pytest", "-p", "decilist.triage_trace_v5", "-q", "-o", "addopts=", "tests"],
                            cwd=project, env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 1, result.stdout + result.stderr
    return json.loads(output.read_text(encoding="utf-8"))


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    return root


def test_more_than_128_setup_functions_preserve_actual_late_validator_and_edge(project):
    pieces = [f"def setup{index}():\n    return {index}\n\n" for index in range(320)]
    pieces.append("SETUP = [" + ",".join(f"setup{index}" for index in range(320)) + "]\n")
    pieces.append("def initialize():\n    for function in SETUP:\n        function()\n\ndef validate_record(record):\n    return type(record['value']) is bool\n\ndef evaluate(record):\n    return validate_record(record)\n")
    write(project, "src/validation.py", "".join(pieces))
    write(project, "tests/test_record.py", "from validation import initialize, evaluate\ndef test_record():\n    initialize()\n    assert evaluate({'value': 1.0}) is True\n")
    trace = run_trace(project)
    assert trace["schema_version"] == 5
    assert len(trace["functions"]) <= 256
    assert trace["coverage"]["function_evictions"] > 0
    by_symbol = {row["symbol"]: row for row in trace["functions"]}
    assert {"test_record", "evaluate", "validate_record"} <= by_symbol.keys()
    assert any(edge["caller"] == by_symbol["evaluate"]["id"] and edge["callee"] == by_symbol["validate_record"]["id"] for edge in trace["edges"])
    assert any(event["function_id"] == by_symbol["validate_record"]["id"] for event in trace["events"])
    context = build_context(project, trace)
    validator = next(row for row in context["functions"] if row["symbol"] == "validate_record")
    assert validator["complete"] and validator["editable"]
    assert "type(record['value']) is bool" in validator["source"]
    assert context["schema_version"] == 5 and context["kind"] == "runtime_python_context"
    assert len(json.dumps(context)) <= 24000
    assert context["coverage"]["trace_function_evictions"] > 0


def test_observed_small_sibling_survives_more_than_24_candidates(project):
    pieces = [f"def noise{index}():\n    return {{'content': b'padding', 'method': 'GET'}}\n\n" for index in range(45)]
    pieces.append("WARMUP = [" + ",".join(f"noise{index}" for index in range(45)) + "]\n")
    pieces.append("class Packet:\n    def __init__(self):\n        self.method = 'POST'\n        self.payload = b'entity'\n\ndef select_entity(packet, verb):\n    if packet.method == 'GET' and verb != 'GET':\n        return b''\n    return packet.payload\n\ndef dispatch_payload(packet):\n    method = 'GET'\n    content = select_entity(packet, method)\n    return {'method': method, 'content': content}\n")
    write(project, "src/adapter.py", "".join(pieces))
    write(project, "tests/test_packet.py", "from adapter import WARMUP, Packet, dispatch_payload\ndef test_packet():\n    for function in WARMUP:\n        function()\n    actual = dispatch_payload(Packet())\n    assert actual['method'] == 'GET'\n    assert actual['content'] == b''\n")
    trace = run_trace(project, trusted=True)
    assert len(trace["functions"]) > 24
    context = build_context(project, trace)
    by_name = {row["symbol"]: row for row in context["functions"]}
    assert by_name["select_entity"]["editable"] and by_name["select_entity"]["complete"]
    assert by_name["dispatch_payload"]["complete"]
    assert any(edge["caller"] == by_name["dispatch_payload"]["id"] and edge["callee"] == by_name["select_entity"]["id"] for edge in context["call_graph"])
    assert len(json.dumps(context)) <= 24000


def test_negative_membership_literals_ground_complete_invariant_body(project):
    pieces = [f"def accessor{index}():\n    return {{'content': 'x', 'headers': {{}}}}\n\n" for index in range(30)]
    pieces.append("ACCESSORS = [" + ",".join(f"accessor{index}" for index in range(30)) + "]\n")
    pieces.append("def prepare_fields(headers, defaults):\n    output = dict(headers)\n    for key, value in defaults.items():\n        if key == 'content-size' and 'content-size' in headers:\n            continue\n        output.setdefault(key, value)\n    return output\n")
    write(project, "src/fields.py", "".join(pieces))
    test_source = "from fields import ACCESSORS, prepare_fields\ndef test_fields():\n    for function in ACCESSORS:\n        function()\n    actual = prepare_fields({'content-size': '3'}, {'transfer-mode': 'chunks'})\n    assert 'transfer-mode' not in actual\n"
    write(project, "tests/test_fields.py", test_source)
    context = build_context(project, run_trace(project), max_chars=12000)
    body = next(row for row in context["functions"] if row["symbol"] == "prepare_fields")
    assert body["complete"] and body["editable"]
    assert body["source"].startswith("def prepare_fields(")
    assert any(row["symbol"] == "test_fields" and row["source"] == test_source[test_source.index("def test_fields"):] for row in context["functions"])
    assert len(json.dumps(context)) <= 12000


def test_small_child_of_observed_adapter_survives_two_hop_closure(project):
    pieces = [f"def warmup{index}():\n    return {{'content': b'x', 'method': 'GET'}}\n\n" for index in range(40)]
    pieces.append("WARMUP = [" + ",".join(f"warmup{index}" for index in range(40)) + "]\n")
    filler = "".join(f"    spare_{index} = {index}\n" for index in range(15))
    pieces.append("def describe_content(packet):\n" + filler + "    return {'content': packet.payload, 'method': 'GET'}\n\ndef resolve_method(packet):\n" + filler + "    record = {'content': packet.payload, 'method': 'GET'}\n    return record['method']\n\n")
    pieces.append("class Packet:\n    method = 'POST'\n    payload = b'entity'\n\ndef choose_bytes(packet, verb):\n    if packet.method == 'GET' and verb != 'GET':\n        return b''\n    return packet.payload\n\ndef container_for(packet):\n    method = resolve_method(packet)\n    describe_content(packet)\n    return {'method': method, 'content': choose_bytes(packet, method)}\n\ndef dispatch_payload(packet):\n    return container_for(packet)\n")
    write(project, "src/transport.py", "".join(pieces))
    write(project, "tests/test_transport.py", "from transport import WARMUP, Packet, dispatch_payload\ndef test_transport():\n    for function in WARMUP:\n        function()\n    actual = dispatch_payload(Packet())\n    assert actual['method'] == 'GET'\n    assert actual['content'] == b''\n")
    context = build_context(project, run_trace(project), max_chars=18000)
    by_name = {row["symbol"]: row for row in context["functions"]}
    assert by_name["choose_bytes"]["complete"] and by_name["choose_bytes"]["editable"]
    assert by_name["container_for"]["complete"] and by_name["dispatch_payload"]["complete"]
    observed = {(edge["caller"], edge["callee"]) for edge in context["call_graph"]}
    assert (by_name["dispatch_payload"]["id"], by_name["container_for"]["id"]) in observed
    assert (by_name["container_for"]["id"], by_name["choose_bytes"]["id"]) in observed
    assert len(json.dumps(context)) <= 18000


def test_membership_and_comparison_literals_are_tokens_but_docstrings_are_not():
    import ast
    tree = ast.parse("def check(record):\n    'unrelated-secret-documentation'\n    assert 'transfer-mode' not in record\n    assert record['value'] == 'accepted-kind'\n")
    tokens = _tokens(tree.body[0])
    assert tokens["transfer"] and tokens["mode"] and tokens["value"] and tokens["accepted"]
    assert not tokens["unrelated"] and not tokens["documentation"]


def test_budget_skips_large_rows_then_admits_complete_small_adapter(project):
    filler = "".join(f"    spare_{index} = {index}\n" for index in range(400))
    write(project, "src/engine.py", "def large_wrapper(record):\n" + filler + "    return record\n\ndef adapt(record):\n    return {'status': record['status'] + 1}\n")
    write(project, "tests/test_engine.py", "from engine import large_wrapper, adapt\ndef test_engine():\n    large_wrapper({'status': 0})\n    assert adapt({'status': 200})['status'] == 200\n")
    context = build_context(project, run_trace(project), max_chars=9000)
    assert next(row for row in context["functions"] if row["symbol"] == "adapt")["editable"]
    assert all(row["complete"] and row["source"] for row in context["functions"])
    assert len(json.dumps(context)) <= 9000
    assert context["truncation"]["omitted_function_count"]


def test_runtime_selection_has_no_unexecuted_decoy_and_source_is_not_executed(project):
    write(project, "src/calculation.py", "def observed():\n    return 1\n\ndef tempting_decoy():\n    raise RuntimeError('DO_NOT_INCLUDE')\n")
    write(project, "tests/test_calculation.py", "from calculation import observed\ndef test_calculation():\n    assert observed() == 2\n")
    context = build_context(project, run_trace(project))
    assert "tempting_decoy" not in json.dumps(context)
    assert all(row["runtime_observed"] for row in context["functions"])
    assert context["coverage"]["unobserved_source_fallback"] is False


def test_default_trace_privacy_and_source_redaction_preserve_path_literals(project):
    write(project, "src/app.py", "def route():\n    token = 'sk-abcdefghijklmnopqrst'\n    return '/health'\n")
    write(project, "tests/test_route.py", "from app import route\ndef test_route():\n    private = 'dynamic-' + 'private-value'\n    assert route() == '/other', private\n")
    trace = run_trace(project)
    assert "dynamic-private-value" not in json.dumps(trace)
    context = build_context(project, trace)
    body = next(row for row in context["functions"] if row["symbol"] == "route")
    assert "'/health'" in body["source"]
    assert "sk-abcdefghijklmnopqrst" not in json.dumps(context)
    assert not body["editable"] and "source_redacted" in body["guard_reasons"]


def test_small_execution_and_context_are_deterministic(project):
    write(project, "src/work.py", "def compute():\n    return 1\n")
    write(project, "tests/test_work.py", "from work import compute\ndef test_work():\n    assert compute() == 2\n")
    first, second = run_trace(project), run_trace(project)
    assert first == second
    assert build_context(project, first) == build_context(project, second)


@pytest.mark.parametrize("raw", ["../outside.py", "//server/outside.py", "file:///outside.py", "C:outside.py"])
def test_path_escapes_never_supply_edit_evidence(project, raw):
    write(project.parent, "outside.py", "def outside():\n    return 'PRIVATE_OUTSIDE'\n")
    trace = {"schema_version": 5, "functions": [{"id": "x", "path": raw, "symbol": "outside", "first_line": 1,
             "last_line": 2, "executed_lines": [2], "test_ids": [], "phases": ["call"]}], "edges": [], "events": [], "failures": []}
    context = build_context(project, trace)
    assert not context["functions"] and not context["edit_allowlist"]
    assert "PRIVATE_OUTSIDE" not in json.dumps(context)


def test_input_record_caps_are_consistent_and_fail_closed(project):
    trace = {"schema_version": 5, "functions": [{}] * 257, "edges": [], "events": [], "failures": []}
    with pytest.raises(ValueError, match="record count"):
        build_context(project, trace)


def test_legacy_trace_replay_is_labeled_schema4_input(project):
    write(project, "x.py", "def value():\n    return 1\n")
    trace = {"schema_version": 4, "functions": [{"id": "x", "path": "x.py", "symbol": "value", "first_line": 1,
             "last_line": 2, "executed_lines": [2], "test_ids": [], "phases": ["call"]}], "edges": [], "events": [], "failures": []}
    context = build_context(project, trace)
    assert context["schema_version"] == 5 and context["coverage"]["input_schema_version"] == 4
    assert context["functions"][0]["source"] == "def value():\n    return 1\n"


def test_crlf_source_stays_exact_for_multiline_replacement(project):
    target = project / "src/transform.py"
    target.parent.mkdir(parents=True)
    original = b"def transform(value):\r\n    if value == 2:\r\n        return 3\r\n    return value\r\n"
    target.write_bytes(original)
    write(project, "tests/test_transform.py", "from transform import transform\ndef test_transform():\n    assert transform(2) == 4\n")
    context = build_context(project, run_trace(project))
    body = next(row for row in context["functions"] if row["symbol"] == "transform")
    assert body["editable"] and body["source"].encode() == original
    old = "if value == 2:\r\n        return 3"
    new = "if value == 2:\r\n        return 4"
    assert body["source"].count(old) == 1
    replacement = original.replace(old.encode(), new.encode(), 1)
    assert b"return 4\r\n" in replacement and b"\n" not in replacement.replace(b"\r\n", b"")


def test_recent_async_cancellation_chronology_has_complete_function_bodies(project):
    write(project, "src/asyncwork.py", "import asyncio\nasync def child(ready):\n    ready.set()\n    try:\n        await asyncio.sleep(60)\n    except asyncio.CancelledError:\n        raise\nasync def run():\n    ready = asyncio.Event()\n    task = asyncio.create_task(child(ready))\n    await ready.wait()\n    task.cancel()\n    try:\n        await task\n    except asyncio.CancelledError:\n        pass\n    return False\n")
    write(project, "tests/test_asyncwork.py", "import asyncio\nfrom asyncwork import run\ndef test_asyncwork():\n    assert asyncio.run(run()) is True\n")
    context = build_context(project, run_trace(project))
    child = next(row for row in context["functions"] if row["symbol"] == "child")
    events = [event for event in context["events"] if event["function_id"] == child["id"]]
    cancellation = next(event for event in events if event.get("exception_type") == "CancelledError")
    assert child["complete"] and "await asyncio.sleep(60)" in child["source"]
    assert next(event["seq"] for event in events if event["kind"] == "suspend") < cancellation["seq"]
    assert next(event["seq"] for event in events if event["kind"] == "resume") < cancellation["seq"]


def test_long_observed_producer_bundle_is_not_replaced_by_small_terminal_helper(project):
    noise = "".join(f"def noise{index}():\n    return {{'field': 'padding', 'value': 0}}\n\n" for index in range(38))
    noise += "WARMUP = [" + ",".join(f"noise{index}" for index in range(38)) + "]\n"
    producer = r'''    def accept_text(self, text):
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if "\x00" in text:
            raise ValueError("null characters are not supported")
        if len(text) > 100000:
            raise ValueError("chunk is too large")
        if self.pending:
            text = self.pending + text
            self.pending = ""
        fragments = text.split(",")
        if not text.endswith(","):
            self.pending = fragments.pop()
        records = []
        for fragment in fragments:
            if not fragment:
                continue
            field, separator, number = fragment.partition(":")
            if separator != ":":
                raise ValueError("each field needs a separator")
            if not field:
                raise ValueError("field cannot be empty")
            if len(field) > 80:
                raise ValueError("field name is too long")
            if field != field.strip():
                raise ValueError("field whitespace is significant")
            if not field.replace("_", "").isalpha():
                raise ValueError("field must contain letters")
            number = number.strip()
            if not number:
                raise ValueError("number cannot be empty")
            sign = 1
            if number.startswith("-"):
                sign = -1
                number = number[1:]
            elif number.startswith("+"):
                number = number[1:]
            if not number or not number.isdecimal():
                raise ValueError("number must use decimal digits")
            if len(number) > 12:
                raise ValueError("number is too large")
            parsed = int(number) * sign + 1
            records.append({"field": field, "value": parsed})
        return records
'''
    assert len(producer) > 1800
    source = noise + "class Framer:\n    def __init__(self):\n        self.pending = ''\n\n" + producer
    source += "\n    def finish(self):\n        if not self.pending:\n            return []\n        field, _, number = self.pending.partition(':')\n        self.pending = ''\n        return [{'field': field, 'value': int(number)}]\n\ndef produce(chunks):\n    parser = Framer()\n    for chunk in chunks:\n        yield from parser.accept_text(chunk)\n    yield from parser.finish()\n"
    write(project, "src/records.py", source)
    write(project, "tests/test_records.py", "from records import WARMUP, produce\ndef test_records():\n    for function in WARMUP:\n        function()\n    actual = list(produce(['first:7,', 'second:8,']))\n    assert actual == [{'field': 'first', 'value': 7}, {'field': 'second', 'value': 8}]\n")
    context = build_context(project, run_trace(project), max_chars=24000)
    by_name = {row["symbol"]: row for row in context["functions"]}
    assert by_name["Framer.accept_text"]["complete"] and by_name["Framer.accept_text"]["editable"]
    assert by_name["Framer.accept_text"]["source"] == producer
    assert by_name["Framer.finish"]["complete"] and by_name["produce"]["complete"]
    edge = next(edge for edge in context["call_graph"] if edge["caller"] == by_name["produce"]["id"] and edge["callee"] == by_name["Framer.accept_text"]["id"])
    assert edge["value_flow"] == "yield_from"
    assert len(json.dumps(context)) <= 24000
