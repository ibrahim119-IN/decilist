import json

import pytest

from decilist import Costs, CostedDecision, Decilist, DecisionLog, FunctionProvider, Policy, Shadow, apply_costs, cost_report
from decilist.cli import main


OPTIONS = {"ignore": "Noise", "fix": "Run the fix", "page": "Call a person"}
MISS = Costs({"ignore": {"page": 100}}, abstain=5)


def sdk(probabilities, log=None):
    return Decilist([FunctionProvider(lambda _: probabilities)], log=log)


def test_costs_are_validated():
    for bad in (-1, float("nan"), float("inf"), "1", True):
        with pytest.raises(ValueError):
            Costs({"a": {"b": bad}}, abstain=1)
        with pytest.raises(ValueError):
            Costs(abstain=bad)
        with pytest.raises(ValueError):
            Costs(abstain=1, default=bad)
    with pytest.raises(TypeError):
        Costs({"a": 1}, abstain=1)
    with pytest.raises(TypeError):
        Costs({1: {"b": 1}}, abstain=1)
    with pytest.raises(TypeError):
        Costs(abstain=1, wrong=[("a", "b", 1)])
    with pytest.raises(ValueError):
        Costs.from_dict({"wrong": {}})
    with pytest.raises(ValueError):
        Costs.from_dict({"abstain": 1, "typo": 2})
    costs = Costs({"a": {"b": 3, "a": .5}, True: {False: 2}}, abstain=None, default=4)
    assert costs.of("a", "b") == 3 and costs.of("a", "a") == .5 and costs.of("b", "b") == 0 and costs.of("b", "a") == 4
    assert costs.of("true", "false") == 2 and costs.abstain is None
    assert Costs.from_dict(json.loads(json.dumps(costs.to_dict()))).to_dict() == costs.to_dict()


def test_uniform_costs_match_the_confidence_threshold():
    # With every wrong answer costing 1, abstain=0.3 accepts exactly when the top probability is at least 0.7.
    costs = Costs(abstain=.3)
    assert costs.pick({"a": .71, "b": .29})[0] == "a"
    assert costs.pick({"a": .69, "b": .31})[0] is None
    assert Costs(abstain=None).pick({"a": .4, "b": .35, "c": .25})[0] == "a"


async def test_the_cheapest_answer_can_differ_from_the_most_probable():
    result = await sdk({"ignore": .7, "fix": .1, "page": .2}).define("triage", "Triage", options=OPTIONS, costs=MISS)("alert")
    assert isinstance(result, CostedDecision) and result.status == "accepted"
    assert result.value == "page" and result.decision.value == "ignore" and result.request_id == result.decision.request_id
    assert result.expected_costs == pytest.approx({"ignore": 20.1, "fix": .9, "page": .8})
    assert result.expected_cost == pytest.approx(.8) and result.abstain_cost == 5
    sure = await sdk({"ignore": .995, "fix": .003, "page": .002}).define("triage", "Triage", options=OPTIONS, costs=MISS)("alert")
    assert sure.value == "ignore"


async def test_abstains_when_asking_a_person_is_cheaper():
    costs = Costs({"ignore": {"page": 100}, "page": {"ignore": 30, "fix": 30}, "fix": {"ignore": 30, "page": 30}}, abstain=5)
    result = await sdk({"ignore": .5, "fix": .2, "page": .3}).define("triage", "Triage", options=OPTIONS, costs=costs)("alert")
    assert result.status == "abstained" and result.value is None and result.expected_cost is None
    assert min(result.expected_costs.values()) > 5 and result.decision.status == "accepted"


async def test_the_threshold_is_not_applied_and_provider_failure_abstains():
    low = sdk({"ignore": .4, "fix": .3, "page": .3})
    assert (await low.choose("alert", "Triage", OPTIONS)).status == "abstained"
    result = await low.define("triage", "Triage", options=OPTIONS, costs=Costs(abstain=None), policy=Policy(min_confidence=.99))("alert")
    assert result.status == "accepted" and result.value == "ignore"

    def broken(_):
        raise RuntimeError("down")
    failed = await Decilist([FunctionProvider(broken)]).define("triage", "Triage", options=OPTIONS, costs=MISS)("alert")
    assert failed.status == "abstained" and failed.expected_costs == {} and failed.decision.attempts[0].outcome == "error"


