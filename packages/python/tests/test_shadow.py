import asyncio
from enum import Enum
import json
import math
import gc
import warnings

import httpx
import pytest

from decilist import (Client, Decilist, DecisionLog, FunctionProvider, Policy, Predict,
                      Shadow, shadow_report, threshold_table)
from decilist.cli import main


OPTIONS = {"a": "A", "b": "B"}
POLICY = Policy(min_confidence=0)


def engine(log, provider=None, policy=POLICY):
    return Decilist([provider or FunctionProvider(lambda _: {"a": .8, "b": .2})], policy, log=log)


def test_constructor_and_no_loop(tmp_path, caplog):
    log = DecisionLog(tmp_path / "log")
    with pytest.raises(ValueError):
        Shadow(engine(None))
    for value in (0, True, 1.5):
        with pytest.raises(ValueError):
            Shadow(engine(log), max_pending=value)
    shadow = Shadow(engine(log))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert shadow.choose("s", "i", OPTIONS, current="a") is False
        assert shadow.choose("s", "i", OPTIONS, current="a") is False
        gc.collect()
    assert not any("never awaited" in str(w.message) for w in caught)
    assert shadow.dropped == 2 and caplog.text.count("no running event loop") == 1
    assert shadow.choose("s", "i", OPTIONS, current=[]) is False
    assert shadow.errors == 1 and list(log.read())[0]["error"] == "TypeError"


async def test_background_drain_capacity_and_cancellation(tmp_path):
    started, release = asyncio.Event(), asyncio.Event()
    class Slow:
        name, model, locality = "slow", "m", "local"
        async def infer(self, question):
            started.set()
            await release.wait()
            return {"a": .8, "b": .2}
    log = DecisionLog(tmp_path / "log")
    shadow = Shadow(engine(log, Slow()), max_pending=1)
    assert shadow.choose("s", "i", OPTIONS, current="a") is True
    await started.wait()
    assert shadow.choose("s", "i", OPTIONS, current="a") is False
    assert shadow.dropped == 1 and await shadow.drain(.001) == 1
    assert not any(task.cancelled() for task in shadow._tasks)
    release.set()
    assert await shadow.drain(1) == 0 and not shadow._tasks
    assert [r["type"] for r in log.read()] == ["decision", "shadow"]
    started.clear()
    release.clear()
    task = asyncio.create_task(shadow.compare_choose("s", "i", OPTIONS, current="a"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await shadow.drain(1) == 0


async def test_errors_and_compare(tmp_path, monkeypatch, caplog):
    log = DecisionLog(tmp_path / "log")
    decisions = engine(log)
    shadow = Shadow(decisions)
    result = await shadow.compare_choose("s", "i", OPTIONS, current="a")
    assert result.value == "a"
    assert await shadow.compare_choose("s", "i", {}, current="a") is None
    async def fail(*args, **kwargs):
        raise RuntimeError("private error text")
    monkeypatch.setattr(decisions, "predict", fail)
    assert await shadow.compare_predict("s", "i", current=True) is None
    assert shadow.errors == 2
    def fail_write(*args):
        raise OSError("private error text")
    monkeypatch.setattr(log, "record_shadow", fail_write)
    assert await shadow.compare_choose("s", "i", OPTIONS, current="a") is None
    assert shadow.errors == 3
    assert "private error text" not in caplog.text and "private error text" not in log.path.read_text()
    assert [r["type"] for r in log.read()].count("shadow_error") == 2


async def test_provider_error_is_counted_without_exposing_text(tmp_path, caplog):
    class Broken:
        name, model, locality = "broken", "m", "local"
        async def infer(self, question):
            raise RuntimeError("private provider details")
    log = DecisionLog(tmp_path / "log")
    shadow = Shadow(engine(log, Broken()))
    result = await shadow.compare_choose("s", "i", OPTIONS, current="a")
    assert result.status == "abstained" and shadow.errors == 1
    assert "Shadow provider attempt failed (error)" in caplog.text and "private provider details" not in caplog.text
    assert "private provider details" not in log.path.read_text()
    assert [r["type"] for r in log.read()] == ["decision", "shadow"]


async def test_agreement_and_policy(tmp_path):
    class Answer(Enum):
        a = "A"
    log = DecisionLog(tmp_path / "log")
    shadow = Shadow(engine(log))
    await shadow.compare_choose("s", "i", OPTIONS, current=Answer.a)
    await shadow.compare_choose("s", "i", OPTIONS, current="b")
    predict = Shadow(engine(log, FunctionProvider(lambda _: {"false": .2, "true": .8})))
    await predict.compare_predict("s", "i", current=True)
    await predict.compare(Predict(state="s", instructions="i"), current=1)
    score = Shadow(engine(log, FunctionProvider(lambda _: {"0": .2, "1": .8})))
    await score.compare_score("s", "i", ["low", "high"], current=1)
    await score.compare_score("s", "i", ["low", "high"], current=0)
    await score.compare_score("s", "i", ["low", "high"], current=True)
    await score.compare_score("s", "i", ["low", "high"], current=None)
    called = []
    class Remote:
        name, model, locality = "remote", "m", "remote"
        async def infer(self, question):
            called.append(True)
            raise AssertionError()
    blocked = Shadow(engine(log, Remote(), Policy()))
    result = await blocked.compare_choose("s", "i", OPTIONS, current="a")
    assert result.status == "abstained" and not called
    records = [r for r in log.read() if r["type"] == "shadow"]
    assert [r["agreed"] for r in records] == [True, False, True, None, True, False, None, None, None]
    assert records[0]["current"] == "a"


async def test_client(tmp_path):
    log = DecisionLog(tmp_path / "log")
    client = Client(log=log, policy=POLICY)
    await client._http.aclose()
    response = {"schema_version": "1", "request_id": "http", "kind": "choose", "status": "accepted", "value": "a", "probabilities": {"a": .8, "b": .2}, "confidence": .8, "provider": "stub", "model": "stub", "latency_ms": 1.0, "cost_usd": None, "attempts": []}
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)), base_url="http://localhost")
    client._ready = True
    try:
        assert (await Shadow(client).compare_choose("s", "i", OPTIONS, current="a")).value == "a"
        assert [r["type"] for r in log.read()] == ["decision", "shadow"]
    finally:
        await client.aclose()


