import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from decilist import (Choose, Client, Decilist, Decision, DecisionLog, FunctionProvider,
                      Policy, Predict, Score, threshold_table, threshold_table_from_cases)
from decilist.cli import main
from decilist.evaluation import Case


OPTIONS = {"a": "SECRET_OPTION_DESCRIPTION", "b": "Other"}
POLICY = Policy(min_confidence=0.0)


def sdk(log=None, policy=POLICY):
    return Decilist([FunctionProvider(lambda _: {"a": .8, "b": .2})], policy, log=log)


async def test_log_privacy_and_unicode(tmp_path):
    path = tmp_path / "log.jsonl"
    log = DecisionLog(path)
    assert not path.exists()
    result = await sdk(log).choose("caf\u00e9", "SECRET_INSTRUCTIONS", OPTIONS)
    record, = log.read()
    assert set(record) == {"type", "log_version", "ts", "request_id", "kind", "status", "value", "confidence", "probabilities", "provider", "model", "latency_ms", "attempts", "min_confidence", "state_sha256", "instructions_sha256"}
    assert record["request_id"] == result.request_id and record["min_confidence"] == 0
    assert record["state_sha256"] == hashlib.sha256("caf\u00e9".encode()).hexdigest()
    assert b"SECRET_INSTRUCTIONS" not in path.read_bytes() and b"SECRET_OPTION_DESCRIPTION" not in path.read_bytes()
    assert "caf\u00e9" not in path.read_text(encoding="utf-8")
    second = DecisionLog(tmp_path / "state.jsonl", include_state=True)
    await sdk(second).choose("caf\u00e9", "SECRET_INSTRUCTIONS", OPTIONS, policy=Policy(min_confidence=.5))
    assert list(second.read())[0]["state"] == "caf\u00e9"
    assert list(second.read())[0]["min_confidence"] == .5
    assert b"SECRET_INSTRUCTIONS" not in second.path.read_bytes() and b"SECRET_OPTION_DESCRIPTION" not in second.path.read_bytes()


def test_read_torn_and_threaded_writes(tmp_path):
    log = DecisionLog(tmp_path / "log.jsonl")
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda i: log.record_outcome(str(i), expected="\u00e9"), range(200)))
    with log.path.open("ab") as file:
        file.write(b'{"type":\n[]\n{"torn":')
    records = list(log.read())
    assert len(records) == 200 and all(r["expected"] == "\u00e9" for r in records)
    assert log.malformed_lines == 3
    list(log.read())
    assert log.malformed_lines == 3


@pytest.mark.parametrize("request_id,kwargs", [("", {"correct": True}), (1, {"correct": True}),
    ("id", {}), ("id", {"correct": True, "expected": "a"}), ("id", {"correct": 1})])
def test_outcome_validation(tmp_path, request_id, kwargs):
    with pytest.raises(ValueError):
        DecisionLog(tmp_path / "log").record_outcome(request_id, **kwargs)


async def test_write_failure_and_no_log(tmp_path, caplog):
    log = DecisionLog(tmp_path / "missing" / "log")
    result = await sdk(log).choose("private state", "instructions", OPTIONS)
    assert result.value == "a" and result.status == "accepted"
    assert "FileNotFoundError" in caplog.text and str(log.path) in caplog.text and "private state" not in caplog.text
    with pytest.raises(OSError):
        log.record_outcome(result.request_id, correct=True)
    await sdk().choose("state", "instructions", OPTIONS)
    assert list(tmp_path.iterdir()) == []


async def test_client_logging_and_connect(tmp_path, monkeypatch):
    log = DecisionLog(tmp_path / "http.jsonl")
    client = Client(log=log, policy=POLICY)
    await client._http.aclose()
    response = {"schema_version": "1", "request_id": "http", "kind": "choose", "status": "accepted", "value": "a", "probabilities": {"a": .8, "b": .2}, "confidence": .8, "provider": "stub", "model": "stub", "latency_ms": 1.0, "cost_usd": None, "attempts": []}
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)), base_url="http://localhost")
    client._ready = True
    try:
        await client.choose("state", "instructions", OPTIONS)
        assert list(log.read())[0]["request_id"] == "http"
    finally:
        await client.aclose()
    async def ready(self, **kwargs):
        return {}
    monkeypatch.setattr(Client, "ready", ready)
    connected = await Client.connect(log=log)
    try:
        assert connected.log is log
    finally:
        await connected.aclose()


