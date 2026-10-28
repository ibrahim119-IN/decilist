"""Neutral fixtures exercise the host boundary without study data or inference."""
import asyncio
import json
import shutil
from pathlib import Path

import httpx
import pytest

from decilist.review_repair import ExecutionResult, ProviderResponseError, ReproductionSpec, _snapshot
from decilist.review_repair_local import LocalReviewProvider, NodeReproductionHost


@pytest.fixture
def library(tmp_path):
    (tmp_path / "math.cjs").write_text(
        "exports.add = (a, b) => a + b;\n"
        "exports.reject = async () => { throw new Error('expected refusal'); };\n"
        "exports.values = () => [1, {value: 2}];\n"
        "exports.invalid = () => { throw new TypeError('invalid input'); };\n"
        "exports.invalidAsync = async () => { throw new TypeError('invalid input'); };\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def host():
    if shutil.which("node") is None:
        pytest.skip("Node is required for executable proof tests")
    return NodeReproductionHost(entry_paths=["math.cjs"], timeout_s=10)


@pytest.mark.parametrize("endpoint", [
    "https://example.com/v1/chat/completions", "http://127.0.0.1.example.com:8792",
    "http://localhost:8792", "http://192.168.1.2:8792", "http://0.0.0.0:8792",
    "http://127.0.0.1:8792@evil.example", "http://user:pass@127.0.0.1:8792",
    "http://127.0.0.1:8792?forward=https://example.com", "http://127.0.0.1:8792/other",
])
def test_provider_rejects_non_literal_loopback_and_ambiguous_urls(endpoint):
    with pytest.raises(ValueError):
        LocalReviewProvider(source_paths=["math.cjs"], public_docs="Adds numbers.", endpoint=endpoint)


async def test_provider_structured_request_exact_capture_and_hash_injection(tmp_path):
    requests = []
    source = "exports.add = (a, b) => a - b;\n"
    snapshot = _snapshot({"math.cjs": source, "private-hidden-check.cjs": "SECRET EXPECTED FIX"})

    async def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            content = {"hypotheses": [{"claim": "Addition subtracts the second operand.", "path": "math.cjs",
                        "start_line": 1, "end_line": 1, "evidence": source.strip(),
                        "reproduction": {"code": "const m = await load('math.cjs');\nassert.equal(m.default.add(2, 3), 5);"}}]}
        else:
            content = {"edits": [{"path": "math.cjs", "start_line": 1, "end_line": 1,
                                  "replacement": "exports.add = (a, b) => a + b;"}]}
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}]})

    provider = LocalReviewProvider(source_paths=["math.cjs"], public_docs="add(a,b) returns the sum.",
                                   evidence_dir=tmp_path, disable_thinking=True,
                                   transport=httpx.MockTransport(respond))
    hypothesis = (await provider.hypothesize(snapshot, "Check numerical behavior"))[0]
    edits = await provider.repair(snapshot, hypothesis, ExecutionResult(outcome="assertion_failure", assertions=1))
    assert edits[0].source_sha256 == snapshot.source_hashes["math.cjs"]
    assert len(provider.traces) == 2
    assert requests[0]["temperature"] == 0
    assert requests[0]["seed"] == 17843
    assert requests[0]["cache_prompt"] is False
    assert requests[0]["max_tokens"] == 1024
    assert requests[0]["reasoning_format"] == "deepseek"
    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": False}
    hypothesis_schema = requests[0]["response_format"]["json_schema"]["schema"]["properties"]["hypotheses"]
    assert hypothesis_schema["maxItems"] == 1
    assert hypothesis_schema["items"]["properties"]["claim"]["maxLength"] == 320
    assert hypothesis_schema["items"]["properties"]["evidence"]["maxLength"] == 320
    assert requests[1]["max_tokens"] == 1024
    assert requests[1]["chat_template_kwargs"] == {"enable_thinking": False}
    assert requests[0]["response_format"]["json_schema"]["strict"] is True
    assert "SECRET EXPECTED FIX" not in provider.traces[0].request
    assert (tmp_path / "001-hypothesize-request.json").read_text(encoding="utf-8") == provider.traces[0].request
    assert (tmp_path / "002-repair-response.json").read_text(encoding="utf-8") == provider.traces[1].response
    with pytest.raises((AttributeError, TypeError)):
        provider.traces[0].stage = "modified"


