"""Application decisions go through the same SDK engine; app owns the next step."""
import json
from html.parser import HTMLParser

import httpx
import pytest

from decilist import Decilist, FunctionProvider, UniformProvider
from decilist.demo import APP_INSTRUCTIONS, APP_OPTIONS
from decilist.server import create_app


def elements_by_id(html):
    class Elements(HTMLParser):
        def __init__(self):
            super().__init__()
            self.elements = {}

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if "id" in values:
                self.elements[values["id"]] = {"tag": tag, **values}

    parser = Elements()
    parser.feed(html)
    return parser.elements


@pytest.fixture
async def ui_client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://localhost:8765") as client:
        yield client


@pytest.mark.parametrize("request_text,expected", [
    ("Prepare a short summary of the project update.", "proceed"),
    ("Prepare something from this document.", "clarify"),
    ("Maybe a summary or a checklist, I'm not sure.", "review"),
    ("Email a summary to the team.", "review"),
    ("عايز ملخص للمستند", "proceed"),
    ("ابعت ملخص للفريق", "review"),
])
async def test_baseline_app_flow(ui_client, request_text, expected):
    response = await ui_client.post("/v1/demo/request", json={"request": request_text})
    assert response.status_code == 200
    result = response.json()
    assert result["outcome"] == expected
    assert result["decision"]["model"] == "rules-v1"
    assert [x["outcome"] for x in result["decision"]["attempts"]] == ["low_confidence", "accepted"]
    assert (result["draft"] is not None) == (expected == "proceed")


async def test_single_clarification_reuses_request_and_known_context(ui_client):
    body = {"request": "Prepare something from this document."}
    first = (await ui_client.post("/v1/demo/request", json=body)).json()
    assert first["outcome"] == "clarify"
    second = (await ui_client.post("/v1/demo/request", json={**body, "clarification": "A short outline"})).json()
    assert second["outcome"] == "proceed"
    assert second["draft"] == {**body, "clarification": "A short outline", "source": "Project update", "destination": "Private drafts"}
    uncertain = (await ui_client.post("/v1/demo/request", json={**body, "clarification": "Whatever seems right"})).json()
    assert uncertain["outcome"] == "review"


async def test_model_path_receives_request_and_app_context_and_app_owns_copy():
    seen = []
    def infer(question):
        seen.append(question)
        return {"proceed": 0.05, "clarify": 0.9, "review": 0.05}
    engine = Decilist([FunctionProvider(infer, name="test-model", model="test-model-v1")])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine, provider="liquid")), base_url="http://localhost") as client:
        first = (await client.post("/v1/demo/request", json={"request": "A draft please"})).json()
        second = (await client.post("/v1/demo/request", json={"request": "A draft please", "clarification": "A summary"})).json()
    assert first["outcome"] == "clarify"
    assert first["message"] == "What would you like from the project update: a summary, outline, or checklist?"
    assert second["outcome"] == "review"
    assert len(seen) == 2
    assert seen[0].instructions == APP_INSTRUCTIONS
    assert seen[0].options == APP_OPTIONS
    assert json.loads(seen[1].state) == {"app":"request-desk-v1", "current_document":"Project update", "destination":"Private drafts", "request":"A draft please", "clarification":"A summary"}


async def test_app_refuses_consequential_action_even_when_provider_says_proceed():
    engine = Decilist([FunctionProvider(lambda _: {"proceed": 1., "clarify": 0., "review": 0.})])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)), base_url="http://localhost") as client:
        result = (await client.post("/v1/demo/request", json={"request": "Publish this summary"})).json()
    assert result["decision"]["value"] == "proceed"
    assert result["outcome"] == "review"
    assert result["draft"] is None


@pytest.mark.parametrize("engine,outcome", [
    (Decilist([UniformProvider()]), "review"),
    (Decilist([FunctionProvider(lambda _: {})]), "unavailable"),
])
async def test_abstention_and_invalid_output_have_intelligible_state(engine, outcome):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)), base_url="http://localhost") as client:
        result = (await client.post("/v1/demo/request", json={"request":"Prepare a summary"})).json()
    assert result["outcome"] == outcome
    assert result["decision"]["status"] == "abstained"
    assert result["draft"] is None


@pytest.mark.parametrize("body", [{"request":" "}, {"request":"x"*4001}, {"request":"A summary", "clarification":"x"*1001}, {"request":"A summary", "provider":"remote"}])
async def test_composer_input_validation(ui_client, body):
    assert (await ui_client.post("/v1/demo/request", json=body)).status_code == 422


async def test_packaged_workspace_and_demo_assets(ui_client):
    page = await ui_client.get("/")
    assert "Your question" in page.text
    controls = elements_by_id(page.text)
    assert controls["advanced"]["tag"] == "details"
    assert controls["save-test"]["tag"] == "details"
    assert controls["copy-sdk"]["tag"] == "button"
    assert controls["snippet-language"]["tag"] == "select"
    assert "TypeScript" in page.text
    assert "hidden" in controls["levels-field"]
    assert controls["result-heading"]["tabindex"] == "-1"
    for path in ("/app.js", "/demo.js", "/style.css"):
        response = await ui_client.get(path)
        assert response.status_code == 200
        assert "script-src 'self'" in response.headers["content-security-policy"]
    demo = elements_by_id((await ui_client.get("/demo")).text)
    assert demo["request"]["dir"] == "auto"
    assert demo["clarification"]["dir"] == "auto"
    assert "threshold" not in demo
    assert "json" not in demo
    assert "kind" not in demo