async def test_predict_costs_use_booleans():
    costs = Costs({False: {True: 50}}, abstain=10)
    fraud = Decilist([FunctionProvider(lambda _: {"true": .1, "false": .9})]).define("fraud", "Is this fraud?", costs=costs)
    result = await fraud("payment")
    assert result.value is True and result.decision.value is False and result.expected_costs == pytest.approx({"false": 5, "true": .9})
    assert apply_costs(result.decision, Costs(abstain=None)).value is False


def test_costs_must_fit_the_question():
    decisions = sdk({"ignore": 1.0, "fix": 0.0, "page": 0.0})
    with pytest.raises(ValueError, match="pgae"):
        decisions.define("typo", "Triage", options=OPTIONS, costs=Costs({"ignore": {"pgae": 100}}, abstain=1))
    with pytest.raises(ValueError, match="not score"):
        decisions.define("score", "Rate", levels=["low", "high"], costs=MISS)
    with pytest.raises(TypeError):
        decisions.define("dict", "Triage", options=OPTIONS, costs={"abstain": 1})
    assert decisions.definitions == {}


async def test_verify_follows_the_cost_order_and_skips_costly_answers(tmp_path):
    log = DecisionLog(tmp_path / "log.jsonl")
    calls = []
    decisions = sdk({"ignore": .7, "fix": .1, "page": .2}, log)
    checked = decisions.define("triage", "Triage", options=OPTIONS, costs=MISS, fallback=lambda: "person",
                               verify=lambda label: calls.append(label) or label == "fix")
    result = await checked("alert")
    # Expected costs are page 0.8, fix 0.9, ignore 20.1; ignore costs more than a person, so it is never tried.
    assert calls == ["page", "fix"] and result.status == "verified" and result.value == "fix"
    assert result.costed.value == "page" and result.decision.value == "ignore"
    calls.clear()
    refused = decisions.define("refused", "Triage", options=OPTIONS, costs=MISS, fallback=lambda: "person", verify=lambda label: calls.append(label))
    result = await refused("alert")
    assert calls == ["page", "fix"] and result.status == "fallback" and result.result == "person"
    # The first check was not on the model's top answer, so no outcome is written for it.
    assert not [r for r in log.read() if r["type"] == "outcome"]
    costs = [r for r in log.read() if r["type"] == "cost"]
    assert [(r["name"], r["status"], r["value"]) for r in costs] == [("triage", "accepted", "page"), ("refused", "accepted", "page")]
    assert costs[0]["expected_cost"] == pytest.approx(.8) and costs[0]["abstain_cost"] == 5


async def test_outcomes_and_shadow_with_costs(tmp_path):
    log = DecisionLog(tmp_path / "log.jsonl")
    decisions = sdk({"ignore": .7, "fix": .1, "page": .2}, log)
    triage = decisions.define("triage", "Triage", options=OPTIONS, costs=MISS)
    result = await triage("alert")
    with pytest.raises(ValueError, match="expected answer"):
        triage.record_outcome(result, correct=True)
    triage.record_outcome(result, expected="page")
    assert [r["expected"] for r in log.read() if r["type"] == "outcome"] == ["page"]


