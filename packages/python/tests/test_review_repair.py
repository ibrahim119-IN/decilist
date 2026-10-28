import asyncio
import json
import shutil
from pathlib import Path

import pytest

from decilist.review_repair import (
    DefectHypothesis, ExecutionResult, LineEdit, RepairPolicy, ReproductionSpec,
    ReviewRepairEngine, source_sha256,
)


SOURCE = "module.exports.divide = function divide(numerator, denominator) {\n    return numerator / (denominator || 1);\n};\n"
BROKEN_LINE = "    return numerator / (denominator || 1);"
FIX = "    if (denominator === 0) throw new RangeError('zero denominator');\n    return numerator / denominator;"
SOURCES = {"src/math.cjs": SOURCE, "package.json": '{"name":"proof-host-test","private":true}\n'}
REPRODUCTION = "const {divide} = load('src/math.cjs'); assert.throws(() => divide(6, 0), RangeError);"


def hypothesis(**changes):
    return DefectHypothesis(**({"claim": "Zero denominator must throw a RangeError.", "path": "src/math.cjs",
                                "start_line": 2, "end_line": 2, "evidence": BROKEN_LINE,
                                "reproduction": ReproductionSpec(code=REPRODUCTION)} | changes))


def edit(replacement=FIX, **changes):
    return LineEdit(**({"path": "src/math.cjs", "start_line": 2, "end_line": 2,
                       "source_sha256": source_sha256(SOURCE), "replacement": replacement} | changes))


class Provider:
    def __init__(self, hypotheses=None, edits=None):
        self.hypotheses = hypotheses if hypotheses is not None else [hypothesis()]
        self.edits = edits if edits is not None else [[edit()]]
        self.repair_calls = []
        self.snapshots = []

    async def hypothesize(self, snapshot, task):
        self.snapshots.append(snapshot)
        return self.hypotheses

    async def repair(self, snapshot, proposed, baseline):
        self.snapshots.append(snapshot)
        self.repair_calls.append((proposed, baseline))
        return self.edits[len(self.repair_calls) - 1]


class Host:
    def __init__(self, *, baseline_outcome="assertion_failure", baseline_assertions=1,
                 baseline_checks="pass", regression=False, baseline_source=SOURCE):
        self.baseline_outcome, self.baseline_assertions = baseline_outcome, baseline_assertions
        self.baseline_checks, self.regression = baseline_checks, regression
        self.baseline_source = baseline_source
        self.calls = []

    async def reproduce(self, workspace, spec):
        source = (workspace / "src/math.cjs").read_text(encoding="utf-8")
        self.calls.append(("reproduce", workspace, source, spec.code))
        if source == self.baseline_source:
            return ExecutionResult(outcome=self.baseline_outcome, assertions=self.baseline_assertions,
                                   evidence="Independent host observation")
        return ExecutionResult(outcome="pass" if "throw new RangeError" in source else "assertion_failure", assertions=1)

    async def regress(self, workspace):
        source = (workspace / "src/math.cjs").read_text(encoding="utf-8")
        self.calls.append(("regress", workspace, source, None))
        outcome = self.baseline_checks if source == self.baseline_source else "assertion_failure" if self.regression else "pass"
        return ExecutionResult(outcome=outcome, assertions=2)


async def run(tmp_path, provider=None, host=None, **kwargs):
    return await ReviewRepairEngine(provider or Provider(), host or Host(), kwargs.pop("policy", None)).run(
        kwargs.pop("sources", SOURCES), task="Reject division by zero while preserving other arithmetic.",
        allowed_paths=kwargs.pop("allowed_paths", {"src/math.cjs"}), scratch_root=tmp_path, **kwargs)


async def test_requires_host_proof_before_edit_and_preserves_input(tmp_path):
    provider, host = Provider(), Host()
    original = dict(SOURCES)
    result = await run(tmp_path, provider, host)
    assert result.status == "accepted_candidate"
    assert [call[0] for call in host.calls] == ["regress", "reproduce", "reproduce", "regress"]
    assert provider.repair_calls[0][1].outcome == "assertion_failure"
    assert result.attempts[0].baseline_regression.outcome == "pass"
    assert result.attempts[0].candidate_regression.assertions == 2
    assert result.latency_ms >= 0 and result.attempts[0].timings_ms.keys() >= {"baseline_reproduction", "repair"}
    assert result.human_time_claim == "not_measured"
    assert SOURCES == original
    candidate = Path(result.candidate_workspace)
    assert candidate.parent == tmp_path
    assert (candidate / "src/math.cjs").read_text(encoding="utf-8") != SOURCE
    assert (candidate / "package.json").read_text(encoding="utf-8") == SOURCES["package.json"]
    snapshot = provider.snapshots[0]
    assert snapshot.sources == SOURCES and snapshot.source_hashes["src/math.cjs"] == source_sha256(SOURCE)
    with pytest.raises(TypeError):
        snapshot.sources["src/math.cjs"] = "mutate"


