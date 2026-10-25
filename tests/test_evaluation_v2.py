import argparse
import copy
import json

import pytest

from benchmarks import run_text
from decilist import Choose, Decilist, FunctionProvider, Policy, Predict, Score, UniformProvider
from decilist.evaluation import Case, evaluate, summarize, validate_cases


CHOOSE = Choose(state="evidence", instructions="Choose", options={"a": "A", "b": "B"})
PREDICT = Predict(state="evidence", instructions="Ready?")
SCORE = Score(state="evidence", instructions="Count", levels=["zero", "one", "two", "three"])


async def answer(question, probabilities, threshold=0):
    return await Decilist([FunctionProvider(lambda _: probabilities)], Policy(min_confidence=threshold)).decide(question)


async def test_mixed_metrics_keep_classification_and_score_denominators_separate():
    cases = [Case("choose", CHOOSE, "a"), Case("predict", PREDICT, True),
             Case("score", SCORE, 1.0), Case("abstain", SCORE, 3.0)]
    results = [await answer(CHOOSE, {"a": 1, "b": 0}),
               await answer(PREDICT, {"false": 1, "true": 0}),
               await answer(SCORE, {"0": 0, "1": .5, "2": .5, "3": 0}),
               await Decilist([UniformProvider()]).decide(SCORE)]
    for index, result in enumerate(results):
        results[index] = result.model_copy(update={"latency_ms": float(index + 1)})
    metrics = summarize(cases, results)
    assert metrics["cases"] == 4 and metrics["coverage"] == .75
    assert metrics["classification_cases"] == 2
    assert metrics["accuracy_all"] == metrics["accuracy_accepted"] == .5
    assert metrics["score_cases"] == 2 and metrics["score_coverage"] == .5
    assert metrics["score_mae"] == .5
    assert metrics["abstention_rate"] == .25
    assert metrics["valid_output_rate"] == 1  # low-confidence distributions remain valid output
    assert metrics["p50_ms"] == 2 and metrics["p95_ms"] == 4
    assert metrics["cost_usd"] is None
    assert {error["id"] for error in metrics["errors"]} == {"predict", "score", "abstain"}


async def test_score_only_and_abstention_have_no_fabricated_error_or_accuracy():
    report = await evaluate(Decilist([UniformProvider()]), [Case("score", SCORE, 2.0)])
    metrics = report["metrics"]
    assert metrics["accuracy_all"] is metrics["accuracy_accepted"] is None
    assert metrics["score_mae"] is None and metrics["score_coverage"] == 0
    assert metrics["low_confidence_attempts"] == 1
    assert metrics["errors"][0]["absolute_error"] is None


async def test_classification_abstention_is_miss_and_accepted_accuracy_is_conditional():
    cases = [Case("correct", CHOOSE, "b"), Case("unknown", CHOOSE, "b")]
    results = [await answer(CHOOSE, {"a": 0, "b": 1}), await Decilist([UniformProvider()]).decide(CHOOSE)]
    metrics = summarize(cases, results)
    assert metrics["accuracy_all"] == .5 and metrics["accuracy_accepted"] == 1
    assert metrics["coverage"] == .5


async def test_invalid_output_and_successful_fallback_are_counted():
    engine = Decilist([FunctionProvider(lambda _: {"a": 2, "b": -1}, name="bad"),
                      FunctionProvider(lambda _: {"a": 1, "b": 0}, name="good")])
    report = await evaluate(engine, [Case("fallback", CHOOSE, "a")])
    metrics = report["metrics"]
    assert metrics["invalid_output_attempts"] == 1
    assert metrics["valid_output_rate"] == .5
    assert metrics["fallback_cases"] == metrics["fallback_successes"] == 1
    assert metrics["fallback_rate"] == metrics["accuracy_all"] == 1


@pytest.mark.parametrize("question,expected", [(CHOOSE, "missing"), (CHOOSE, True), (PREDICT, "true"),
    (PREDICT, 1), (SCORE, True), (SCORE, -1.0), (SCORE, 4.0), (SCORE, float("nan")),
    (SCORE, float("inf")), (SCORE, None)])
def test_malformed_labels_rejected(question, expected):
    with pytest.raises(ValueError):
        validate_cases([Case("bad", question, expected)])