async def test_shadow_compares_the_cost_pick(tmp_path):
    from decilist import shadow_report
    log = DecisionLog(tmp_path / "log.jsonl")
    decisions = sdk({"ignore": .7, "fix": .1, "page": .2}, log)  # Most probable: ignore. Cost pick: page.
    shadow = Shadow(decisions)
    triage = decisions.define("triage", "Triage", options=OPTIONS, costs=MISS, policy=Policy(min_confidence=.99))
    agreed = await triage.compare(shadow, "alert", current="page")
    assert isinstance(agreed, CostedDecision) and agreed.value == "page" and agreed.decision.value == "ignore"
    triage.record_outcome(agreed, expected="page")
    assert triage.observe(shadow, "alert", current="ignore") is True
    costly = decisions.define("costly", "Triage", options=OPTIONS, costs=Costs({"ignore": {"page": 100}, "fix": {"page": 100}, "page": {"ignore": 30, "fix": 30}}, abstain=5))
    assert (await costly.compare(shadow, "alert", current="page")).status == "abstained"
    assert await shadow.drain(timeout=5) == 0 and shadow.errors == 0

    lines = list(log.read())
    shadows = [r for r in lines if r["type"] == "shadow"]
    # The background observation can finish after the awaited comparison that follows it.
    assert {(r["status"], r["value"], r["agreed"]) for r in shadows} == {("accepted", "page", True), ("accepted", "page", False), ("abstained", None, None)} and len(shadows) == 3
    assert [r["min_confidence"] for r in lines if r["type"] == "decision"] == [0, 0, 0]
    assert len([r for r in lines if r["type"] == "cost"]) == 3

    report = shadow_report(log.path, name="triage")
    assert report["observations"] == 2 and report["accepted"] == 2 and report["agreements"] == 1 and report["cost_rule"] == 2
    assert report["disagreements"][0]["value"] == "page" and report["disagreements"][0]["current"] == "ignore"
    # The outcome is judged against the cost pick (page), not the model's top answer (ignore).
    assert report["outcomes"]["model_correct"] == 1 and report["outcomes"]["current_correct"] == 1
    everything = shadow_report(log.path)
    assert everything["observations"] == 3 and everything["abstained"] == 1 and everything["compared"] == 2

    plain = decisions.define("plain", "Triage", options=OPTIONS, policy=Policy(min_confidence=0))
    await plain.compare(shadow, "alert", current="ignore")
    mixed = shadow_report(log.path)
    assert mixed["cost_rule"] == 3 and any("mixes observations answered by a cost rule" in w for w in mixed["warnings"])


async def test_shadow_rejects_costs_that_do_not_fit(tmp_path):
    from decilist import Choose, Score
    decisions = sdk({"ignore": .7, "fix": .1, "page": .2}, DecisionLog(tmp_path / "log.jsonl"))
    shadow = Shadow(decisions)
    question = Choose(state="alert", instructions="Triage", options=OPTIONS)
    assert shadow.observe(question, current="page", costs=Costs({"ignore": {"typo": 1}}, abstain=1)) is False
    assert shadow.observe(question, current="page", costs={"abstain": 1}) is False
    assert shadow.observe(Score(state="s", instructions="Rate", levels=["low", "high"]), current=1, costs=MISS) is False
    assert shadow.errors == 3 and await shadow.compare(question, current="page", costs=MISS) is not None


