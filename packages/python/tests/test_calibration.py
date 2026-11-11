import json
import math

import httpx
import pytest

from decilist import (Calibration, Choose, Client, Costs, Decilist, DecisionLog, FunctionProvider, Policy, Shadow,
                      calibrate_decision, fit_calibration, shadow_report)
from decilist.cli import main


OPTIONS = {"a": "A", "b": "B", "c": "C"}
LABELS = list(OPTIONS)


def from_state(question):
    return json.loads(question.state)


def sdk(log=None, policy=Policy(min_confidence=0), model="m1"):
    return Decilist([FunctionProvider(from_state, model=model)], policy, log=log)


def spread(top, label="a"):
    return {key: top if key == label else (1 - top) / 2 for key in LABELS}


async def write_log(path, top, right_in_ten, n=100, model="m1", name="pick"):
    """Log n choices where the provider says `top` and is right `right_in_ten` times in ten."""
    log = DecisionLog(path)
    pick = sdk(log, model=model).define(name, "Choose", options=OPTIONS)
    for index in range(n):
        label = LABELS[index % 3]
        right = index % 10 < right_in_ten
        actual = label if right else LABELS[(index + 1 + index % 2) % 3]
        pick.record_outcome(await pick(spread(top, label)), expected=actual)
    return log


def test_calibration_object(tmp_path):
    calibration = Calibration(temperature=2, kind="choose", labels=("c", "a", "b"), model="m1")
    assert calibration.labels == ("a", "b", "c")
    result = calibration.apply({"a": .9, "b": .1, "c": 0.0})
    assert result["c"] == 0 and sum(result.values()) == pytest.approx(1) and result["a"] == pytest.approx(.75)
    assert max(result, key=result.__getitem__) == "a"
    assert Calibration(temperature=1, kind="choose", labels=LABELS).apply(spread(.9)) == spread(.9)
    sharper = Calibration(temperature=.5, kind="predict", labels=("true", "false")).apply({"true": .8, "false": .2})
    assert sharper["true"] == pytest.approx(.64 / .68)
    assert calibration.applies_to("m1") and not calibration.applies_to("m2") and Calibration(temperature=2, kind="choose", labels=LABELS).applies_to("any")
    with pytest.raises(ValueError, match="different answers"):
        calibration.apply({"a": .5, "x": .5})
    for bad in (dict(temperature=0), dict(temperature=-1), dict(temperature=float("nan")), dict(temperature="2"), dict(kind="score"),
                dict(labels=("a",)), dict(labels=("a", "a")), dict(min_confidence=2)):
        with pytest.raises(ValueError):
            Calibration(**{"temperature": 2, "kind": "choose", "labels": LABELS, **bad})
    path = tmp_path / "calibration.json"
    calibration.save(path)
    assert Calibration.load(path) == calibration and json.loads(path.read_text())["method"] == "temperature"
    for bad in ({}, {"calibration_version": 2, "method": "temperature"}, {"calibration_version": 1, "method": "temperature"},
                {"calibration_version": 1, "method": "temperature", "temperature": 2, "kind": "choose", "labels": None}, [1]):
        with pytest.raises(ValueError):
            Calibration.from_dict(bad)


async def test_an_overconfident_model_is_corrected(tmp_path):
    log = await write_log(tmp_path / "log.jsonl", top=.95, right_in_ten=7)
    fit = fit_calibration(log.path, name="pick")
    report = fit.report
    assert report["usable"] == report["decisions"] == 100 and report["helps"] is True and report["folds"] == 5
    assert fit.calibration.temperature > 1.5 and fit.calibration.temperature == report["temperature"] == report["temperature_fitted"]
    assert report["before"]["mean_confidence"] == pytest.approx(.95) and report["before"]["accuracy"] == pytest.approx(.7)
    assert report["after"]["mean_confidence"] == pytest.approx(.7, abs=.03) and report["after"]["accuracy"] == pytest.approx(.7)
    for metric in ("log_loss", "brier", "ece"):
        assert report["after"][metric] < report["before"][metric]
    assert fit.calibration.kind == "choose" and fit.calibration.labels == ("a", "b", "c") and fit.calibration.model == "m1"
    assert fit.calibration.name == "pick" and fit.calibration.fitted_on == 100 and fit.calibration.min_confidence is None
    assert fit.calibration.apply(spread(.95))["a"] == pytest.approx(.7, abs=.03)
    assert not any("Fewer than 100" in w for w in report["warnings"]) and "describes past decisions" in report["warnings"][-1]


