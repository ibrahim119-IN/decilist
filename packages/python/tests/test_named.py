import json

import pytest

from decilist import Decilist, DecisionLog, FunctionProvider, NamedDecision, Policy, Shadow, shadow_report, threshold_table
from decilist.cli import main


OPTIONS = {"a": "A", "b": "B", "c": "C"}


def sdk(log=None, probabilities=None, policy=None):
    return Decilist([FunctionProvider(lambda _: probabilities or {"a": .8, "b": .15, "c": .05})], policy, log=log)


async def test_kind_follows_the_definition_and_matches_direct_calls():
    decisions = sdk()
    choice = decisions.define("pick", "Choose", options=OPTIONS)
    assert isinstance(choice, NamedDecision) and choice.kind == "choose"
    result = await choice({"x": 1})
    direct = await decisions.choose({"x": 1}, "Choose", OPTIONS)
    assert result.value == direct.value == "a" and result.probabilities == direct.probabilities
    yes_no = Decilist([FunctionProvider(lambda _: {"true": .9, "false": .1})]).define("ready", "Is it ready?")
    assert yes_no.kind == "predict" and (await yes_no("state")).value is True
    score = Decilist([FunctionProvider(lambda _: {"0": .1, "1": .9})]).define("quality", "Rate it", levels=["poor", "good"])
    assert score.kind == "score" and (await score("state")).value == pytest.approx(.9)
    assert set(decisions.definitions) == {"pick"} and decisions.definitions["pick"] is choice


def test_definition_is_validated_when_defined():
    decisions = sdk()
    decisions.define("pick", "Choose", options=OPTIONS)
    with pytest.raises(ValueError, match="already defined"):
        decisions.define("pick", "Choose again", options=OPTIONS)
    for bad in ("", " pick", "has space", "-dash", "x" * 65, None):
        with pytest.raises(ValueError):
            decisions.define(bad, "Choose", options=OPTIONS)
    with pytest.raises(ValueError):
        decisions.define("both", "Choose", options=OPTIONS, levels=["a", "b"])
    with pytest.raises(ValueError):
        decisions.define("one-option", "Choose", options={"a": "A"})
    with pytest.raises(ValueError):
        decisions.define("no-instructions", "  ", options=OPTIONS)
    with pytest.raises(ValueError, match="verify applies to a choice"):
        decisions.define("yes-no-verify", "Is it ready?", verify=lambda label: True)
    with pytest.raises(ValueError, match="fallback needs verify"):
        decisions.define("fallback-only", "Choose", options=OPTIONS, fallback=lambda: None)
    assert set(decisions.definitions) == {"pick"}


async def test_own_policy_and_per_call_policy():
    decisions = sdk(probabilities={"a": .6, "b": .3, "c": .1})
    assert (await decisions.define("default", "Choose", options=OPTIONS)("s")).status == "abstained"
    relaxed = decisions.define("relaxed", "Choose", options=OPTIONS, policy=Policy(min_confidence=.5))
    assert (await relaxed("s")).value == "a"
    assert (await relaxed("s", policy=Policy(min_confidence=.9))).status == "abstained"


async def test_verify_and_fallback_belong_to_the_definition():
    calls = []
    decisions = sdk()
    checked = decisions.define("checked", "Choose", options=OPTIONS, verify=lambda label: calls.append(label) or label == "b",
                               fallback=lambda: "person", policy=Policy(min_confidence=0))
    result = await checked("s")
    assert result.status == "verified" and result.value == "b" and calls == ["a", "b"] and result.costed is None
    refused = decisions.define("refused", "Choose", options=OPTIONS, verify=lambda label: False, fallback=lambda: "person", policy=Policy(min_confidence=0))
    result = await refused("s")
    assert result.status == "fallback" and result.result == "person"


def test_sync_call():
    assert sdk().define("pick", "Choose", options=OPTIONS).sync("s").value == "a"


async def test_name_is_logged_and_reports_filter_by_it(tmp_path, capsys):
    log = DecisionLog(tmp_path / "log.jsonl")
    decisions = sdk(log, policy=Policy(min_confidence=0))
    first = decisions.define("first", "Choose first", options=OPTIONS)
    second = decisions.define("second", "Choose second", options=OPTIONS)
    for _ in range(3):
        first.record_outcome(await first("s"), expected="a")
    second.record_outcome((await second("s")).request_id, correct=False)
    await decisions.choose("s", "Unnamed", OPTIONS)
    records = [r for r in log.read() if r["type"] == "decision"]
    assert [r.get("name") for r in records] == ["first", "first", "first", "second", None]
    assert "name" not in records[-1]

    everything = threshold_table(log.path, min_samples=1)
    assert everything["decisions"] == 5 and everything["names"] == ["first", "second"] and everything["name"] is None
    assert any("2 named decisions (first, second)" in w for w in everything["warnings"])
    table = threshold_table(log.path, min_samples=1, name="first")
    assert table["decisions"] == 3 and table["labelled"] == 3 and table["name"] == "first"
    assert not any("mixes different questions" in w for w in table["warnings"])
    assert table["rows"][0]["accuracy"] == 1.0
    assert threshold_table(log.path, min_samples=1, name="second")["rows"][0]["accuracy"] == 0.0
    missing = threshold_table(log.path, min_samples=1, name="absent")
    assert missing["decisions"] == 0 and any("No decision named 'absent'" in w for w in missing["warnings"])

    assert main(["calibrate", str(log.path), "--decision", "first", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["decisions"] == 3


async def test_shadow_runs_under_the_name(tmp_path):
    log = DecisionLog(tmp_path / "log.jsonl")
    decisions = sdk(log)
    shadow = Shadow(decisions)
    first = decisions.define("first", "Choose first", options=OPTIONS)
    second = decisions.define("second", "Choose second", options=OPTIONS)
    assert (await first.compare(shadow, "s", current="a")).value == "a"
    assert first.observe(shadow, "s", current="b") is True
    assert (await second.compare(shadow, "s", current="a")).value == "a"
    await shadow.compare_choose("s", "Unnamed", OPTIONS, current="a")
    assert await shadow.drain(timeout=5) == 0
    report = shadow_report(log.path)
    assert report["observations"] == 4 and report["names"] == ["first", "second"]
    assert any("mixes 2 named decisions" in w for w in report["warnings"])
    named = shadow_report(log.path, name="first")
    assert named["observations"] == 2 and named["agreements"] == 1 and named["name"] == "first"
    assert main(["shadow-report", str(log.path), "--decision", "second", "--json"]) == 0


def test_record_outcome_needs_a_log():
    definition = sdk().define("pick", "Choose", options=OPTIONS)
    with pytest.raises(ValueError, match="DecisionLog"):
        definition.record_outcome("id", expected="a")
