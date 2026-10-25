from pathlib import Path
import json
import pytest

from decilist import Decilist, FunctionProvider, Policy
from decilist.contracts import Attempt, Decision
from decilist.triage import ROUTES
from decilist.triage_evidence import prepare_evidence
from decilist import triage_assistant as assistant


def failure(log="AssertionError: expected two but got one\nFAILED tests/test_app.py::test_result - AssertionError"):
    return prepare_evidence(log)


class LocalClient:
    locality = "local"
    def __init__(self, *, route="application_logic", abstain=False, error=False):
        self.route, self.abstain, self.error = route, abstain, error
        self.calls = []
    async def ready(self):
        return {"provider": "liquid", "ready": True}
    async def choose(self, state, instructions, options, *, policy):
        self.calls.append({"state": state, "instructions": instructions, "options": options, "policy": policy})
        if self.error:
            raise RuntimeError("private error details")
        if self.abstain:
            return Decision(request_id="test", kind="choose", status="abstained", latency_ms=1,
                            attempts=[Attempt(provider="liquid-local", outcome="low_confidence", latency_ms=1)])
        probabilities = {key: float(key == self.route) for key in options}
        engine = Decilist([FunctionProvider(lambda _: probabilities, name="liquid-local", model="fixture")], Policy(min_confidence=0))
        return await engine.choose(state, instructions, options)


async def test_explicit_rule_needs_no_client_or_model(monkeypatch):
    def forbidden():
        pytest.fail("A direct rule must not create a model client")
    monkeypatch.setattr(assistant, "Client", forbidden)
    report = await assistant.diagnose(failure("ModuleNotFoundError: No module named 'example_lib'"))
    assert report["status"] == "suggested" and report["source"] == "rules"
    assert report["route"] == "dependency_environment"
    assert report["confidence"] is None and report["decision"] is None
    assert report["rule"]["evidence_lines"] == [1]
    assert "No model was called" in report["message"]


async def test_rules_mode_preserves_assertion_facts_without_guessing_route():
    client = LocalClient()
    report = await assistant.diagnose(failure(), client, mode="rules")
    assert report["status"] == "needs_review" and report["source"] == "none"
    assert report["facts"]["expected_actual"][0]["expected"] == "two"
    assert report["facts"]["expected_actual"][0]["actual"] == "one"
    assert not client.calls


async def test_model_call_uses_readable_facts_and_unchanged_acceptance_policy():
    client = LocalClient()
    report = await assistant.diagnose(failure(), client)
    assert report["status"] == "suggested" and report["source"] == "model"
    assert client.calls[0]["policy"].min_confidence == .7
    assert client.calls[0]["policy"].local_only is True
    assert client.calls[0]["options"] == ROUTES
    assert "\n" in client.calls[0]["state"]
    assert report["decision"]["provider"] == "liquid-local"


async def test_model_mode_does_not_disguise_rules_as_model():
    client = LocalClient(route="dependency_environment")
    report = await assistant.diagnose(failure("ModuleNotFoundError: No module named 'example_lib'"), client, mode="model")
    assert report["source"] == "model" and len(client.calls) == 1
    assert report["rule"] is None


@pytest.mark.parametrize("error,status", [(False,"needs_review"),(True,"unavailable")])
async def test_uncertain_or_unavailable_model_keeps_concrete_facts(error, status):
    client = LocalClient(abstain=True, error=error)
    report = await assistant.diagnose(failure(), client)
    assert report["status"] == status
    assert report["route"] == "manual_review"
    assert report["facts"]["expected_actual"]
    assert "private error" not in json.dumps(report)
    assert "No code was changed" in assistant.format_text(report)


async def test_passing_insufficient_and_mixed_controls_never_infer():
    client = LocalClient()
    with pytest.raises(ValueError, match="No observed failure"):
        await assistant.diagnose(prepare_evidence("3 passed"), client)
    insufficient = prepare_evidence("The retained execution log is empty.")
    insufficient["exit_code"] = 1
    assert (await assistant.diagnose(insufficient, client))["status"] == "needs_review"
    assert (await assistant.diagnose(prepare_evidence("AssertionError"), client))["status"] == "needs_review"
    mixed = failure("ModuleNotFoundError: No module named 'alpha'\nValueError: wrong value\nFAILED test.py::test_one\nFAILED test.py::test_two")
    assert (await assistant.diagnose(mixed, client))["status"] == "needs_review"
    assert not client.calls


async def test_remote_refused_before_ready_or_model_call():
    client = LocalClient()
    client.base_url = "https://example.com"
    with pytest.raises(ValueError, match="remote"):
        await assistant.diagnose(failure(), client)
    assert not client.calls


async def test_demo_runtime_is_not_presented_as_learned():
    client = LocalClient()
    async def demo():
        return {"provider": "demo"}
    client.ready = demo
    report = await assistant.diagnose(failure(), client)
    assert report["status"] == "unavailable" and not client.calls


async def test_context_is_added_before_inspection_and_preserves_project(tmp_path):
    project = tmp_path
    (project / "app.py").write_text("def calculate():\n    return 1\n")
    (project / "test_app.py").write_text("from app import calculate\ndef test_result():\n    assert calculate() == 2\n")
    evidence = prepare_evidence("test_app.py:3: AssertionError\nE   assert 1 == 2\nFAILED test_app.py::test_result - AssertionError", project)
    original = {p.name:p.read_bytes() for p in project.iterdir()}
    report = await assistant.diagnose(evidence, mode="rules", project=project)
    assert any(r["path"] == "app.py" for r in report["evidence"]["implementation_references"])
    assert any(r["path"] == "app.py" for r in report["facts"]["source_references"])
    assert "Code (implementation): app.py:" in assistant.format_text(report)
    assert {p.name:p.read_bytes() for p in project.iterdir()} == original


def test_cli_rules_path_and_output_are_real_without_model(tmp_path, capsys):
    log = tmp_path / "failed.log"
    log.write_text("ModuleNotFoundError: No module named 'example_lib'\n")
    output = tmp_path / "result.json"
    assert assistant.main(["--log",str(log),"--project",str(tmp_path),"--mode","rules","--format","json","--output",str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["source"] == "rules" and report["confidence"] is None
    original = output.read_bytes()
    assert assistant.main(["--log",str(log),"--output",str(output)]) == 2
    assert output.read_bytes() == original


def test_cli_rejects_invalid_mode_or_input_without_mutation(tmp_path):
    with pytest.raises(SystemExit):
        assistant.main(["--log","missing","--mode","cloud"])
    assert assistant.main(["--log",str(tmp_path/"missing")]) == 2