async def test_a_calibrated_model_is_left_alone_and_a_cautious_one_is_flagged(tmp_path):
    fine = fit_calibration((await write_log(tmp_path / "fine.jsonl", top=.8, right_in_ten=8)).path)
    assert fine.report["temperature_fitted"] == pytest.approx(1, abs=.05)
    assert fine.calibration.apply(spread(.8))["a"] == pytest.approx(.8, abs=.02)
    if not fine.report["helps"]:
        assert fine.calibration.temperature == 1 and any("left at 1" in w for w in fine.report["warnings"])
    cautious = fit_calibration((await write_log(tmp_path / "cautious.jsonl", top=.6, right_in_ten=9)).path)
    assert cautious.calibration.temperature < 1 and any("more sure of itself" in w for w in cautious.report["warnings"])
    assert cautious.calibration.apply(spread(.6))["a"] == pytest.approx(.9, abs=.03)


async def test_what_can_and_cannot_be_fitted(tmp_path):
    few = fit_calibration((await write_log(tmp_path / "few.jsonl", top=.9, right_in_ten=7, n=20)).path)
    assert few.calibration is None and few.report["usable"] == 20 and any("at least 30" in w for w in few.report["warnings"])
    assert fit_calibration(tmp_path / "few.jsonl", min_samples=10).calibration is not None

    two = tmp_path / "two.jsonl"
    await write_log(two, top=.9, right_in_ten=7, n=40, model="m1")
    await write_log(two, top=.9, right_in_ten=7, n=40, model="m2")
    with pytest.raises(ValueError, match="several models"):
        fit_calibration(two)
    assert fit_calibration(two, model="m2").calibration.model == "m2"

    mixed = DecisionLog(tmp_path / "mixed.jsonl")
    decisions = sdk(mixed)
    choice = await decisions.choose(spread(.9), "Choose", OPTIONS)
    mixed.record_outcome(choice.request_id, expected="a")
    yes = await decisions.predict({"true": .9, "false": .1}, "Ready?")
    mixed.record_outcome(yes.request_id, expected=True)
    with pytest.raises(ValueError, match="mixes choose and predict"):
        fit_calibration(mixed.path)
    answers = DecisionLog(tmp_path / "answers.jsonl")
    decisions = sdk(answers)
    for state, options, expected in ((spread(.9), OPTIONS, "a"), ({"x": .9, "y": .1}, {"x": "X", "y": "Y"}, "x")):
        answers.record_outcome((await decisions.choose(state, "Choose", options)).request_id, expected=expected)
    with pytest.raises(ValueError, match="one set of answers"):
        fit_calibration(answers.path, min_samples=2)
    for bad in (dict(min_samples=1), dict(folds=1), dict(target_accuracy=2)):
        with pytest.raises(ValueError):
            fit_calibration(mixed.path, **bad)