async def test_empty_hypotheses_is_an_ordinary_abstention(tmp_path):
    provider, host = Provider([]), Host()
    result = await run(tmp_path, provider, host)
    assert result.status == "unproven" and result.reason == "no_hypothesis"
    assert result.attempts == [] and not host.calls and not provider.repair_calls


@pytest.mark.parametrize("prefix", [
    "    function ordinary() { return 1; }\n",
    "    setTimeout(() => {}, 0);\n",
    "    setInterval(() => {}, 1000);\n",
    "    setImmediate(() => {});\n",
])
async def test_ordinary_functions_and_timer_calls_are_allowed(tmp_path, prefix):
    result = await run(tmp_path, Provider(edits=[[edit(prefix + FIX)]]))
    assert result.status == "accepted_candidate"


@pytest.mark.parametrize("outcome,assertions,status", [
    ("pass", 1, "unproven"), ("assertion_failure", 0, "unproven"),
    ("import_error", 1, "infrastructure_failure"), ("syntax_error", 0, "infrastructure_failure"),
    ("timeout", 0, "infrastructure_failure"), ("error", 1, "infrastructure_failure"),
])
async def test_non_assertion_failures_are_never_defect_proof(tmp_path, outcome, assertions, status):
    provider = Provider()
    result = await run(tmp_path, provider, Host(baseline_outcome=outcome, baseline_assertions=assertions))
    assert result.status == status and result.candidate_workspace is None
    assert provider.repair_calls == []
    assert result.attempts[0].edits == []


async def test_existing_checks_must_pass_before_reproduction_or_repair(tmp_path):
    provider, host = Provider(), Host(baseline_checks="assertion_failure")
    result = await run(tmp_path, provider, host)
    assert result.status == "infrastructure_failure"
    assert result.reason == "baseline_checks_not_passing"
    assert [call[0] for call in host.calls] == ["regress"] and not provider.repair_calls


async def test_rejected_candidate_does_not_accumulate_into_next_hypothesis(tmp_path):
    provider = Provider([hypothesis(), hypothesis()], [[edit("    return 999;")], [edit()]])
    host = Host()
    result = await run(tmp_path, provider, host)
    assert result.status == "accepted_candidate"
    assert [attempt.status for attempt in result.attempts] == ["unproven", "accepted_candidate"]
    assert len(provider.repair_calls) == 2
    baseline_sources = [source for stage, workspace, source, _ in host.calls if "baseline" in workspace.name]
    assert baseline_sources and all(source == SOURCE for source in baseline_sources)
    candidates = [workspace for stage, workspace, _, _ in host.calls if stage == "reproduce" and "candidate" in workspace.name]
    assert len(candidates) == len(set(candidates)) == 2
    assert "999" not in (Path(result.candidate_workspace) / "src/math.cjs").read_text(encoding="utf-8")


async def test_passing_reproduction_does_not_override_regression_failure(tmp_path):
    result = await run(tmp_path, host=Host(regression=True))
    assert result.status == "regression" and result.candidate_workspace is None
    attempt = result.attempts[0]
    assert attempt.candidate_reproduction.outcome == "pass"
    assert attempt.candidate_regression.outcome == "assertion_failure"


@pytest.mark.parametrize("edits,reason", [
    ([edit(source_sha256="0" * 64)], "stale_source"),
    ([edit(start_line=1)], "edit_range"),
    ([edit(end_line=100)], "edit_range"),
    ([edit(), edit()], "overlapping_edits"),
    ([edit(), edit(), edit()], "edit_count"),
    ([edit("\n".join("    value++;" for _ in range(31)))], "changed_line_budget"),
    ([edit(BROKEN_LINE)], "unchanged_candidate"),
    ([edit(path="package.json")], "protected_path"),
])
async def test_invalid_line_edits_are_rejected_before_candidate_execution(tmp_path, edits, reason):
    host = Host()
    result = await run(tmp_path, Provider(edits=[edits]), host)
    assert result.status == "invalid" and result.reason == reason
    assert len(host.calls) == 2 and result.candidate_workspace is None


@pytest.mark.parametrize("replacement", [
    "import x from 'fs';", "const fs = require('node:fs');", "process.exit(0);", "fetch('https://example.com');",
    "eval('1');", "new Function('return 1')();", "value.constructor('return process')();",
    "globalThis['fetch']('x');", "value['con' + 'structor']('x');", r"const f = '\u0065val';",
    "readFileSync('x');", "new WebSocket('x');", "module.exports = {};",
])
async def test_added_capabilities_are_rejected(tmp_path, replacement):
    result = await run(tmp_path, Provider(edits=[[edit(replacement)]]))
    assert result.status == "invalid" and result.reason == "added_capability"