def test_report_and_cli(tmp_path, capsys):
    path = tmp_path / "log"
    log = DecisionLog(path)
    def decision(id, kind, status, value, latency, model="m"):
        return {"type": "decision", "request_id": id, "kind": kind, "status": status, "value": value,
                "confidence": .8 if status == "accepted" else None, "latency_ms": latency, "model": model,
                "state_sha256": "hash", "attempts": []}
    for row in [decision("1", "choose", "accepted", "a", 1), decision("2", "choose", "accepted", "b", 2),
                decision("3", "predict", "abstained", None, 3), decision("4", "choose", "accepted", "a", 4, "other"),
                decision("unrelated", "choose", "accepted", "a", 5)]:
        log._write(row)
    for id, current, agreed in [("1", "a", True), ("2", "a", False), ("3", True, None), ("4", "b", False)]:
        log.record_shadow(id, current, agreed)
    log.record_outcome("1", expected="a")
    log.record_outcome("2", correct=True)
    log.record_outcome("3", expected=True)
    log._write({"type": "shadow_error", "error": "ValueError"})
    log._write({"type": "shadow", "request_id": [], "current": "a", "agreed": False})
    report = shadow_report(path, max_disagreements=1)
    assert (report["observations"], report["accepted"], report["abstained"], report["coverage"]) == (4, 3, 1, .75)
    assert (report["compared"], report["agreements"], report["agreement_rate"], report["disagreements_total"]) == (3, 1, 1/3, 2)
    assert math.isclose(report["agreement_wilson_lower"], .0614903152761605)
    assert report["latency_ms"] == {"p50": 2, "p95": 4, "max": 4}
    assert [d["request_id"] for d in report["disagreements"]] == ["4"]
    assert (report["errors"], report["malformed_lines"], report["by_kind"]["choose"]["observations"]) == (1, 1, 3)
    assert report["outcomes"] == {"labelled": 3, "model_correct": 2, "model_accuracy": 1.0,
        "current_correct": 2, "current_accuracy": 1.0, "both_labelled": 1,
        "model_correct_on_both": 1, "current_correct_on_both": 1}
    assert shadow_report(path, model="m")["observations"] == 3
    assert threshold_table(path)["decisions"] == 5
    assert main(["shadow-report", str(path)]) == 0
    output = capsys.readouterr().out
    assert "Observations: 4" in output
    assert "Agreement rate: 33.3%; Wilson lower: 6.1%" in output
    assert "model accuracy: 100.0%; current accuracy: 100.0%" in output
    assert main(["shadow-report", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["observations"] == 4
    assert main(["shadow-report", str(tmp_path / "missing")]) == 2
    assert "not found" in capsys.readouterr().out
    empty = tmp_path / "empty"
    empty.write_text("", encoding="utf-8")
    assert main(["shadow-report", str(empty)]) == 1
    assert "No shadow" in capsys.readouterr().out


def test_malformed_shapes_never_reach_cli(tmp_path, capsys):
    path = tmp_path / "malformed"
    rows = [{"type": "shadow", "request_id": {"bad": 1}, "current": [], "agreed": "yes"},
            {"type": "decision", "request_id": "x", "kind": "score", "status": "accepted", "value": {}, "latency_ms": []},
            {"type": "outcome", "request_id": "x", "expected": []},
            {"type": "shadow_error", "error": []}]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    assert shadow_report(path)["malformed_lines"] == 4
    assert main(["shadow-report", str(path)]) == 1
    assert "No shadow" in capsys.readouterr().out


async def test_timeout_attempt_is_counted_even_when_recovered(tmp_path, caplog):
    class Slow:
        name, model, locality = "slow", "m", "local"
        async def infer(self, question):
            await asyncio.sleep(1)
            return {"a": .8, "b": .2}
    class Fast:
        name, model, locality = "fast", "m", "local"
        async def infer(self, question):
            return {"a": .8, "b": .2}
    log = DecisionLog(tmp_path / "log")
    decisions = Decilist([Slow(), Fast()], Policy(min_confidence=0, timeout_ms=1), log=log)
    shadow = Shadow(decisions)
    result = await shadow.compare_choose("s", "i", OPTIONS, current="a")
    assert result.status == "accepted"
    assert [a.outcome for a in result.attempts] == ["timeout", "accepted"]
    assert shadow.errors == 1
    assert "Shadow provider attempt failed (timeout)" in caplog.text


async def test_client_attempt_error_is_counted(tmp_path, caplog):
    log = DecisionLog(tmp_path / "log")
    client = Client(log=log, policy=POLICY)
    await client._http.aclose()
    response = {"schema_version": "1", "request_id": "http-error", "kind": "choose", "status": "accepted", "value": "a", "probabilities": {"a": .8, "b": .2}, "confidence": .8, "provider": "stub", "model": "stub", "latency_ms": 1.0, "cost_usd": None, "attempts": [{"provider": "failed", "outcome": "error", "latency_ms": .2}, {"provider": "stub", "outcome": "accepted", "latency_ms": .8}]}
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)), base_url="http://localhost")
    client._ready = True
    try:
        shadow = Shadow(client)
        assert (await shadow.compare_choose("s", "i", OPTIONS, current="a")).value == "a"
        assert shadow.errors == 1
        assert "Shadow provider attempt failed (error)" in caplog.text
    finally:
        await client.aclose()


