"""Portable line generation, isolated feedback, native protocol and workflow tests."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from decilist import triage_hypothesis_v6 as legacy
from decilist import triage_line_edits_v6 as protocol
from decilist import triage_line_hypothesis_v6 as lines
from decilist import triage_lines_v6 as selected
from decilist import triage_v6 as workflow
from benchmarks import run_triage_v6 as measurement


def context(source="def compute(value):\n    return value + 1\n", *, start=10, path="src/sample.py", roles=None):
    row = {"id": "f1", "evidence_id": "s1", "path": path, "symbol": "compute", "start_line": start,
        "end_line": start + len(protocol.physical_lines(source)) - 1, "source": source, "complete": True, "editable": True,
        "guard_reasons": [], "roles": roles or ["implementation"], "runtime_observed": True}
    return {"schema_version": 6, "kind": "runtime_python_context", "functions": [row], "events": [], "call_graph": [],
        "failures": [{"evidence_id": "failure1", "nodeid": "tests/test.py::test", "phase": "call", "longrepr": "assert1==2"}],
        "evidence_ids": ["s1", "failure1"], "edit_allowlist": [{"function_id": "f1", **{key: row[key] for key in ("path", "evidence_id", "start_line", "end_line")}}]}


class NativeStub:
    def __init__(self,replacements=("        return 4",)):
        self.replacements,self.calls,self.messages=replacements,[],[]
        self.template="line stub template"
        self.config={"model":"line-stub","expected_build":"b11889-d854d98ba","generation_profile":"qwen3_coder_non_reasoning",
                     "expected_chat_template_sha256":hashlib.sha256(self.template.encode()).hexdigest()}

    def handle(self,request):
        path=request.url.path;body=json.loads(request.content) if request.content else None
        self.calls.append((path,body))
        if path=="/health":result={"status":"ok"}
        elif path=="/props":result={"model_alias":"line-stub","build_info":"b11889-d854d98ba","model_path":"models/stub.gguf","is_sleeping":False,"chat_template":self.template,"default_generation_settings":{"n_ctx":16384}}
        elif path=="/v1/models":result={"data":[{"id":"line-stub","meta":{"n_vocab":256,"n_ctx":16384,"n_ctx_train":32768}}]}
        elif path=="/apply-template":
            self.messages=body["messages"]
            result={"prompt":"".join("<|im_start|>"+m["role"]+"\n"+m["content"]+"<|im_end|>\n" for m in self.messages)+"<|im_start|>assistant\n"}
        elif path=="/completion":
            view=json.loads(self.messages[1]["content"])["runtime_context"]
            row=next(r for r in view["functions"] if "editable_lines" in r)
            number=next(int(line.split(" | ",1)[0]) for line in row["editable_lines"].splitlines() if "return 3" in line or "return value + 1" in line)
            count=sum(p=="/completion" for p,_ in self.calls)-1
            raw={"hypothesis":"The selected branch returns a smaller amount than the visible assertion expects.","failure_evidence_id":view["failures"][0]["evidence_id"],"evidence_ids":[],"expected_test_behavior":"The selected assertion would observe the requested amount.","edits":[{"source_evidence_id":row["evidence_id"],"start_line":number,"end_line":number,"replacement":self.replacements[min(count,len(self.replacements)-1)]}]}
            result={"model":"line-stub","content":json.dumps(raw),"truncated":False,"tokens_predicted":130,"tokens_evaluated":600,"stop":True,"stop_type":"eos","stopped_limit":False,"timings":{"predicted_ms":1}}
        else:raise AssertionError(path)
        return httpx.Response(200,json=result)

    def provider(self):
        return legacy.V6LlamaCppProvider(**self.config,expected_model_path="models/stub.gguf",transport=httpx.MockTransport(self.handle))


def test_generator_keeps_actual_raw_lines_and_canonical_internal_result_without_mutation():
    async def exercise():
        stub=NativeStub(("    return value",));adapter=lines.LineEditGenerator()
        before=(legacy.generate_hypothesis,legacy.SYSTEM,workflow.run_triage_v6)
        async with stub.provider() as provider:report=await adapter.generate_hypothesis(context(),provider=provider)
        assert report["status"]=="hypothesis",report
        assert "edits" in json.loads(report["raw_output"]) and "replacements" in report["result"]
        assert report["result"]["replacements"][0]["new"]=="    return value\n"
        assert before==(legacy.generate_hypothesis,legacy.SYSTEM,workflow.run_triage_v6)
        assert stub.calls[-1][1]["n_predict"]==legacy.MAX_TOKENS
    asyncio.run(exercise())


def test_generator_retains_selected_failure_for_abstention_without_canonical_citations():
    class AbstainStub(NativeStub):
        def handle(self,request):
            response=super().handle(request)
            if request.url.path=="/completion":
                data=response.json()
                data["content"]=json.dumps({"hypothesis":None,"failure_evidence_id":"failure1",
                    "evidence_ids":[],"expected_test_behavior":None,"edits":[]})
                return httpx.Response(200,json=data)
            return response
    async def exercise():
        stub=AbstainStub();adapter=lines.LineEditGenerator()
        async with stub.provider() as provider:report=await adapter.generate_hypothesis(context(),provider=provider)
        assert report["reason"]=="unsupported_hypothesis"
        assert report["line_result"]["failure_evidence_id"]=="failure1"
        assert report["result"]["hypothesis"] is None and report["result"]["evidence_ids"]==[]
    asyncio.run(exercise())


def test_feedback_remains_canonical_and_repeated_source_interventions_are_rejected():
    async def exercise():
        stub=NativeStub(("    return value","    return value"));adapter=lines.LineEditGenerator()
        async with stub.provider() as provider:
            first=await adapter.generate_hypothesis(context(),provider=provider)
            feedback={"attempt":1,"previous_result":first["result"],
                "rejection":{"kind":"test_rejection","exit_code":1,"output":"observed assertion mismatch"}}
            second=await adapter.generate_hypothesis(context(),provider=provider,feedback=feedback)
        assert first["status"]=="hypothesis" and second["reason"]=="repeated_intervention"
        assert "edits" in json.loads(stub.messages[2]["content"])
        assert second["generation_adapter_path"].endswith("triage_line_hypothesis_v6.py")
    asyncio.run(exercise())


@pytest.mark.parametrize("crlf",[False,True])
def test_workflow_runs_unchanged_checks_and_feedback_then_preserves_internal_contract(tmp_path,crlf):
    root=tmp_path/"project";(root/"src").mkdir(parents=True);(root/"tests").mkdir()
    source="def compute(value):\n    if value == 2:\n        return 3\n    return value + 1\n"
    initial=source.replace("\n","\r\n").encode() if crlf else source.encode()
    (root/"src/sample.py").write_bytes(initial)
    (root/"tests/test_failure.py").write_bytes(b"from sample import compute\ndef test_failure():\n    assert compute(2) == 4\n")
    (root/"tests/test_regression.py").write_bytes(b"from sample import compute\ndef test_regression():\n    assert compute(0) == 1\n")
    stub=NativeStub(("        return 5","        return 4"));config=tmp_path/"config.json";config.write_text(json.dumps(stub.config))
    async def exercise():
        async with stub.provider() as provider:return await selected.run_triage_v6(root,["tests/test_failure.py"],["tests/test_regression.py"],tmp_path/"run",config,provider=provider)
    report=asyncio.run(exercise())
    assert report["state"]==workflow.SUPPORTED,report
    assert len(report["attempts"])==2 and report["attempts"][0]["candidate"]["state"]=="failed"
    assert report["attempts"][1]["candidate"]["state"]==report["attempts"][1]["regression"]["state"]=="passed"
    assert (root/"src/sample.py").read_bytes()==initial and not report["apply"]["applied"]
    replacement=report["attempts"][1]["hypothesis"]["replacements"][0]
    eol="\r\n" if crlf else "\n"
    assert replacement=={"path":"src/sample.py","old":"        return 3"+eol,"new":"        return 4"+eol}
    assert report["original_unchanged_at_end"] and report["verification"]["protected_file_hashes_unchanged"]
    assert "edits" in json.loads(stub.messages[2]["content"])
    persisted=json.loads(Path(report["report_path"]).read_text())
    assert persisted["edit_protocol"]==report["edit_protocol"]
    assert persisted["generation_module"]=="decilist.triage_line_hypothesis_v6"
    assert persisted["generation_specification"]==lines.specification()


def rejection(previous):
    return {"attempt": 1, "previous_result": copy.deepcopy(previous),
        "rejection": {"kind": "test_rejection", "exit_code": 1, "output": "Observed assertion mismatch"}}


def test_selected_specification_and_native_provider_identity_are_consistent():
    assert lines.V6LlamaCppProvider is legacy.V6LlamaCppProvider
    assert lines.validate_generation_mode_proof is legacy.validate_generation_mode_proof
    assert lines.MODE_PROBE_MESSAGES is legacy.MODE_PROBE_MESSAGES
    assert lines.specification()["schema"] == protocol.LineHypothesis.model_json_schema()
    assert lines.specification()["system"] == lines.SYSTEM
    assert lines.specification()["version"] == protocol.VERSION

def test_measurement_resolves_the_selected_executed_specification_without_case_access():
    assert measurement._generator_module() is lines
    assert measurement._workflow_module() is selected
    spec = measurement._specification()
    assert spec["hypothesis"] == lines.specification()
    assert spec["hypothesis"]["version"] == "evidence_absolute_lines_verbatim.2"
    assert "edits" in spec["hypothesis"]["schema"]["properties"]
    assert "replacements" not in spec["hypothesis"]["schema"]["properties"]
    assert spec["workflow_version"] == selected.VERSION


def test_separate_concurrent_runs_do_not_share_feedback_or_projection_state():
    async def exercise():
        a, b = lines.LineEditGenerator(), lines.LineEditGenerator()
        native_a, native_b = NativeStub(("    return value",)), NativeStub(("    return value + 2",))
        supplied_a, supplied_b = context(), context(start=30)
        before = (legacy.SYSTEM, legacy.generate_hypothesis, workflow.run_triage_v6)
        async with native_a.provider() as provider_a, native_b.provider() as provider_b:
            first_a, first_b = await asyncio.gather(
                a.generate_hypothesis(supplied_a, provider=provider_a),
                b.generate_hypothesis(supplied_b, provider=provider_b))
            assert first_a["status"] == first_b["status"] == "hypothesis"
            assert a.projection is not b.projection
            assert a.last_line["edits"][0]["start_line"] == 11
            assert b.last_line["edits"][0]["start_line"] == 31
            calls_before = len(native_b.calls)
            wrong = await b.generate_hypothesis(supplied_b, provider=provider_b, feedback=rejection(first_a["result"]))
            assert wrong["status"] == "invalid_input" and len(native_b.calls) == calls_before
            assert b.last_canonical_hash == legacy._fingerprint(first_b["result"])
        assert before == (legacy.SYSTEM, legacy.generate_hypothesis, workflow.run_triage_v6)
    asyncio.run(exercise())


def test_actual_test_feedback_uses_line_json_and_commits_only_valid_distinct_candidate():
    async def exercise():
        adapter = lines.LineEditGenerator()
        native = NativeStub(("    return value", "    return value + 2"))
        supplied = context()
        async with native.provider() as provider:
            first = await adapter.generate_hypothesis(supplied, provider=provider)
            first_line = copy.deepcopy(adapter.last_line)
            second = await adapter.generate_hypothesis(supplied, provider=provider, feedback=rejection(first["result"]))
            assert second["status"] == "hypothesis" and adapter.last_line != first_line
            assert json.loads(native.messages[2]["content"]) == first_line
            assert json.loads(native.messages[3]["content"])["rejection"]["kind"] == "test_rejection"
            assert second["specification_sha256"] == legacy._fingerprint(lines.specification())
            count = len(native.calls)
            third = await adapter.generate_hypothesis(supplied, provider=provider, feedback=rejection(second["result"]))
            assert third["status"] == "invalid_input" and len(native.calls) == count
    asyncio.run(exercise())


def test_rejected_revision_retains_raw_without_overwriting_previous_valid_state():
    async def exercise():
        adapter, native = lines.LineEditGenerator(), NativeStub(("    return value", "    return value"))
        async with native.provider() as provider:
            first = await adapter.generate_hypothesis(context(), provider=provider)
            accepted = copy.deepcopy(adapter.last_line)
            second = await adapter.generate_hypothesis(context(), provider=provider, feedback=rejection(first["result"]))
        assert second["reason"] == "repeated_intervention" and second["raw_completion"]
        assert "edits" in json.loads(second["raw_output"])
        assert second["line_result"] is None and adapter.last_line == accepted
    asyncio.run(exercise())


@pytest.mark.parametrize("change", ["context", "non_test", "new_run", "second_initial"])
def test_invalid_feedback_or_state_reuse_stops_before_any_additional_native_request(change):
    async def exercise():
        adapter, native = lines.LineEditGenerator(), NativeStub(("    return value",))
        supplied = context()
        async with native.provider() as provider:
            first = await adapter.generate_hypothesis(supplied, provider=provider)
            feedback = rejection(first["result"])
            if change == "context":
                supplied["note"] = "Different retained runtime context"
            elif change == "non_test":
                feedback["rejection"]["kind"] = "validation_rejection"
            elif change == "new_run":
                adapter = lines.LineEditGenerator()
            else:
                feedback = None
            count = len(native.calls)
            report = await adapter.generate_hypothesis(supplied, provider=provider, feedback=feedback)
            assert report["status"] == "invalid_input" and len(native.calls) == count
    asyncio.run(exercise())


def test_overlapping_calls_on_one_adapter_fail_closed_without_changing_the_first():
    async def exercise():
        adapter, native = lines.LineEditGenerator(), NativeStub(("    return value",))
        started, release = asyncio.Event(), asyncio.Event()
        async def handler(request):
            if request.url.path == "/completion":
                started.set()
                await release.wait()
            return native.handle(request)
        provider = legacy.V6LlamaCppProvider(**native.config, expected_model_path="models/stub.gguf",
            transport=httpx.MockTransport(handler))
        async with provider:
            first = asyncio.create_task(adapter.generate_hypothesis(context(), provider=provider))
            await asyncio.wait_for(started.wait(), 2)
            count = len(native.calls)
            with pytest.raises(ValueError, match="sequential"):
                await adapter.generate_hypothesis(context(), provider=provider)
            assert len(native.calls) == count
            release.set()
            assert (await first)["status"] == "hypothesis"
    asyncio.run(exercise())


def test_selected_cli_uses_explicit_workflow_seam_and_legacy_main_stays_available(monkeypatch):
    observed = {}
    def cli(argv, *, _workflow=None):
        observed.update(argv=argv, workflow=_workflow)
        return 7
    monkeypatch.setattr(workflow, "main", cli)
    assert selected.main(["--help"]) == 7
    assert observed == {"argv": ["--help"], "workflow": selected.run_triage_v6}


def test_selected_workflow_rejects_external_generator_override():
    with pytest.raises(TypeError, match="owns its per-run generator"):
        asyncio.run(selected.run_triage_v6(_generator=lambda: None))