@pytest.mark.parametrize("changes,reason", [
    ({"path": "package.json"}, "protected_path"),
    ({"start_line": 2, "end_line": 100}, "evidence_range"),
    ({"evidence": "invented source evidence"}, "evidence_content"),
])
async def test_hypothesis_must_cite_allowed_source_content(tmp_path, changes, reason):
    provider, host = Provider([hypothesis(**changes)]), Host()
    result = await run(tmp_path, provider, host)
    assert result.status == "invalid" and result.reason == reason
    assert not host.calls and not provider.repair_calls


@pytest.mark.parametrize("source_prefix,quote_prefix", [
    ("    ", "\t"), ("\t", "    "), ("\t\t", " \t    "), ("    ", ""),
])
async def test_evidence_indentation_is_canonicalized_to_source_bytes(tmp_path, source_prefix, quote_prefix):
    body = BROKEN_LINE.lstrip(" \t")
    source_line = source_prefix + body
    source = SOURCE.replace(BROKEN_LINE, source_line)
    sources = SOURCES | {"src/math.cjs": source}
    raw = hypothesis(evidence=quote_prefix + body)
    provider = Provider([raw], [[edit(source_sha256=source_sha256(source))]])
    host = Host(baseline_source=source)
    result = await run(tmp_path, provider, host, sources=sources)
    assert result.status == "accepted_candidate"
    assert result.attempts[0].hypothesis.evidence == source_line
    assert provider.repair_calls[0][0].evidence == source_line
    assert raw.evidence == quote_prefix + body  # Provider/raw trace object stays verbatim.
    assert sources["src/math.cjs"] == source
    assert provider.snapshots[0].sources["src/math.cjs"] == source
    assert provider.snapshots[0].source_hashes["src/math.cjs"] == source_sha256(source)
    assert result.source_sha256 == provider.snapshots[0].sha256 == provider.snapshots[1].sha256
    baseline = host.calls[0][1]
    assert (baseline / "src/math.cjs").read_bytes() == source.encode("utf-8")


async def test_multiline_indentation_canonicalization_stays_in_cited_range(tmp_path):
    source_excerpt = "\tconst unit = denominator || 1;\n \treturn numerator / unit;"
    quoted_excerpt = "    const unit = denominator || 1;\n\t\treturn numerator / unit;"
    source = SOURCE.replace(BROKEN_LINE, source_excerpt)
    raw = hypothesis(start_line=2, end_line=3, evidence=quoted_excerpt)
    provider = Provider([raw], [[edit(end_line=3, source_sha256=source_sha256(source))]])
    result = await run(tmp_path, provider, Host(baseline_source=source), sources=SOURCES | {"src/math.cjs": source})
    assert result.status == "accepted_candidate"
    assert result.attempts[0].hypothesis.evidence == source_excerpt
    assert raw.evidence == quoted_excerpt


@pytest.mark.parametrize("quoted", [
    "\treturn numerator * (denominator || 1);",             # Changed operator.
    "\treturn numerator / (denominator || 2);",             # Changed token.
    "\treturn numerator / (denominator || '1');",           # Changed string/token.
    "\treturn numerator / (denominator || 1); // changed",   # Added comment.
    "\treturn numerator / (denominator || 1); ",             # Changed trailing whitespace.
    "\treturn numerator / (denominator || 1); extra();",     # Added trailing code.
    "\treturn numerator / (denominator || 1)",               # Omitted trailing token.
])
async def test_evidence_indentation_fallback_does_not_relax_source_body(tmp_path, quoted):
    provider, host = Provider([hypothesis(evidence=quoted)]), Host()
    result = await run(tmp_path, provider, host)
    assert result.status == "invalid" and result.reason == "evidence_content"
    assert not host.calls and not provider.repair_calls


async def test_evidence_indentation_cannot_match_outside_claimed_range(tmp_path):
    provider, host = Provider([hypothesis(start_line=1, end_line=1, evidence="\t" + BROKEN_LINE.lstrip())]), Host()
    result = await run(tmp_path, provider, host)
    assert result.status == "invalid" and result.reason == "evidence_content"
    assert not host.calls and not provider.repair_calls


async def test_evidence_indentation_does_not_bypass_stale_source_precondition(tmp_path):
    provider = Provider([hypothesis(evidence="\t" + BROKEN_LINE.lstrip())], [[edit(source_sha256="0" * 64)]])
    result = await run(tmp_path, provider)
    assert result.status == "invalid" and result.reason == "stale_source"