async def test_cost_report_replays_both_rules(tmp_path, capsys):
    log = DecisionLog(tmp_path / "log.jsonl")
    cases = [({"ignore": .7, "fix": .1, "page": .2}, "page"),    # cost rule pages (right); confidence rule ignores a real incident
             ({"ignore": .7, "fix": .1, "page": .2}, "ignore"),  # cost rule pages needlessly (cost 1); confidence rule is right
             ({"ignore": .995, "fix": .003, "page": .002}, "ignore"),
             ({"ignore": .4, "fix": .5, "page": .1}, "fix")]     # cost rule fixes (right); confidence rule abstains (cost 5)
    for probabilities, expected in cases:
        triage = sdk(probabilities, log).define("triage", "Triage", options=OPTIONS, costs=MISS)
        triage.record_outcome(await triage("alert"), expected=expected)
    other = sdk({"ignore": .9, "fix": .05, "page": .05}, log).define("other", "Other", options=OPTIONS, costs=MISS)
    other.record_outcome(await other("alert"), expected="ignore")
    unlabelled = sdk({"ignore": .9, "fix": .05, "page": .05}, log).define("triage", "Triage", options=OPTIONS, costs=MISS)
    await unlabelled("alert")

    report = cost_report(log.path, MISS, name="triage")
    assert report["decisions"] == 5 and report["replayed"] == 4
    assert report["cost_rule"] == {"answered": 4, "abstained": 0, "wrong": 1, "total_cost": 1.0, "mean_cost": .25}
    assert report["confidence_rule"] == {"answered": 3, "abstained": 1, "wrong": 1, "total_cost": 105.0, "mean_cost": 26.25}
    assert report["always_ask"] == {"total_cost": 20.0, "mean_cost": 5.0}
    assert report["costliest_mistakes"] == [{"answered": "page", "actual": "ignore", "count": 1, "cost": 1.0}]
    assert any("Fewer than 30" in w for w in report["warnings"]) and any("Only some decisions" in w for w in report["warnings"])
    assert any("mixes different questions" in w for w in cost_report(log.path, MISS)["warnings"])
    assert cost_report(log.path, MISS, name="absent")["replayed"] == 0

    costs_file = tmp_path / "costs.json"
    costs_file.write_text(json.dumps(MISS.to_dict()), encoding="utf-8")
    assert main(["cost-report", str(log.path), "--costs", str(costs_file), "--decision", "triage"]) == 0
    printed = capsys.readouterr().out
    assert "Replayed 4 of 5 decisions" in printed and "Cost rule" in printed and "Confidence >= 0.70" in printed
    assert main(["cost-report", str(log.path), "--costs", str(costs_file), "--decision", "triage", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["cost_rule"]["total_cost"] == 1.0
    assert main(["cost-report", str(log.path), "--costs", str(costs_file), "--decision", "absent"]) == 1
    assert main(["cost-report", str(log.path), "--costs", str(tmp_path / "missing.json")]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text('{"wrong": {}}', encoding="utf-8")
    capsys.readouterr()
    assert main(["cost-report", str(log.path), "--costs", str(bad)]) == 2 and "Cannot report costs" in capsys.readouterr().out


async def test_cost_report_leaves_out_what_it_cannot_replay(tmp_path):
    log = DecisionLog(tmp_path / "log.jsonl")
    decisions = sdk({"ignore": .5, "fix": .3, "page": .2}, log)
    abstained = await decisions.choose("alert", "Triage", OPTIONS)  # Default threshold: abstains, so no probabilities are stored.
    log.record_outcome(abstained.request_id, expected="ignore")
    accepted = await decisions.choose("alert", "Triage", OPTIONS, policy=Policy(min_confidence=0))
    log.record_outcome(accepted.request_id, correct=True)
    report = cost_report(log.path, Costs(abstain=None))
    assert report["replayed"] == 0 and report["cost_rule"]["mean_cost"] is None
    text = " ".join(report["warnings"])
    assert "have no probabilities" in text and "give only correct" in text and "No abstain cost" in text
    with pytest.raises(TypeError):
        cost_report(log.path, {"abstain": 1})
    with pytest.raises(ValueError):
        cost_report(log.path, MISS, min_confidence=2)


async def test_http_client_defines_named_decisions_with_costs(tmp_path):
    import httpx
    from decilist import Client

    probabilities = {"ignore": .75, "fix": .05, "page": .20}
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"schema_version": "1", "request_id": "remote", "kind": "choose", "status": "accepted", "value": "ignore", "probabilities": probabilities, "confidence": .75, "provider": "stub", "model": "stub", "latency_ms": 1.0, "cost_usd": None, "attempts": []})
    log = DecisionLog(tmp_path / "log.jsonl")
    client = Client(log=log)
    await client._http.aclose()
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://localhost")
    client._ready = True
    try:
        triage = client.define("triage", "Triage", options=OPTIONS, costs=Costs({"ignore": {"page": 100}, "fix": {"page": 40}, "page": {"ignore": 3, "fix": 3}}, abstain=2))
        result = await triage("alert")
        assert result.status == "abstained" and result.decision.value == "ignore"
        # The threshold sent to the runtime is 0, and the name stays on the client side.
        assert seen[0]["policy"]["min_confidence"] == 0 and "name" not in seen[0] and "name" not in seen[0]["question"]
        assert [(r["type"], r.get("name")) for r in log.read()] == [("decision", "triage"), ("cost", "triage")]
        with pytest.raises(TypeError, match="async only"):
            triage.sync("alert")
        with pytest.raises(ValueError, match="already defined"):
            client.define("triage", "Triage", options=OPTIONS)
    finally:
        await client.aclose()