async def test_provider_does_not_follow_redirects_or_retry():
    calls = []

    async def redirect(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://example.com/collect"})

    provider = LocalReviewProvider(source_paths=["math.cjs"], public_docs="Adds numbers.",
                                   transport=httpx.MockTransport(redirect))
    with pytest.raises(httpx.HTTPStatusError):
        await provider.hypothesize(_snapshot({"math.cjs": "exports.add = () => 1;"}), "Review")
    assert calls == ["http://127.0.0.1:8792/v1/chat/completions"]
    assert len(provider.traces) == 1


async def test_provider_rejects_extra_fields():
    async def respond(request):
        value = {"hypotheses": [], "outcome": "pass"}
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]})

    provider = LocalReviewProvider(source_paths=["math.cjs"], public_docs="Adds numbers.",
                                   transport=httpx.MockTransport(respond))
    with pytest.raises(ProviderResponseError, match="^invalid_provider_output$"):
        await provider.hypothesize(_snapshot({"math.cjs": "exports.add = () => 1;"}), "Review")


@pytest.mark.parametrize("code", ["import { add } from './math.cjs'; assert.equal(add(1, 2), 3);",
                                 "require('./math.cjs');", "process.exit(0);", "Function('return 1')();"])
async def test_provider_rejects_prohibited_proofs_without_hiding_raw_output(tmp_path, code):
    raw = {"hypotheses": [{"claim": "Neutral proposal", "path": "math.cjs", "start_line": 1,
                           "end_line": 1, "evidence": "add", "reproduction": {"code": code}}]}
    calls = []

    async def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(raw)}}]})

    provider = LocalReviewProvider(source_paths=["math.cjs"], public_docs="Adds finite numbers.",
                                   evidence_dir=tmp_path, transport=httpx.MockTransport(respond))
    with pytest.raises(ProviderResponseError, match="^prohibited_reproduction$"):
        await provider.hypothesize(_snapshot({"math.cjs": "exports.add = (a, b) => a + b;"}), "Review")
    assert len(calls) == 1
    assert json.loads(json.loads(provider.traces[0].response)["choices"][0]["message"]["content"]) == raw
    assert (tmp_path / "001-hypothesize-response.json").read_text(encoding="utf-8") == provider.traces[0].response
    system = json.loads(provider.traces[0].request)["messages"][0]["content"]
    assert "finite numeric inputs" in system
    assert "strings, booleans, null" in system
    assert "const {default: fn} = await load('index.js')" in system
    assert '{"hypotheses":[]}' in system


async def test_provider_accepts_empty_response_and_normal_function_syntax():
    outputs = [{"hypotheses": []}, {"hypotheses": [{"claim": "Neutral proposal", "path": "math.cjs",
                "start_line": 1, "end_line": 1, "evidence": "add", "reproduction": {
                    "code": "function* values() { yield 1; } assert.deepEqual(Array.from(values()), [1]);"}}]}]

    async def respond(request):
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(outputs.pop(0))}}]})

    provider = LocalReviewProvider(source_paths=["math.cjs"], public_docs="Adds finite numbers.",
                                   transport=httpx.MockTransport(respond))
    snapshot = _snapshot({"math.cjs": "exports.add = (a, b) => a + b;"})
    assert await provider.hypothesize(snapshot, "Review") == []
    assert len(await provider.hypothesize(snapshot, "Review")) == 1
    assert len(provider.traces) == 2


@pytest.mark.parametrize("content", ['{"hypotheses":[]}', '{"hypotheses":'])
async def test_provider_rejects_length_finish_even_when_json_is_parseable(tmp_path, content):
    raw = json.dumps({"choices": [{"finish_reason": "length", "message": {"content": content}}]}).encode()
    calls = []

    async def respond(request):
        calls.append(request)
        return httpx.Response(200, content=raw)

    provider = LocalReviewProvider(source_paths=["math.cjs"], public_docs="Adds finite numbers.",
                                   evidence_dir=tmp_path, transport=httpx.MockTransport(respond))
    with pytest.raises(ProviderResponseError, match="^response_truncated$") as caught:
        await provider.hypothesize(_snapshot({"math.cjs": "exports.add = (a, b) => a + b;"}), "Review")
    assert caught.value.code == "response_truncated"
    assert len(calls) == 1
    assert (tmp_path / "001-hypothesize-response.json").read_bytes() == raw


