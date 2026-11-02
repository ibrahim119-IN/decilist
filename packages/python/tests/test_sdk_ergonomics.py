import asyncio
import gc
import json
import subprocess
import sys
import time
import warnings
from enum import Enum

import pytest

from decilist import (Choose, Client, Decilist, FunctionProvider, LlamaCppProvider,
                      Policy, PredictProbaProvider, encode_state)


async def test_function_timeout_and_event_loop_progress():
    progress = asyncio.Event()
    def slow(_question):
        time.sleep(.5)
        return {"a": 1.0, "b": 0.0}
    async def tick():
        await asyncio.sleep(.01)
        progress.set()
    sdk = Decilist([FunctionProvider(slow)], Policy(timeout_ms=50))
    start = time.monotonic()
    task = asyncio.create_task(tick())
    result = await sdk.choose("state", "Choose", {"a": "A", "b": "B"})
    assert time.monotonic() - start < .4
    assert result.status == "abstained" and [a.outcome for a in result.attempts] == ["timeout"]
    await task
    assert progress.is_set()


async def test_function_coroutines_timeout_and_error_redaction():
    async def good(_):
        return {"false": 0.0, "true": 1.0}
    async def slow(_):
        await asyncio.sleep(.5)
        return {"false": 0.0, "true": 1.0}
    def bad(_):
        raise ValueError("private exception text")
    def returning_awaitable(question):
        return good(question)
    assert (await Decilist([FunctionProvider(good)]).predict("state", "Ready?")).value is True
    assert (await Decilist([FunctionProvider(returning_awaitable)]).predict("state", "Ready?")).value is True
    result = await Decilist([FunctionProvider(slow)], Policy(timeout_ms=50)).predict("state", "Ready?")
    assert result.status == "abstained" and result.attempts[0].outcome == "timeout"
    result = await Decilist([FunctionProvider(bad)]).predict("state", "Ready?")
    assert result.attempts[0].outcome == "error" and "private exception text" not in result.model_dump_json()


class Action(Enum):
    yes = "Yes"
    no = "No"


class Empty(Enum):
    pass


async def test_structured_state_enum_and_client():
    seen = []
    async def infer(q):
        seen.append(q)
        return {"yes": 1., "no": 0.}
    sdk = Decilist([FunctionProvider(infer)])
    result = await sdk.choose({"word": "\u0639\u0631\u0628\u064a", "count": 2}, "Choose", Action)
    assert Action[result.value] == Action.yes
    assert json.loads(seen[0].state) == {"word": "\u0639\u0631\u0628\u064a", "count": 2}
    assert seen[0].options == {"yes": "Yes", "no": "No"}
    for value in (Action.yes, Empty):
        with pytest.raises((TypeError, ValueError)):
            await sdk.choose("state", "Choose", value)
    assert encode_state(" raw ") == " raw "
    assert encode_state({"w": "\u0639"}) == '{"w": "\u0639"}'
    assert json.loads(encode_state((1, 2))) == [1, 2]
    for value in (1, {"n": float("nan")}, {"x": object()}):
        with pytest.raises((TypeError, ValueError)):
            encode_state(value)
    client = Client()
    async def capture(question, policy=None):
        seen.append(question)
        return result
    client.decide = capture
    await client.choose([1, 2], "Choose", Action)
    await client.predict({"ready": True}, "Ready?")
    await client.score((1, 2), "Rate", ["Low", "High"])
    await client.aclose()
    assert [json.loads(q.state) for q in seen[-3:]] == [[1, 2], {"ready": True}, [1, 2]]


def test_sync_runner_reuses_loop_and_rejects_running_loop():
    loops = []
    async def infer(_):
        loops.append(asyncio.get_running_loop())
        if _.kind == "score":
            return {"0": 0., "1": 1.}
        return {"false": 0., "true": 1.}
    sdk = Decilist([FunctionProvider(infer)])
    assert sdk.predict_sync("s", "Ready?").value is True
    assert sdk.predict_sync("s", "Ready?").value is True
    assert loops[0] is loops[1]
    assert sdk.decide_sync(Choose(state="s", instructions="Choose", options={"false": "No", "true": "Yes"})).value == "true"
    assert sdk.choose_sync("s", "Choose", {"false": "No", "true": "Yes"}).value == "true"
    assert sdk.score_sync("s", "Rate", ["Low", "High"]).value == 1.0
    async def inside():
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(RuntimeError, match="await decisions.choose"):
                sdk.predict_sync("s", "Ready?")
            gc.collect()
            assert not caught
    asyncio.run(inside())
    del sdk
    gc.collect()
    assert loops[0].is_closed()


class Estimator:
    def __init__(self, classes, probabilities):
        self.classes_, self.probabilities = classes, probabilities
    def predict_proba(self, rows):
        assert rows == [[7]]
        return [self.probabilities]


async def test_predict_proba_mappings():
    def provider(classes, probabilities, labels=None):
        return PredictProbaProvider(Estimator(classes, probabilities), features=lambda q: [7], labels=labels)
    choice = await Decilist([provider(["a", "b"], [.8, .2])]).choose("s", "Choose", {"a": "A", "b": "B"})
    assert choice.value == "a"
    predicted = await Decilist([provider([0, 1], [.1, .9])]).predict("s", "Ready?")
    assert predicted.value is True
    scored = await Decilist([provider(["0", "1"], [.2, .8])]).score("s", "Rate", ["Low", "High"])
    assert scored.value == .8
    missing = await Decilist([provider(["a"], [1.])]).choose("s", "Choose", {"a": "A", "b": "B"})
    assert missing.probabilities == {"a": 1., "b": 0.}
    mapped = await Decilist([provider([2, 3], [.8, .2], {2: "a", 3: "b"})]).choose("s", "Choose", {"a": "A", "b": "B"})
    assert mapped.value == "a"
    unknown = await Decilist([provider(["bad"], [1.])]).choose("s", "Choose", {"a": "A", "b": "B"})
    assert unknown.status == "abstained" and unknown.attempts[0].outcome == "error"


async def test_numpy_classes():
    np = pytest.importorskip("numpy")
    estimator = Estimator(np.array(["a", "b"], dtype=np.str_), np.array([.8, .2]))
    result = await Decilist([PredictProbaProvider(estimator, features=lambda q: [7])]).choose("s", "Choose", {"a": "A", "b": "B"})
    assert result.value == "a"
    estimator = Estimator(np.array([False, True]), np.array([.1, .9]))
    result = await Decilist([PredictProbaProvider(estimator, features=lambda q: [7])]).predict("s", "Ready?")
    assert result.value is True
    estimator = Estimator(np.array([0, 1]), np.array([.1, .9]))
    result = await Decilist([PredictProbaProvider(estimator, features=lambda q: [7])]).predict("s", "Ready?")
    assert result.value is True
    result = await Decilist([PredictProbaProvider(estimator, features=lambda q: [7])]).score("s", "Rate", ["Low", "High"])
    assert result.value == .9


def test_llama_provider_is_public():
    import decilist
    from decilist.llama_cpp import LlamaCppProvider as Implementation
    assert LlamaCppProvider is Implementation and "LlamaCppProvider" in decilist.__all__


def test_import_has_no_model_loading_or_network():
    code = """
import builtins
import socket
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'numpy', 'sklearn', 'torch', 'transformers', 'huggingface_hub'}:
        raise AssertionError('unexpected model dependency import: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
def no_connect(*args, **kwargs):
    raise AssertionError('unexpected network access')
socket.socket.connect = no_connect
from decilist import LlamaCppProvider
assert LlamaCppProvider.__name__ == 'LlamaCppProvider'
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