async def test_outcomes_that_only_say_correct(tmp_path):
    log = DecisionLog(tmp_path / "log.jsonl")
    decisions = sdk(log)
    for index in range(10):
        right = index % 10 < 7
        choice = await decisions.choose(spread(.9), "Choose", OPTIONS)
        log.record_outcome(choice.request_id, correct=right)
    abstained = await decisions.choose(spread(.9), "Choose", OPTIONS, policy=Policy(min_confidence=.99))
    log.record_outcome(abstained.request_id, expected="a")
    fit = fit_calibration(log.path, min_samples=5)
    # A right choice names its own label; a wrong one does not say which answer was right.
    assert fit.report["usable"] == 7 and fit.report["decisions"] == 11
    text = " ".join(fit.report["warnings"])
    assert "3 outcomes do not give the answer" in text and "1 labelled decisions have no probabilities" in text

    yes_no = DecisionLog(tmp_path / "yes_no.jsonl")
    decisions = sdk(yes_no)
    for index in range(10):
        answer = await decisions.predict({"true": .9, "false": .1}, "Ready?")
        yes_no.record_outcome(answer.request_id, correct=index < 6)
    fit = fit_calibration(yes_no.path, min_samples=5)
    # For yes or no, a wrong answer means the other one was right, so every outcome is usable.
    assert fit.report["usable"] == 10 and fit.calibration.kind == "predict" and fit.calibration.labels == ("false", "true")
    assert fit.calibration.temperature > 1


async def test_the_engine_corrects_before_the_threshold_and_logs_the_temperature(tmp_path):
    log = DecisionLog(tmp_path / "log.jsonl")
    calibration = Calibration(temperature=2, kind="choose", labels=LABELS, model="m1")
    decisions = sdk(log)
    question = Choose(state=json.dumps(spread(.9)), instructions="Choose", options=OPTIONS)
    expected = calibration.apply(spread(.9))
    accepted = await decisions.decide(question, calibration=calibration)
    assert accepted.probabilities == pytest.approx(expected) and accepted.confidence == pytest.approx(expected["a"]) and accepted.value == "a"
    assert .6 < expected["a"] < .7
    refused = await decisions.decide(question, Policy(min_confidence=.85), calibration=calibration)
    assert refused.status == "abstained" and refused.attempts[0].outcome == "low_confidence"
    assert (await decisions.decide(question, Policy(min_confidence=.85))).status == "accepted"
    other_model = await sdk(log, model="m2").decide(question, calibration=calibration)
    assert other_model.probabilities == pytest.approx(spread(.9))
    records = [r for r in log.read() if r["type"] == "decision"]
    assert [r.get("temperature") for r in records] == [2, None, None, None] and "temperature" not in records[1]

    with pytest.raises(ValueError, match="different answers"):
        await decisions.decide(Choose(state="s", instructions="Choose", options={"a": "A", "x": "X"}), calibration=calibration)
    with pytest.raises(ValueError, match="predict question, not choose"):
        await decisions.decide(question, calibration=Calibration(temperature=2, kind="predict", labels=("true", "false")))
    with pytest.raises(TypeError):
        await decisions.decide(question, calibration={"temperature": 2})


async def test_a_later_fit_recovers_the_models_own_probabilities(tmp_path):
    first = fit_calibration((await write_log(tmp_path / "raw.jsonl", top=.95, right_in_ten=7)).path).calibration
    log = DecisionLog(tmp_path / "calibrated.jsonl")
    pick = sdk(log).define("pick", "Choose", options=OPTIONS, calibration=first)
    for index in range(100):
        label = LABELS[index % 3]
        actual = label if index % 10 < 7 else LABELS[(index + 1 + index % 2) % 3]
        pick.record_outcome(await pick(spread(.95, label)), expected=actual)
    assert all(r["temperature"] == first.temperature for r in log.read() if r["type"] == "decision")
    again = fit_calibration(log.path, name="pick")
    # The log holds corrected probabilities, yet the fit lands on the same temperature rather than on 1.
    assert again.report["temperature_fitted"] == pytest.approx(first.temperature, rel=1e-3)
    assert again.report["before"]["mean_confidence"] == pytest.approx(.95)


