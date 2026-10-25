import httpx
import pytest

from decilist import Choose, Client, Decilist, FunctionProvider, Policy
from decilist.contracts import labels_for
from decilist.server import create_app


@pytest.mark.parametrize("given, expected", [
    (["2", "1"], ["1", "2"]),
    (["01", "4294967295", "-1", "١", "4294967294", "0", "00", "2"], ["0", "2", "4294967294", "01", "4294967295", "-1", "١", "00"]),
    (["4294967294", "4294967293", "4294967296"], ["4294967293", "4294967294", "4294967296"]),
    (["٠", "１", "0", "01"], ["0", "٠", "１", "01"]),
    (["__proto__", "constructor", "first", "second"], ["__proto__", "constructor", "first", "second"]),
    (["1.0", "1e0", "+1", "-0"], ["1.0", "1e0", "+1", "-0"]),
])
def test_choice_keys_follow_json_array_index_order_without_mutating_caller(given, expected):
    original = {key: f"Description for {key}" for key in given}
    question = Choose(state="Evidence", instructions="Choose", options=original)
    assert list(original) == given
    assert list(question.options) == expected
    assert labels_for(question) == expected
    assert list(question.model_dump()["options"]) == expected


async def test_copied_question_is_canonical_for_provider_ties_and_http_validation():
    seen = []
    def infer(question):
        seen.append(list(question.options))
        # Provider insertion order cannot determine a tied choice.
        return {"2": .5, "1": .5}
    engine = Decilist([FunctionProvider(infer)], Policy(min_confidence=0))
    original = Choose(state="Evidence", instructions="Choose", options={"first":"First", "second":"Second"})
    copied = original.model_copy(update={"options": {"2":"Two", "1":"One"}})
    assert labels_for(copied) == ["1", "2"]
    direct = await engine.decide(copied)
    assert direct.value == "1"
    assert list(direct.probabilities) == ["1", "2"]
    app = create_app(engine, include_ui=False)
    async with Client(policy=Policy(min_confidence=0)) as client:
        await client._http.aclose()
        client._http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=client.base_url)
        remote = await client.decide(copied)
    assert remote.value == "1"
    assert seen == [["1", "2"], ["1", "2"]]
    assert list(copied.options) == ["2", "1"]
    assert list(original.options) == ["first", "second"]