@pytest.mark.parametrize("current", [float("nan"), float("inf"), float("-inf")])
async def test_nonfinite_current_does_not_call_provider(tmp_path, current):
    called = []
    def provider(question):
        called.append(True)
        return {"a": .8, "b": .2}
    log = DecisionLog(tmp_path / "log")
    shadow = Shadow(engine(log, FunctionProvider(provider)))
    assert shadow.choose("s", "i", OPTIONS, current=current) is False
    assert await shadow.compare_choose("s", "i", OPTIONS, current=current) is None
    assert shadow.errors == 2 and not called
    assert [r["type"] for r in log.read()] == ["shadow_error", "shadow_error"]


def test_outcome_matching_and_missing_current(tmp_path, capsys):
    log = DecisionLog(tmp_path / "log")
    rows = [
        ("1", "score", 2.37, 2.4, 2.0),
        ("2", "score", 2.51, 2.51, 2.0),
        ("3", "score", 2.0, 2.5, 2.0),
        ("4", "score", 2.0, 2.51, 2.0),
        ("5", "predict", True, 1, True),
        ("6", "predict", True, None, True),
    ]
    for request_id, kind, value, current, expected in rows:
        log._write({"type": "decision", "request_id": request_id, "kind": kind, "status": "accepted", "value": value,
                    "confidence": .8, "latency_ms": 1, "model": "m"})
        log.record_shadow(request_id, current, None)
        log.record_outcome(request_id, expected=expected)
    log._write({"type": "decision", "request_id": "7", "kind": "choose", "status": "accepted", "value": "a",
                "confidence": .8, "latency_ms": 1, "model": "m"})
    log.record_shadow("7", "b", False)
    log.record_outcome("7", correct=True)
    assert shadow_report(log.path)["outcomes"] == {
        "labelled": 7, "model_correct": 6, "model_accuracy": 6/7,
        "current_correct": 2, "current_accuracy": 2/5,
        "both_labelled": 5, "model_correct_on_both": 4, "current_correct_on_both": 2}
    assert main(["shadow-report", str(log.path)]) == 0
    output = capsys.readouterr().out
    assert "model accuracy: 85.7%; current accuracy: 40.0%" in output