async def test_named_decision_uses_the_calibration_and_its_threshold(tmp_path):
    log = await write_log(tmp_path / "log.jsonl", top=.95, right_in_ten=7)
    fit = fit_calibration(log.path, name="pick", target_accuracy=.5)
    threshold = fit.report["threshold"]
    assert threshold["target_accuracy"] == .5 and threshold["min_confidence"] == fit.calibration.min_confidence and threshold["rows"]
    assert fit.calibration.min_confidence is not None and fit.calibration.target_accuracy == .5
    assert any("somewhat optimistic" in w for w in fit.report["warnings"])
    unreachable = fit_calibration(log.path, name="pick", target_accuracy=.999)
    assert unreachable.calibration.min_confidence is None and "No threshold" in unreachable.report["threshold"]["reason"]

    strict = Calibration(temperature=fit.calibration.temperature, kind="choose", labels=LABELS, model="m1", min_confidence=.9)
    decisions = Decilist([FunctionProvider(from_state, model="m1")])
    assert (await decisions.define("strict", "Choose", options=OPTIONS, calibration=strict)(spread(.95))).status == "abstained"
    assert (await decisions.define("own-policy", "Choose", options=OPTIONS, calibration=strict, policy=Policy(min_confidence=.5))(spread(.95))).status == "accepted"
    lenient = decisions.define("lenient", "Choose", options=OPTIONS, calibration=fit.calibration)
    answer = await lenient(spread(.95))
    assert answer.status == "accepted" and answer.confidence == pytest.approx(.7, abs=.03)
    assert (await lenient(spread(.95), policy=Policy(min_confidence=.9))).status == "abstained"

    with pytest.raises(ValueError, match="different answers"):
        decisions.define("other-answers", "Choose", options={"a": "A", "b": "B"}, calibration=strict)
    with pytest.raises(ValueError, match="not score"):
        decisions.define("score", "Rate", levels=["low", "high"], calibration=strict)
    with pytest.raises(ValueError, match="choose question, not predict"):
        decisions.define("yes-no", "Ready?", calibration=strict)
    with pytest.raises(TypeError):
        decisions.define("dict", "Choose", options=OPTIONS, calibration=strict.to_dict())


async def test_calibration_changes_what_the_cost_rule_picks():
    costs = Costs({"a": {"b": 100}, "b": {"a": 50, "c": 50}, "c": {"a": 50, "b": 50}}, abstain=2)
    calibration = Calibration(temperature=3, kind="choose", labels=LABELS)
    decisions = Decilist([FunctionProvider(from_state)])
    state = {"a": .98, "b": .01, "c": .01}
    raw = await decisions.define("raw", "Choose", options=OPTIONS, costs=costs)(state)
    corrected = await decisions.define("corrected", "Choose", options=OPTIONS, costs=costs, calibration=calibration)(state)
    assert raw.value == "a" and raw.expected_cost == pytest.approx(1.01)
    assert corrected.status == "abstained" and corrected.decision.probabilities["a"] < .7
    assert corrected.expected_costs["a"] > 2 and corrected.decision.value == "a"


async def test_http_client_applies_calibration_and_threshold_locally(tmp_path):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"schema_version": "1", "request_id": "remote", "kind": "choose", "status": "accepted", "value": "a", "probabilities": spread(.9), "confidence": .9, "provider": "stub", "model": "m1", "latency_ms": 1.0, "cost_usd": None, "attempts": [{"provider": "stub", "outcome": "accepted", "latency_ms": 1.0}]})
    log = DecisionLog(tmp_path / "log.jsonl")
    client = Client(log=log)
    await client._http.aclose()
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://localhost")
    client._ready = True
    try:
        calibration = Calibration(temperature=2, kind="choose", labels=LABELS, model="m1")
        accepted = await client.define("lenient", "Choose", options=OPTIONS, calibration=calibration, policy=Policy(min_confidence=.6))("state")
        assert accepted.status == "accepted" and accepted.confidence == pytest.approx(calibration.apply(spread(.9))["a"])
        refused = await client.define("strict", "Choose", options=OPTIONS, calibration=calibration, policy=Policy(min_confidence=.8))("state")
        assert refused.status == "abstained" and refused.probabilities == {} and refused.attempts[0].outcome == "low_confidence"
        elsewhere = Calibration(temperature=2, kind="choose", labels=LABELS, model="m2")
        untouched = await client.define("other-model", "Choose", options=OPTIONS, calibration=elsewhere, policy=Policy(min_confidence=.8))("state")
        assert untouched.status == "accepted" and untouched.confidence == pytest.approx(.9)
        plain = await client.choose("state", "Choose", OPTIONS)
        assert plain.confidence == pytest.approx(.9)
        # With a calibration the runtime is asked for every answer; the threshold is applied on the client.
        assert [request["policy"]["min_confidence"] for request in seen] == [0, 0, 0, .7]
        records = list(log.read())
        assert [r.get("temperature") for r in records] == [2, None, None, None]
        assert [r["min_confidence"] for r in records] == [.6, .8, .8, .7] and [r["status"] for r in records] == ["accepted", "abstained", "accepted", "accepted"]
    finally:
        await client.aclose()