@pytest.mark.parametrize("envelope", [
    None, {}, {"choices": []}, {"choices": [None]},
    {"choices": [{"finish_reason": "stop", "message": {"content": None}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": "private malformed text"}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": "[]"}}]},
    {"choices": [{"finish_reason": "tool_calls", "message": {"content": '{"hypotheses":[]}'}}]},
    {"choices": [{"message": {"content": '{"hypotheses":[]}'}}]},
])
async def test_provider_malformed_envelopes_have_safe_reason_and_preserve_raw(tmp_path, envelope):
    raw = json.dumps(envelope).encode()

    async def respond(request):
        return httpx.Response(200, content=raw)

    provider = LocalReviewProvider(source_paths=["math.cjs"], public_docs="Adds finite numbers.",
                                   evidence_dir=tmp_path, transport=httpx.MockTransport(respond))
    with pytest.raises(ProviderResponseError, match="^invalid_provider_output$"):
        await provider.hypothesize(_snapshot({"math.cjs": "exports.add = (a, b) => a + b;"}), "Review")
    assert len(provider.traces) == 1
    assert (tmp_path / "001-hypothesize-response.json").read_bytes() == raw


@pytest.mark.parametrize("mutation", ["two_hypotheses", "long_claim", "long_evidence", "wrong_type", "extra_field"])
async def test_provider_enforces_concise_schema_without_truncating(tmp_path, mutation):
    hypothesis = {"claim": "Neutral defect", "path": "math.cjs", "start_line": 1, "end_line": 1,
                  "evidence": "add", "reproduction": {"code": "assert.equal(1, 2);"}}
    body = {"hypotheses": [hypothesis]}
    if mutation == "two_hypotheses":
        body["hypotheses"].append(dict(hypothesis))
    elif mutation == "long_claim":
        hypothesis["claim"] = "c" * 321
    elif mutation == "long_evidence":
        hypothesis["evidence"] = "e" * 321
    elif mutation == "wrong_type":
        hypothesis["start_line"] = "1"
    else:
        hypothesis["private_reasoning"] = "private malformed detail"
    raw = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(body)}}]}).encode()

    async def respond(request):
        return httpx.Response(200, content=raw)

    provider = LocalReviewProvider(source_paths=["math.cjs"], public_docs="Adds finite numbers.",
                                   evidence_dir=tmp_path, transport=httpx.MockTransport(respond))
    with pytest.raises(ProviderResponseError, match="^invalid_provider_output$"):
        await provider.hypothesize(_snapshot({"math.cjs": "exports.add = (a, b) => a + b;"}), "Review")
    assert (tmp_path / "001-hypothesize-response.json").read_bytes() == raw


@pytest.mark.parametrize("code", ["assert.equal(1, 2);\n" * 21, " " * 4001])
async def test_provider_reproduction_budget_has_distinct_safe_code(code):
    body = {"hypotheses": [{"claim": "Neutral defect", "path": "math.cjs", "start_line": 1, "end_line": 1,
                            "evidence": "add", "reproduction": {"code": code}}]}

    async def respond(request):
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(body)}}]})

    provider = LocalReviewProvider(source_paths=["math.cjs"], public_docs="Adds finite numbers.",
                                   transport=httpx.MockTransport(respond))
    with pytest.raises(ProviderResponseError, match="^reproduction_budget$"):
        await provider.hypothesize(_snapshot({"math.cjs": "exports.add = (a, b) => a + b;"}), "Review")


async def test_concise_guidance_preserves_existing_twenty_line_hard_limit():
    code = "\n".join(["const x = 1;"] + ["assert.equal(x, 1);"] * 19)
    body = {"hypotheses": [{"claim": "c" * 320, "path": "math.cjs", "start_line": 1, "end_line": 1,
                            "evidence": "add", "reproduction": {"code": code}}]}

    async def respond(request):
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(body)}}]})

    provider = LocalReviewProvider(source_paths=["math.cjs"], public_docs="Adds finite numbers.",
                                   transport=httpx.MockTransport(respond))
    hypotheses = await provider.hypothesize(_snapshot({"math.cjs": "exports.add = (a, b) => a + b;"}), "Review")
    assert hypotheses[0].reproduction.code == code
    system = json.loads(provider.traces[0].request)["messages"][0]["content"]
    assert "<=12-line" in system
    assert "inner monologue" in system
    assert "320 characters" in system
    assert "chat_template_kwargs" not in json.loads(provider.traces[0].request)