def test_cli_empty_with_errors_and_unavailable_rates(tmp_path, capsys):
    log = DecisionLog(tmp_path / "log")
    log._write({"type": "shadow_error", "error": "ValueError"})
    with log.path.open("a", encoding="utf-8") as file:
        file.write("invalid json\n")
    assert main(["shadow-report", str(log.path)]) == 1
    output = capsys.readouterr().out
    assert "No shadow" in output and "Errors: 1; malformed lines: 1" in output
    log._write({"type": "decision", "request_id": "1", "kind": "predict", "status": "abstained", "value": None,
                "confidence": None, "latency_ms": 1, "model": None})
    log.record_shadow("1", None, None)
    log.record_outcome("1", expected=True)
    assert main(["shadow-report", str(log.path)]) == 0
    output = capsys.readouterr().out
    assert "Agreement rate: -; Wilson lower: -" in output
    assert "model accuracy: -; current accuracy: -" in output


@pytest.mark.parametrize("phrase,cause", [
    ("more than one model", "models"),
    ("Fewer than 30", "few"),
    ("some observations", "unlabelled"),
    ("Agreement is not accuracy", "no_outcomes"),
])
@pytest.mark.parametrize("present", [True, False])
def test_report_warnings(tmp_path, phrase, cause, present):
    log = DecisionLog(tmp_path / "log")
    count = 29 if cause == "few" and present else 30
    for i in range(count):
        request_id = str(i)
        model = "other" if cause == "models" and present and i == 0 else "m"
        log._write({"type": "decision", "request_id": request_id, "kind": "choose", "status": "accepted", "value": "a",
                    "confidence": .8, "latency_ms": 1, "model": model})
        log.record_shadow(request_id, "a", True)
        if not (cause == "no_outcomes" and present) and not (cause == "unlabelled" and present and i == 0):
            log.record_outcome(request_id, expected="a")
    warnings = shadow_report(log.path)["warnings"]
    assert any(phrase in warning for warning in warnings) is present
    assert any("current answer may itself be wrong" in warning for warning in warnings)