def test_calibrate_decision_function():
    from decilist import Decision
    decision = Decision(request_id="id", kind="choose", status="accepted", value="a", confidence=.9, probabilities=spread(.9), provider="p", model="m1", latency_ms=1, attempts=[])
    calibration = Calibration(temperature=2, kind="choose", labels=LABELS)
    assert calibrate_decision(decision, calibration).confidence == pytest.approx(calibration.apply(spread(.9))["a"])
    assert calibrate_decision(decision, calibration, min_confidence=.99).status == "abstained"
    abstained = Decision(request_id="id", kind="choose", status="abstained", latency_ms=1, attempts=[])
    assert calibrate_decision(abstained, calibration) is abstained
    score = Decision(request_id="id", kind="score", status="accepted", value=.5, confidence=.5, probabilities={"0": .5, "1": .5}, provider="p", model="m", latency_ms=1, attempts=[])
    with pytest.raises(ValueError):
        calibrate_decision(score, calibration)


async def test_shadow_runs_with_the_calibration(tmp_path):
    log = DecisionLog(tmp_path / "log.jsonl")
    decisions = sdk(log, policy=Policy())
    shadow = Shadow(decisions)
    calibration = Calibration(temperature=2, kind="choose", labels=LABELS, model="m1", min_confidence=.8)
    pick = decisions.define("pick", "Choose", options=OPTIONS, calibration=calibration)
    assert (await pick.compare(shadow, spread(.9), current="a")).status == "abstained"   # 0.9 becomes about 0.68, under 0.8
    confident = await pick.compare(shadow, spread(.99), current="a")
    assert confident.status == "accepted" and confident.confidence < .99
    assert shadow.errors == 0 and await shadow.drain(timeout=5) == 0
    report = shadow_report(log.path, name="pick")
    assert report["observations"] == 2 and report["accepted"] == 1 and report["agreements"] == 1
    assert [r.get("temperature") for r in log.read() if r["type"] == "decision"] == [None, 2]


async def test_fit_calibration_command(tmp_path, capsys):
    log = await write_log(tmp_path / "log.jsonl", top=.95, right_in_ten=7)
    output = tmp_path / "pick.calibration.json"
    assert main(["fit-calibration", str(log.path), "--decision", "pick", "--target-accuracy", "0.5", "--output", str(output)]) == 0
    printed = capsys.readouterr().out
    assert "Fitted on 100 of 100 decisions, model m1" in printed and "above 1: the model was too sure" in printed
    assert "After (5-fold)" in printed and "Threshold for 0.50 accuracy" in printed and f"Wrote {output}" in printed
    saved = Calibration.load(output)
    assert saved.temperature > 1.5 and saved.name == "pick" and saved.min_confidence is not None
    assert main(["fit-calibration", str(log.path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["calibration"]["temperature"] == pytest.approx(saved.temperature) and payload["helps"] is True
    assert main(["fit-calibration", str(log.path), "--decision", "absent"]) == 1
    assert "No calibration was fitted." in capsys.readouterr().out
    assert main(["fit-calibration", str(tmp_path / "missing.jsonl")]) == 2
    await write_log(log.path, top=.9, right_in_ten=7, n=30, model="m2")
    capsys.readouterr()
    assert main(["fit-calibration", str(log.path)]) == 2 and "several models" in capsys.readouterr().out
    assert math.isfinite(saved.temperature)