async def test_verified_outcomes(tmp_path, caplog):
    log = DecisionLog(tmp_path / "verified")
    result = await sdk(log).choose_verified("state", "instructions", OPTIONS, verify=lambda label: label == "b")
    records = list(log.read())
    assert result.value == "b" and [r["type"] for r in records] == ["decision", "outcome"]
    assert records[1]["correct"] is False and records[1]["source"] == "verify"
    def raises(_):
        raise ValueError("private")
    await sdk(log).choose_verified("state", "instructions", OPTIONS, verify=raises)
    assert [r["type"] for r in log.read()] == ["decision", "outcome", "decision"]
    await sdk(log, Policy(min_confidence=.9)).choose_verified("state", "instructions", OPTIONS, verify=lambda _: True)
    assert [r["type"] for r in log.read()] == ["decision", "outcome", "decision", "decision"]
    broken = DecisionLog(tmp_path / "missing" / "log")
    assert (await sdk(broken).choose_verified("state", "instructions", OPTIONS, verify=lambda _: True)).status == "verified"
    assert "FileNotFoundError" in caplog.text


def make_record(request_id, confidence, *, kind="choose", value="a", model="m", provider="p", minimum=0, instructions="q", attempts=None):
    return {"type": "decision", "request_id": request_id, "kind": kind, "status": "accepted" if confidence is not None else "abstained", "value": value if confidence is not None else None, "confidence": confidence, "provider": provider, "model": model, "min_confidence": minimum, "instructions_sha256": instructions, "attempts": attempts or []}


def write_lines(path, lines):
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")


def test_thresholds_and_warnings(tmp_path):
    path = tmp_path / "data"
    records = [make_record("1", .9, minimum=.5), make_record("2", .8, minimum=.5, model="n", instructions="other", attempts=[{"outcome": "error"}, {"outcome": "accepted"}]),
               make_record("3", .6, kind="predict", value=True, minimum=.5), make_record("4", None, minimum=.5), make_record("s", .7, kind="score", minimum=.5)]
    outcomes = [{"type": "outcome", "request_id": "1", "correct": False, "source": "verify"},
                {"type": "outcome", "request_id": "1", "correct": True}, {"type": "outcome", "request_id": "2", "expected": "b"},
                {"type": "outcome", "request_id": "3", "expected": True}, {"type": "outcome", "request_id": "orphan", "correct": True}]
    write_lines(path, records + outcomes)
    table = threshold_table(path, thresholds=[0, .5, .8, .9], target_accuracy=.2, min_samples=1)
    assert (table["decisions"], table["labelled"], table["unlabelled"], table["abstained"], table["skipped_score"], table["orphan_outcomes"]) == (4, 3, 1, 1, 1, 1)
    assert [r["threshold"] for r in table["rows"]] == [.5, .8, .9]
    low, middle, high = table["rows"]
    assert (low["covered"], low["coverage"], low["correct"], low["accuracy"]) == (3, .75, 2, 2/3)
    assert math.isclose(low["wilson_lower"], 0.20765495512648788)
    assert (middle["covered"], middle["accuracy"], high["accuracy"]) == (2, .5, 1)
    assert table["recommended"] == .5
    for phrase in ("run at min_confidence=0", "below the highest logged", "across providers or models",
                   "fallback provider answered", "different questions or kinds", "Only some decisions"):
        assert any(phrase in warning for warning in table["warnings"])
    assert not any("Verify labels" in warning for warning in table["warnings"])
    assert threshold_table(path, thresholds=[.5], target_accuracy=.99, min_samples=1)["recommended"] is None
    assert "No threshold" in threshold_table(path, thresholds=[.5], target_accuracy=.99, min_samples=1)["recommended_reason"]
    filtered = threshold_table(path, model="m")
    assert not any("across providers" in w for w in filtered["warnings"])
    assert filtered["orphan_outcomes"] == 1