async def test_loads_real_source_and_async_assertions(host, library):
    before = (library / "math.cjs").read_bytes()
    result = await host.reproduce(library, ReproductionSpec(code=(
        "const m = await load('math.cjs');\n"
        "assert.equal(m.default.add(2, 3), 5);\n"
        "assert.deepEqual(m.default.values(), [1, {value: 2}]);\n"
        "await assert.rejects(m.default.reject(), /expected refusal/);\n"
        "await new Promise(resolve => setTimeout(resolve, 5));\n"
        "const controller = new AbortController();\n"
        "controller.abort();\n"
        "assert.equal(controller.signal.aborted, true);"
    )))
    assert result.outcome == "pass", result.evidence
    assert result.assertions == 4
    assert (library / "math.cjs").read_bytes() == before
    assert list(library.iterdir()) == [library / "math.cjs"]


async def test_normal_functions_generators_and_relative_entry_alias(host, library):
    result = await host.reproduce(library, ReproductionSpec(code=(
        "const m = await load('./math.cjs');\n"
        "function increment(value) { return m.default.add(value, 1); }\n"
        "function* values() { yield increment(2); }\n"
        "assert.deepEqual(Array.from(values()), [3]);"
    )))
    assert result.outcome == "pass", result.evidence
    assert result.assertions == 1


async def test_standard_error_expectations_work_for_both_realms(host, library):
    result = await host.reproduce(library, ReproductionSpec(code=(
        "const m = await load('./math.cjs');\n"
        "assert.throws(() => m.default.invalid(), TypeError);\n"
        "await assert.rejects(m.default.invalidAsync(), TypeError);\n"
        "assert.throws(() => { throw new TypeError('local invalid'); }, TypeError);\n"
        "await assert.rejects(Promise.reject(new TypeError('local invalid')), TypeError);"
    )))
    assert result.outcome == "pass", result.evidence
    assert result.assertions == 4


async def test_caught_async_assertion_still_fails_but_expected_nested_failure_passes(host, library):
    swallowed = await host.reproduce(library, ReproductionSpec(code=(
        "try { await assert.rejects(Promise.resolve(1)); } catch (error) {}\n"
        "assert.equal(1, 1);"
    )))
    assert swallowed.outcome == "assertion_failure", swallowed.evidence
    assert swallowed.assertions == 2
    expected = await host.reproduce(library, ReproductionSpec(code="assert.throws(() => assert.equal(1, 2));"))
    assert expected.outcome == "pass", expected.evidence
    assert expected.assertions == 2


@pytest.mark.parametrize(("code", "outcome", "assertions"), [
    ("assert.equal(1, 2);", "assertion_failure", 1),
    ("const e = new Error('fabricated'); e.name = 'AssertionError'; throw e;", "error", 0),
    ("assert.equal(1, 1); const e = new Error('fabricated'); e.name = 'AssertionError'; throw e;", "error", 1),
    ("await assert.rejects(Promise.reject(new Error('ordinary runtime')), TypeError);", "assertion_failure", 1),
    ("await assert.rejects(() => { const e = new Error('fake'); e.name = 'AssertionError'; throw e; });", "error", 1),
    ("assert.throws(3);", "error", 1),
    ("throw new Error('ordinary runtime');", "error", 0),
    ("throw new SyntaxError('runtime fake syntax');", "error", 0),
    ("const = 3;", "syntax_error", 0),
    ("await load('unlisted.cjs');", "import_error", 0),
    ("const x = 1;", "pass", 0),
])
async def test_host_classifies_only_tracked_real_assertion_errors(host, library, code, outcome, assertions):
    result = await host.reproduce(library, ReproductionSpec(code=code))
    assert result.outcome == outcome, result.evidence
    assert result.assertions == assertions


@pytest.mark.parametrize(("contents", "outcome"), [
    ("throw new Error('library import failed');", "import_error"),
    ("exports.add = (;", "syntax_error"),
    ("throw 'string error';", "import_error"),
])
async def test_source_load_failures_are_not_defect_proof(host, library, contents, outcome):
    (library / "math.cjs").write_text(contents, encoding="utf-8")
    result = await host.reproduce(library, ReproductionSpec(code="await load('math.cjs'); assert.equal(1, 2);"))
    assert result.outcome == outcome, result.evidence
    assert result.assertions == 0