@pytest.mark.parametrize("path", ["../outside.cjs", "/absolute.js", "C:/outside.js", "src\\math.cjs", "tests/math.cjs", "src/math.test.cjs", "package.json"])
async def test_unsafe_or_protected_edit_scope_never_executes(tmp_path, path):
    provider, host = Provider(), Host()
    result = await run(tmp_path, provider, host, sources=SOURCES | {path: SOURCE}, allowed_paths={path})
    assert result.status == "invalid" and not provider.snapshots and not host.calls


@pytest.mark.parametrize("sources", [{"src/math.cjs": 42}, {42: SOURCE}, {}, {".decilist-proof/reproduction.cjs": "body"}])
async def test_invalid_source_manifest_is_structured_failure(tmp_path, sources):
    result = await run(tmp_path, sources=sources)
    assert result.status == "invalid" and result.reason == "invalid_input"


async def test_reproduction_body_cannot_be_changed_by_host(tmp_path):
    class MutatingHost(Host):
        async def reproduce(self, workspace, spec):
            (workspace / ".decilist-proof" / spec.filename).write_text("assert(true)", encoding="utf-8")
            return ExecutionResult(outcome="assertion_failure", assertions=1)
    provider = Provider()
    result = await run(tmp_path, provider, MutatingHost())
    assert result.status == "infrastructure_failure" and result.reason == "reproduction_mutated"
    assert not provider.repair_calls


async def test_source_mutation_by_host_never_becomes_accepted(tmp_path):
    class MutatingHost(Host):
        async def regress(self, workspace):
            (workspace / "package.json").write_text("{}", encoding="utf-8")
            return ExecutionResult(outcome="pass", assertions=2)
    result = await run(tmp_path, host=MutatingHost())
    assert result.status == "infrastructure_failure" and result.reason == "workspace_mutated"


async def test_host_exception_does_not_echo_private_source(tmp_path):
    class BrokenHost(Host):
        async def regress(self, workspace):
            raise RuntimeError("private source should not escape")
    result = await run(tmp_path, host=BrokenHost())
    assert result.status == "infrastructure_failure"
    assert "private source" not in result.model_dump_json()


async def test_host_timeout_is_bounded_and_cancellation_propagates(tmp_path):
    class SlowHost(Host):
        async def regress(self, workspace):
            await asyncio.sleep(10)
            return ExecutionResult(outcome="pass", assertions=1)
    result = await run(tmp_path, host=SlowHost(), policy=RepairPolicy(host_timeout_ms=1))
    assert result.status == "infrastructure_failure" and result.reason == "host_timeout"
    pending = asyncio.create_task(run(tmp_path, host=SlowHost()))
    await asyncio.sleep(0.01)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending


@pytest.mark.skipif(not shutil.which("node"), reason="Node is required for independent JS execution")
async def test_real_js_assertion_failure_and_minimal_repair(tmp_path):
    # This host is test-only. Production hosts need a genuine OS/runtime sandbox.
    runner = r"""
const realAssert = require('node:assert/strict');
const path = require('node:path');
const root = process.argv[1], body = process.argv[2];
let assertions = 0;
const assert = {
  equal: (...args) => { assertions++; realAssert.equal(...args); },
  throws: (...args) => { assertions++; realAssert.throws(...args); }
};
const load = name => { if (name !== 'src/math.cjs') throw new Error('blocked'); return require(path.join(root, name)); };
(async () => { try {
  await new (Object.getPrototypeOf(async function(){}).constructor)('load', 'assert', body)(load, assert);
  console.log(JSON.stringify({outcome: 'pass', assertions}));
} catch (error) {
  console.log(JSON.stringify({outcome: error.code === 'ERR_ASSERTION' ? 'assertion_failure' :
    error.code === 'MODULE_NOT_FOUND' ? 'import_error' : error.name === 'SyntaxError' ? 'syntax_error' : 'error', assertions}));
} })();
"""
    class NodeHost:
        async def execute(self, workspace, body):
            process = await asyncio.create_subprocess_exec(shutil.which("node"), "-e", runner, str(workspace), body,
                                                           stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                stdout, stderr = await process.communicate()
            except asyncio.CancelledError:
                process.kill()
                await process.wait()
                raise
            assert process.returncode == 0, stderr.decode()
            return ExecutionResult.model_validate(json.loads(stdout))

        async def reproduce(self, workspace, spec):
            return await self.execute(workspace, spec.code)

        async def regress(self, workspace):
            return await self.execute(workspace, "const {divide} = load('src/math.cjs'); assert.equal(divide(8,2),4); assert.equal(divide(0,2),0);")

    result = await run(tmp_path, host=NodeHost())
    assert result.status == "accepted_candidate"
    attempt = result.attempts[0]
    assert attempt.baseline_reproduction.outcome == "assertion_failure"
    assert attempt.baseline_reproduction.assertions == 1
    assert attempt.candidate_reproduction.outcome == "pass" and attempt.candidate_regression.assertions == 2
