import asyncio
import json
import threading
from pathlib import Path
import pytest
from pydantic import ValidationError
from decilist import Choose, Decilist, FunctionProvider, LiquidLocalProvider, Policy, Predict, Score, UniformProvider
from decilist.contracts import Decision, question_adapter, validate_decision
from decilist.evaluation import Case, evaluate, summarize

QUESTION = Choose(state="evidence", instructions="Choose", options={"a":"A", "b":"B"})


@pytest.mark.parametrize("case", json.loads((Path(__file__).resolve().parents[3] / "contracts/cases.json").read_text()))
async def test_cross_language_contract(case):
    engine = Decilist([FunctionProvider(lambda q: case["probabilities"])], Policy(min_confidence=0))
    question = question_adapter.validate_python(case["question"])
    result = await engine.decide(question)
    assert result.status == "accepted"
    if case["question"]["kind"] == "score":
        assert result.value == pytest.approx(case["value"], abs=1e-12)
    else:
        assert type(result.value) is type(case["value"])
        assert result.value == case["value"]
    assert result.confidence == case["confidence"]
    assert validate_decision(question, result) is result


@pytest.mark.parametrize("probabilities", [{"a":1}, {"a":.2,"b":.3}, {"a":float("nan"),"b":0}, {"a":-1,"b":2}, {"a":True,"b":False}, {"a":0,"b":0}, {"a":0,"b":1,"x":0}])
async def test_reject_invalid_output_and_fallback(probabilities):
    engine = Decilist([FunctionProvider(lambda q: probabilities, name="broken"), FunctionProvider(lambda q: {"a":.9,"b":.1}, name="good")])
    result = await engine.decide(QUESTION)
    assert result.value == "a"
    assert [a.outcome for a in result.attempts] == ["invalid_output", "accepted"]


async def test_low_confidence_abstains_without_fabricating_value():
    result = await Decilist([UniformProvider()]).decide(QUESTION)
    assert result.status == "abstained"
    assert result.value is None and result.probabilities == {} and result.provider is None


async def test_cloud_is_never_called_even_as_fallback():
    class Cloud:
        name, model, locality = "cloud", "test", "remote"
        async def infer(self, question):
            pytest.fail("local-only policy leaked input to remote provider")
    result = await Decilist([UniformProvider(), Cloud()]).decide(QUESTION)
    assert [a.outcome for a in result.attempts] == ["low_confidence", "blocked"]


async def test_explicit_remote_opt_in():
    cloud = FunctionProvider(lambda q: {"a":1,"b":0})
    cloud.locality = "remote"
    result = await Decilist([cloud]).decide(QUESTION, Policy(local_only=False))
    assert result.status == "accepted"


async def test_timeout_then_fallback():
    class Slow:
        name, model, locality = "slow", "test", "local"
        async def infer(self, question):
            await asyncio.sleep(1)
            return {"a":1,"b":0}
    result = await Decilist([Slow(), FunctionProvider(lambda q: {"a":0,"b":1})], Policy(timeout_ms=10)).decide(QUESTION)
    assert result.value == "b"
    assert result.attempts[0].outcome == "timeout"


async def test_error_trace_redacts_provider_error():
    def fail(q):
        raise RuntimeError("SECRET AND INPUT")
    result = await Decilist([FunctionProvider(fail)]).decide(QUESTION)
    assert result.attempts[0].outcome == "error"
    assert "SECRET" not in result.model_dump_json()


async def test_cancellation_is_not_converted_to_fallback():
    class Cancelled:
        name, model, locality = "cancelled", "test", "local"
        async def infer(self, question):
            raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await Decilist([Cancelled()]).decide(QUESTION)


@pytest.mark.parametrize("kwargs", [{"min_confidence":1.1}, {"min_confidence":float("nan")}, {"timeout_ms":0}, {"local_only":"false"}])
def test_invalid_policy(kwargs):
    with pytest.raises(ValidationError):
        Policy(**kwargs)


def test_bad_question_and_duplicate_providers():
    with pytest.raises(ValidationError):
        Choose(state=" ", instructions="Choose", options={"a":"A"})
    with pytest.raises(ValidationError):
        Score(state="evidence", instructions="Score", levels=["same", "same"])
    with pytest.raises(ValueError):
        Decilist([UniformProvider(), UniformProvider()])
    with pytest.raises(ValidationError):
        Choose(state="state", instructions="Choose", options={" a ":"A","b":"B"})


def test_vendor_adapter_mapping_without_model_download():
    question = Predict(state="evidence", instructions="Ready?")
    assert LiquidLocalProvider.question_payload(question)["type"] == "noul"
    assert LiquidLocalProvider.decode_answer(question, {"answers":{"decision":{"noul":.8}}}) == pytest.approx({"false":.2,"true":.8})
    score = Score(state="evidence", instructions="Rate", levels=["Low","High"])
    assert LiquidLocalProvider.question_payload(score)["criteria"] == ["Low","High"]
    assert LiquidLocalProvider.decode_answer(score, {"answers":{"decision":{"probabilities":{"0":.2,"1":.8}}}}) == {"0":.2,"1":.8}
    with pytest.raises(ValueError):
        LiquidLocalProvider.load()


async def test_timed_out_native_worker_never_overlaps_next_inference():
    provider = LiquidLocalProvider(object())
    started = threading.Event()
    release = threading.Event()
    calls = []
    def infer(question):
        calls.append(1)
        started.set()
        release.wait(2)
        return {"a":1,"b":0}
    provider._infer_sync = infer
    try:
        first = asyncio.create_task(provider.infer(QUESTION))
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(.005)
        assert started.is_set()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(provider.infer(QUESTION), .02)
        assert len(calls) == 1
    finally:
        release.set()
        while provider._running:
            await asyncio.sleep(.005)
    assert await provider.infer(QUESTION) == {"a":1,"b":0}


async def test_report_keeps_coverage_separate_from_accuracy():
    cases = [Case("one", QUESTION, "a")]
    report = await evaluate(Decilist([UniformProvider()]), cases)
    assert report["metrics"]["coverage"] == 0
    assert report["metrics"]["accuracy_all"] == 0
    assert report["metrics"]["accuracy_accepted"] is None
    assert report["metrics"]["cost_usd"] is None
    with pytest.raises(ValueError):
        summarize([], [])


async def test_response_semantics_reject_mismatched_value():
    result = await Decilist([FunctionProvider(lambda q: {"a":1,"b":0})]).decide(QUESTION)
    with pytest.raises(ValueError):
        validate_decision(QUESTION, result.model_copy(update={"value":"b"}))