def test_cases_match_log_and_clean_warnings(tmp_path):
    question = Choose(state="state", instructions="same", options={"a": "A", "b": "B"})
    case = Case("case", question, "a")
    result = Decision(request_id="id", kind="choose", status="accepted", value="a", confidence=.8, probabilities={"a": .8, "b": .2}, provider="p", model="m", latency_ms=1, attempts=[])
    log = DecisionLog(tmp_path / "log")
    log._decision(question, POLICY, result)
    log.record_outcome("id", expected="a")
    from_log = threshold_table(log.path, thresholds=[0, .8, .9], min_samples=1)
    from_cases = threshold_table_from_cases([case], [result], thresholds=[0, .8, .9], min_samples=1)
    assert from_log == from_cases
    assert from_log["warnings"] == [] and from_log["recommended"] is None
    assert from_log["reliability"] == [{"low": .8, "high": .9, "labelled": 1, "mean_confidence": .8, "accuracy": 1.0}]
    with pytest.raises(ValueError):
        threshold_table_from_cases([case], [])
    with pytest.raises(ValueError):
        threshold_table_from_cases([Case("case", question, "not-an-option")], [result])


def test_case_score_skipped_and_blocked_not_fallback(tmp_path):
    predict = Predict(state="state", instructions="same")
    score = Score(state="state", instructions="same", levels=["low", "high"])
    from decilist.contracts import Attempt
    result = Decision(request_id="p", kind="predict", status="accepted", value=True, confidence=.8,
                      probabilities={"false": .2, "true": .8}, provider="p", model="m", latency_ms=1,
                      attempts=[Attempt(provider="blocked", outcome="blocked", latency_ms=0), Attempt(provider="p", outcome="accepted", latency_ms=1)])
    score_result = Decision(request_id="s", kind="score", status="accepted", value=.8, confidence=.8,
                            probabilities={"0": .2, "1": .8}, provider="p", model="m", latency_ms=1, attempts=[])
    cases = [Case("prediction", predict, True), Case("score", score, 1.0)]
    table = threshold_table_from_cases(cases, [result, score_result], thresholds=[0], min_samples=1)
    assert table["decisions"] == 1 and table["skipped_score"] == 1
    assert table["warnings"] == []
    assert table["rows"][0]["accuracy"] == 1