@pytest.mark.parametrize("code", [
    "await import('node:fs');", "require('fs');", "process.exit(0);", "globalThis.x = 1;",
    "eval('1');", "Function('return 1')();", "assert.constructor('return 1')();",
    "assert['constr' + 'uctor']('return 1')();", "await fetch('https://example.com');",
    "assert['\\u0063onstructor']('return 1')();", "Reflect.get(assert, 'x');",
])
async def test_capability_requests_do_not_pass(host, library, code):
    result = await host.reproduce(library, ReproductionSpec(code=code))
    assert result.outcome == "error", result.evidence


async def test_subprocess_timeout_and_cancellation_are_reaped(host, library, monkeypatch):
    children = []
    create = asyncio.create_subprocess_exec

    async def track(*args, **kwargs):
        child = await create(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", track)
    host.timeout_s = 0.4
    result = await host.reproduce(library, ReproductionSpec(code="await new Promise(() => {});"))
    # An unresolved promise with no active handles lets Node exit without output;
    # use an actual active timer below to require timeout termination.
    assert result.outcome in {"error", "timeout"}
    result = await host.reproduce(library, ReproductionSpec(code="await new Promise(r => setTimeout(r, 10000));"))
    assert result.outcome == "timeout"
    assert all(child.returncode is not None for child in children)
    host.timeout_s = 10
    task = asyncio.create_task(host.reproduce(library, ReproductionSpec(code="await new Promise(r => setTimeout(r, 10000));")))
    for _ in range(100):
        if len(children) == 3:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(children) == 3
    assert children[-1].returncode is not None


async def test_synchronous_infinite_loop_is_terminated(host, library):
    host.timeout_s = 0.5
    result = await host.reproduce(library, ReproductionSpec(code="while (true) {}"))
    assert result.outcome == "timeout", result.evidence


async def test_cancellation_terminates_ready_descendant_process(host, library):
    (library / "child.cjs").write_text(
        "const fs = require('node:fs');\n"
        "fs.writeFileSync('heartbeat', 'started');\n"
        "fs.writeFileSync('ready', 'ready');\n"
        "setInterval(() => fs.writeFileSync('heartbeat', String(Date.now())), 25);\n",
        encoding="utf-8",
    )
    (library / "parent.cjs").write_text(
        "const {spawn} = require('node:child_process');\n"
        "spawn(process.execPath, ['child.cjs'], {stdio: 'ignore'});\n"
        "setInterval(() => {}, 1000);\n",
        encoding="utf-8",
    )
    host.timeout_s = 30
    host.regression_argv = (host.node, "parent.cjs")
    task = asyncio.create_task(host.regress(library))
    try:
        # Cancellation is triggered by the *descendant's* readiness marker,
        # independent of machine load or cold Node startup time.
        async with asyncio.timeout(20):
            while not (library / "ready").exists():
                await asyncio.sleep(0.025)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    before = (library / "heartbeat").read_bytes()
    await asyncio.sleep(0.25)
    assert (library / "heartbeat").read_bytes() == before


async def test_node_regression_requires_executed_nonempty_checks(host, library):
    (library / "existing.cjs").write_text(
        "const test = require('node:test');\n"
        "const assert = require('node:assert/strict');\n"
        "test('addition preserves ordinary values', () => { assert.equal(require('./math.cjs').add(2, 3), 5); });\n",
        encoding="utf-8",
    )
    host.regression_argv = (host.node, "--test", "--test-reporter=tap", "existing.cjs")
    passing = await host.regress(library)
    assert passing.outcome == "pass", passing.evidence
    assert passing.assertions == 1
    (library / "math.cjs").write_text("exports.add = (a, b) => a - b;\n", encoding="utf-8")
    failing = await host.regress(library)
    assert failing.outcome == "assertion_failure", failing.evidence
    assert failing.assertions == 1
    (library / "existing.cjs").write_text("// No tests\n", encoding="utf-8")
    empty = await host.regress(library)
    assert empty.outcome == "error"
    assert empty.assertions == 0


async def test_unconfigured_existing_checks_are_infrastructure_error(host, library):
    result = await host.regress(library)
    assert result.outcome == "error"
    assert result.assertions == 0
