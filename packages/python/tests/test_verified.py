import asyncio
import threading
import warnings

import httpx
import pytest

from decilist import Client, Decilist, FunctionProvider, Policy


OPTIONS = {"a": "A", "b": "B", "c": "C"}
POLICY = Policy(min_confidence=0.0)


def sdk(probabilities=None):
    return Decilist([FunctionProvider(lambda _: probabilities or {"a": .2, "b": .5, "c": .3})])


async def choose(decisions, verify, **kwargs):
    return await decisions.choose_verified("state", "Choose", OPTIONS, verify=verify, policy=POLICY, **kwargs)


async def test_first_pass_keeps_object_and_decision():
    calls = []
    payload = {"usable": True}
    result = await choose(sdk(), lambda label: calls.append(label) or payload)
    assert result.status == "verified" and result.value == "b" and result.result is payload
    assert calls == ["b"] and result.decision.value == "b"
    assert len(result.tried) == 1 and result.tried[0].ok and result.tried[0].latency_ms >= 0


async def test_order_limit_zero_probability_and_ties():
    calls = []
    result = await choose(sdk(), lambda label: calls.append(label) or label == "c")
    assert calls == ["b", "c"] and result.value == "c"
    assert [item.ok for item in result.tried] == [False, True]
    calls.clear()
    result = await choose(sdk(), lambda label: calls.append(label) or False, max_tries=2)
    assert calls == ["b", "c"] and result.status == "unresolved" and result.result is None
    calls.clear()
    result = await choose(sdk({"a": .5, "b": .5, "c": 0.0}), lambda label: calls.append(label) or False)
    assert calls == ["a", "b", "c"] and calls[0] == result.decision.value


async def test_abstention_and_fallbacks():
    calls = []
    result = await sdk().choose_verified("state", "Choose", OPTIONS, verify=lambda label: calls.append(label), fallback=lambda: 0)
    assert result.status == "fallback" and result.value is None and result.result == 0
    assert not calls and not result.tried and result.decision.status == "abstained"
    result = await choose(sdk(), lambda _: None, fallback=lambda: "existing")
    assert result.status == "fallback" and result.result == "existing" and len(result.tried) == 3
    result = await choose(sdk(), lambda _: False)
    assert result.status == "unresolved" and result.value is None and result.result is None


async def test_error_redaction_and_fallback_exception():
    def verify(label):
        if label == "b":
            raise ValueError("private exception text")
        return label == "c"
    result = await choose(sdk(), verify)
    assert result.value == "c" and result.tried[0].error == "ValueError"
    assert "private exception text" not in repr(result)
    def fallback():
        raise RuntimeError("fallback failed")
    with pytest.raises(RuntimeError, match="fallback failed"):
        await choose(sdk(), lambda _: False, fallback=fallback)


async def test_cancellation_propagates_without_fallback():
    entered = asyncio.Event()
    fallback_called = []
    async def verify(_):
        entered.set()
        await asyncio.Event().wait()
    task = asyncio.create_task(choose(sdk(), verify, fallback=lambda: fallback_called.append(True)))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not fallback_called


async def test_sync_check_off_loop_and_awaitable_return():
    progress = threading.Event()
    def verify(_):
        assert progress.wait(2), "event loop did not make progress"
        async def outcome():
            return "passed"
        return outcome()
    task = asyncio.create_task(choose(sdk(), verify))
    await asyncio.sleep(.01)
    progress.set()
    assert (await task).result == "passed"
    async def async_verify(_):
        return "async passed"
    assert (await choose(sdk(), async_verify)).result == "async passed"


@pytest.mark.parametrize("changes,exception", [
    ({"max_tries": 0}, ValueError), ({"max_tries": True}, ValueError),
    ({"max_tries": 1.5}, ValueError), ({"verify": None}, TypeError),
    ({"fallback": 1}, TypeError),
])
async def test_invalid_arguments_precede_provider(changes, exception):
    def provider(_):
        pytest.fail("provider was called")
    arguments = {"verify": lambda _: True, **changes}
    with pytest.raises(exception):
        await sdk_with(provider).choose_verified("state", "Choose", OPTIONS, **arguments)


def sdk_with(provider):
    return Decilist([FunctionProvider(provider)])


async def test_http_client_uses_shared_verification():
    probabilities = {"c": .5, "b": .5, "a": 0.0}
    def handler(request):
        return httpx.Response(200, json={"schema_version": "1", "request_id": "test", "kind": "choose", "status": "accepted", "value": "b", "probabilities": probabilities, "confidence": .5, "provider": "stub", "model": "stub", "latency_ms": 1.0, "cost_usd": None, "attempts": []})
    client = Client()
    await client._http.aclose()
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://localhost")
    client._ready = True
    try:
        result = await client.choose_verified("state", "Choose", OPTIONS, verify=lambda label: label == "c", policy=POLICY)
        assert [item.label for item in result.tried] == ["b", "c"]
        assert result.value == "c" and list(result.decision.probabilities) == ["c", "b", "a"]
    finally:
        await client.aclose()


def test_sync_choice():
    result = sdk().choose_verified_sync("state", "Choose", OPTIONS, verify=lambda _: "ok", policy=POLICY)
    assert result.status == "verified" and result.result == "ok"


async def test_sync_choice_in_running_loop_has_no_unawaited_coroutine():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(RuntimeError, match="loop is already running"):
            sdk().choose_verified_sync("state", "Choose", OPTIONS, verify=lambda _: True)
    assert not any("never awaited" in str(warning.message) for warning in caught)