def test_calibrate_cli(tmp_path, capsys):
    path = tmp_path / "log"
    assert main(["calibrate", str(path)]) == 2
    assert "not found" in capsys.readouterr().out
    path.write_text("", encoding="utf-8")
    assert main(["calibrate", str(path)]) == 1
    assert "No decisions" in capsys.readouterr().out
    write_lines(path, [make_record("id", .8), {"type": "outcome", "request_id": "id", "correct": True}])
    assert main(["calibrate", str(path), "--target-accuracy", ".1", "--min-samples", "1"]) == 0
    assert "Threshold" in capsys.readouterr().out
    assert main(["calibrate", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["decisions"] == 1


async def test_non_oserror_logging_failure_keeps_decision_and_verification(tmp_path, monkeypatch, caplog):
    log = DecisionLog(tmp_path / "log")
    def fail_write(self, record):
        raise ValueError("private state")
    monkeypatch.setattr(DecisionLog, "_write", fail_write)
    result = await sdk(log).choose("private state", "instructions", OPTIONS)
    assert result.status == "accepted" and result.value == "a"
    verified = await sdk(log).choose_verified("private state", "instructions", OPTIONS, verify=lambda label: label == "b")
    assert verified.status == "verified" and verified.value == "b"
    assert [item.label for item in verified.tried] == ["a", "b"]
    assert "ValueError" in caplog.text and str(log.path) in caplog.text and "private state" not in caplog.text
    with pytest.raises(ValueError):
        log.record_outcome(result.request_id, correct=True)


def test_integer_outcome_and_nonfinite_floats(tmp_path):
    log = DecisionLog(tmp_path / "log")
    log.record_outcome("id", expected=3)
    assert list(log.read())[0]["expected"] == 3
    for value in (float("nan"), float("inf"), -float("inf")):
        with pytest.raises(ValueError):
            log.record_outcome("id", expected=value)


@pytest.mark.parametrize("phrase", ["run at min_confidence=0", "below the highest logged",
    "across providers or models", "fallback provider answered", "different questions or kinds",
    "Only some decisions", "Verify labels"])
def test_warning_absent_without_cause(tmp_path, phrase):
    path = tmp_path / "log"
    write_lines(path, [make_record("id", .8), {"type": "outcome", "request_id": "id", "correct": True}])
    assert not any(phrase in warning for warning in threshold_table(path, thresholds=[0])["warnings"])


def test_verify_warning_uses_latest_outcome(tmp_path):
    path = tmp_path / "log"
    decision = make_record("id", .8)
    verify = {"type": "outcome", "request_id": "id", "correct": False, "source": "verify"}
    app = {"type": "outcome", "request_id": "id", "correct": True, "source": "app"}
    write_lines(path, [decision, app, verify])
    assert any("Verify labels" in warning for warning in threshold_table(path)["warnings"])
    write_lines(path, [decision, verify, app])
    table = threshold_table(path)
    assert not any("Verify labels" in warning for warning in table["warnings"])
    assert table["rows"][0]["correct"] == 1


def test_model_filter_warns_about_omitted_abstentions(tmp_path):
    path = tmp_path / "log"
    records = [make_record("a", .8), make_record("b", None, model=None),
               make_record("c", None, kind="predict", model=None), make_record("s", None, kind="score", model=None),
               make_record("d", None, model="other")]
    write_lines(path, records)
    warning = "2 abstained decisions name no model and were left out, so coverage only counts decisions this model answered."
    assert warning in threshold_table(path, model="m")["warnings"]
    assert warning not in threshold_table(path)["warnings"]
    write_lines(path, records[:1])
    assert not any("abstained decisions name no model" in w for w in threshold_table(path, model="m")["warnings"])


def test_cases_warn_when_low_confidence_abstentions_hide_threshold():
    from decilist.contracts import Attempt
    question = Choose(state="state", instructions="same", options={"a": "A", "b": "B"})
    case = Case("case", question, "a")
    result = Decision(request_id="id", kind="choose", status="abstained", latency_ms=1,
                      attempts=[Attempt(provider="p", outcome="low_confidence", latency_ms=1)])
    warning = "Some decisions abstained on low confidence, so rows below the threshold used are incomplete. Evaluate at min_confidence=0 for the full table."
    assert warning in threshold_table_from_cases([case], [result])["warnings"]
    other = result.model_copy(update={"attempts": [Attempt(provider="p", outcome="error", latency_ms=1)]})
    assert warning not in threshold_table_from_cases([case], [other])["warnings"]


def test_malformed_record_shapes_in_table_and_cli(tmp_path, capsys):
    path = tmp_path / "log"
    valid = [make_record("id", .8), {"type": "outcome", "request_id": "id", "correct": True}]
    invalid = [{"type": "decision", "request_id": [], "confidence": "high"},
               {"type": "decision", "request_id": " ", "confidence": .8},
               *[make_record("bad", confidence) for confidence in (True, "high", -1, 2, float("nan"))],
               make_record("bad-minimum", .8, minimum="high"),
               {"type": "outcome", "request_id": [], "correct": True},
               {"type": "outcome", "request_id": "id", "correct": 1},
               {"type": "outcome", "request_id": "", "expected": "a"}]
    write_lines(path, valid + invalid)
    log = DecisionLog(path)
    assert len(list(log.read())) == len(valid + invalid) and log.malformed_lines == 0
    table = threshold_table(path)
    assert table["malformed_lines"] == len(invalid) and table["decisions"] == 1 and table["labelled"] == 1
    assert main(["calibrate", str(path), "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["malformed_lines"] == len(invalid) and output["decisions"] == 1
    write_lines(path, [valid[0], {"type": "outcome", "request_id": "id", "correct": 1, "expected": "a"}])
    table = threshold_table(path, thresholds=[0])
    assert table["malformed_lines"] == 0 and table["rows"][0]["correct"] == 1
