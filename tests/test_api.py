import httpx
import pytest
from apps.api.main import create_app
from decilist import Decilist, FunctionProvider

BODY = {"question":{"kind":"choose","state":"{\"complete\":true,\"valid\":true}","instructions":"Choose", "options":{"proceed":"ready","retry":"retry","review":"review"}}}


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://localhost:8765") as client:
        yield client


async def test_real_http_flow(client):
    health = (await client.get("/health")).json()
    assert health["local_only"] is True
    response = await client.post("/v1/decisions", json=BODY)
    assert response.status_code == 200
    result = response.json()
    assert result["value"] == "proceed"
    assert [x["outcome"] for x in result["attempts"]] == ["low_confidence","accepted"]
    assert response.headers["cache-control"] == "no-store"


async def test_invalid_and_oversized_input(client):
    assert (await client.post("/v1/decisions", json={"question":{}})).status_code == 422
    assert (await client.post("/v1/decisions", content="x" * 262145, headers={"Content-Type":"application/json"})).status_code == 413
    assert (await client.post("/v1/decisions", content="{}", headers={"Content-Type":"text/plain"})).status_code == 415


async def test_browser_and_host_boundaries(client):
    assert (await client.post("/v1/decisions", json=BODY, headers={"Origin":"https://untrusted.example"})).status_code == 403
    assert (await client.get("/health", headers={"Host":"attacker.example"})).status_code == 400
    assert (await client.post("/v1/decisions", json=BODY, headers={"Origin":"http://localhost:8765"})).status_code == 200


async def test_request_cannot_downgrade_server_policy():
    def fail(q):
        pytest.fail("remote provider must not run")
    cloud = FunctionProvider(fail)
    cloud.locality = "remote"
    app = create_app(Decilist([cloud]), local_only=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://localhost") as client:
        result = (await client.post("/v1/decisions",json={**BODY,"policy":{"local_only":False}})).json()
    assert result["status"] == "abstained"
    assert result["attempts"][0]["outcome"] == "blocked"


async def test_playground_assets_and_openapi(client):
    assert "decilist" in (await client.get("/")).text
    assert (await client.get("/app.js")).status_code == 200
    assert (await client.get("/style.css")).status_code == 200
    assert "/v1/decisions" in (await client.get("/openapi.json")).json()["paths"]