@pytest.mark.parametrize("cases", [[], [Case("", CHOOSE, "a")], [Case(" ", CHOOSE, "a")],
    [Case("same", CHOOSE, "a"), Case("same", CHOOSE, "b")]])
async def test_malformed_case_ids_fail_before_inference(cases):
    def must_not_run(_):
        pytest.fail("invalid dataset reached provider")
    with pytest.raises(ValueError):
        await evaluate(Decilist([FunctionProvider(must_not_run)]), cases)


async def test_invalid_final_decision_is_never_scored_as_valid():
    good = await answer(CHOOSE, {"a": 1, "b": 0})
    for update in ({"value": "b"}, {"kind": "predict"}, {"confidence": .1},
                   {"status": "unexpected"}, {"latency_ms": -1}):
        with pytest.raises(ValueError):
            summarize([Case("bad", CHOOSE, "a")], [good.model_copy(update=update)])
    with pytest.raises(ValueError):
        summarize([Case("mismatch", CHOOSE, "a")], [])


def test_frozen_dataset_is_complete_and_answers_are_excluded_from_payloads():
    document, pairs, freeze, policy = run_text.load_frozen_dataset()
    assert len(pairs) == 60
    assert len(freeze["split_ids"]["dev"]) == 20
    assert len(freeze["split_ids"]["heldout"]) == 40
    assert policy.min_confidence == .7 and policy.timeout_ms == 30000
    assert run_text.sha256(run_text.DATASET) == freeze["dataset_sha256"]
    for split in ("dev", "heldout"):
        rows = [row for row, _ in pairs if row["split"] == split]
        assert {row["operation"] for row in rows} == {"choose", "predict", "score"}
        assert {"missing", "ambiguous", "injection", "paraphrase"} <= {tag for row in rows for tag in row["tags"]}
    for row, case in pairs:
        assert row["rationale"] not in case.question.instructions
        assert row["id"] not in json.dumps(case.question.model_dump())
    assert "no independent human label review" in document["authorship"].lower()


@pytest.mark.parametrize("mutation", ["split", "label", "paraphrase", "rationale", "operation"])
def test_dataset_mutations_are_rejected(mutation):
    document = copy.deepcopy(json.loads(run_text.DATASET.read_text(encoding="utf-8")))
    if mutation == "split":
        document["cases"][0]["split"] = "heldout"
    elif mutation == "label":
        document["cases"][0]["expected"] = "invented"
    elif mutation == "paraphrase":
        document["cases"][1]["paraphrase_of"] = "h01"
    elif mutation == "rationale":
        document["cases"][0]["rationale"] = ""
    elif mutation == "operation":
        document["cases"][0]["operation"] = "other"
    with pytest.raises(ValueError):
        run_text.validate_document(document)


async def test_runner_loads_once_separates_warmup_and_preserves_reports(tmp_path, monkeypatch):
    loads, calls = [], []
    class FakePinnedProvider(UniformProvider):
        name, model, revision = "fake-pinned", "test-fixture-not-liquid", "test-revision"
        async def infer(self, question):
            calls.append(question)
            return await super().infer(question)
    def load(**kwargs):
        loads.append(kwargs)
        return FakePinnedProvider()
    monkeypatch.setattr(run_text.LiquidLocalProvider, "load", load)
    monkeypatch.setattr(run_text, "runtime_metadata", lambda: {"test_fixture": True})
    path = tmp_path / "report.json"
    args = argparse.Namespace(provider="liquid", allow_model_code=True, device="cpu", split="all",
                              repetitions=2, warmup=3, output=str(path))
    report = await run_text.run(args)
    assert loads == [{"allow_model_code": True, "device": "cpu", "offline": True}]
    assert len(calls) == 123 and len(report["warmup"]) == 3
    assert {question.kind for question in calls[:3]} == {"choose", "predict", "score"}
    assert all("welcome desk" in question.state for question in calls[:3])
    assert report["splits"]["dev"]["metrics"]["cases"] == 40
    assert report["splits"]["heldout"]["metrics"]["cases"] == 80
    assert report["unmeasured"]["network_observed"] is None
    assert report["dataset"]["path"] == "benchmarks/text_requests.json"
    assert json.loads(path.read_text(encoding="utf-8"))["providers"][0]["revision"] == "test-revision"
    with pytest.raises(ValueError, match="already exists"):
        await run_text.run(args)
    assert len(loads) == 1
