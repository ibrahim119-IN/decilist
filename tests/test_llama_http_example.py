import asyncio
import json
import sys
from types import SimpleNamespace

import httpx
import pytest

from decilist.contracts import labels_for
from examples import llama_http as example


CONFIG = {"model":"stub-local", "expected_build":"stub-build", "expected_model_path":"models/stub.gguf"}


class StubProvider:
    name, locality = "llama-cpp-local", "local"
    def __init__(self, **kwargs):
        self.kwargs, self.model = kwargs, kwargs["model"]
        self.ready_calls, self.close_calls, self.infer_calls = 0, 0, 0
        self.entered = asyncio.Event()
        self.release = None
        self.error = None
    async def ready(self):
        self.ready_calls += 1
        self.entered.set()
        if self.release:
            await self.release.wait()
        if self.error:
            raise self.error
        return {"model":self.model,"build":"stub-build"}
    async def aclose(self):
        self.close_calls += 1
    async def infer(self, question):
        self.infer_calls += 1
        return {key:float(index == 0) for index,key in enumerate(labels_for(question))}


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(example,"LlamaCppProvider",StubProvider)
    app = example.create_llama_app(CONFIG)
    return app, app.state.llama_provider


async def test_health_and_decisions_wait_for_ready_and_shutdown_closes_only_owned_provider(provider):
    app, model = provider
    assert model.ready_calls == model.close_calls == 0
    model.release = asyncio.Event()
    lifespan = app.router.lifespan_context(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://127.0.0.1:8793") as client:
        assert (await client.get("/health")).status_code == 503
        startup = asyncio.create_task(lifespan.__aenter__())
        await asyncio.wait_for(model.entered.wait(),1)
        assert (await client.get("/health")).status_code == 503
        body = {"question":{"kind":"choose","state":"Evidence","instructions":"Choose","options":{"first":"First","second":"Second"}}}
        assert (await client.post("/v1/decisions",json=body)).status_code == 503
        assert model.infer_calls == 0
        model.release.set()
        await startup
        health = (await client.get("/health")).json()
        assert health["ready"] is True and health["provider"] == "llama-cpp"
        assert health["providers"][0]["model"] == "stub-local"
        answer = await client.post("/v1/decisions",json=body)
        assert answer.status_code == 200 and answer.json()["value"] == "first"
        assert (await client.get("/")).status_code == 404
        assert model.kwargs == {**CONFIG,"base_url":"http://127.0.0.1:8792"}
        await lifespan.__aexit__(None,None,None)
        assert model.ready_calls == model.close_calls == 1
        assert (await client.get("/health")).status_code == 503


async def test_failed_readiness_prevents_available_health_and_still_closes_client(provider):
    app, model = provider
    model.error = RuntimeError("stub identity mismatch")
    with pytest.raises(RuntimeError,match="identity mismatch"):
        async with app.router.lifespan_context(app):
            pytest.fail("startup cannot finish after failed identity verification")
    assert model.ready_calls == model.close_calls == 1
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://127.0.0.1:8793") as client:
        assert (await client.get("/health")).status_code == 503


async def test_shutdown_closes_provider_even_if_application_lifespan_body_fails(provider):
    app, model = provider
    with pytest.raises(RuntimeError,match="application failure"):
        async with app.router.lifespan_context(app):
            raise RuntimeError("application failure")
    assert model.close_calls == 1 and app.state.provider_ready is False


@pytest.mark.parametrize("url", ["http://localhost:8792","https://example.com","http://192.168.1.4:8792","file:///model","http://127.0.0.1/path","http://user:secret@127.0.0.1:8792","http://127.0.0.1:8792?token=secret","http://127.0.0.1:8792#fragment","http://127.0.0.1:0","http://127.0.0.1:65536","http://[::ffff:127.0.0.1]:8792"])
def test_invalid_model_origin_is_refused_before_provider_construction(monkeypatch,url):
    def forbidden(**kwargs):
        pytest.fail("invalid origin must not create a provider or a connection")
    monkeypatch.setattr(example,"LlamaCppProvider",forbidden)
    with pytest.raises(ValueError,match="numeric loopback"):
        example.create_llama_app(CONFIG,model_url=url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8792","http://127.2.3.4:8792/","http://[::1]:8792"])
def test_numeric_loopback_model_origins_are_supported(url):
    assert example.model_origin(url) == url.rstrip("/")


def test_cli_uses_fixed_loopback_host_and_lifespan_without_launching_real_service(tmp_path,monkeypatch):
    config = tmp_path / "model.json"
    config.write_text(json.dumps(CONFIG),encoding="utf-8")
    monkeypatch.setattr(example,"LlamaCppProvider",StubProvider)
    observed = {}
    def run(app,**kwargs):
        observed.update(kwargs)
        async def check():
            async with app.router.lifespan_context(app):
                assert app.state.provider_ready
            assert app.state.llama_provider.close_calls == 1
        asyncio.run(check())
    monkeypatch.setitem(sys.modules,"uvicorn",SimpleNamespace(run=run))
    assert example.main(["--model-config",str(config),"--port","9876"]) == 0
    assert observed == {"host":"127.0.0.1","port":9876,"proxy_headers":False,"lifespan":"on"}


@pytest.mark.parametrize("port", ["0","-1","65536","not-a-port"])
def test_invalid_cli_ports_never_start_service(tmp_path,monkeypatch,port):
    def forbidden(*args,**kwargs):
        pytest.fail("invalid CLI must never launch a service")
    monkeypatch.setitem(sys.modules,"uvicorn",SimpleNamespace(run=forbidden))
    with pytest.raises(SystemExit) as caught:
        example.main(["--model-config",str(tmp_path/"model.json"),"--port",port])
    assert caught.value.code == 2


@pytest.mark.parametrize("data", ['{"model":"one","model":"two","expected_build":"build"}', '{"model":"one"}', '{"model":"one","expected_build":"build","api_key":"private"}', '{"model":"one","expected_build":3}', 'not JSON'])
def test_invalid_config_is_clear_and_does_not_start_service(tmp_path,monkeypatch,capsys,data):
    config = tmp_path / "model.json"
    config.write_text(data,encoding="utf-8")
    def forbidden(*args,**kwargs):
        pytest.fail("invalid config must never create a provider or launch a service")
    monkeypatch.setattr(example,"LlamaCppProvider",forbidden)
    monkeypatch.setitem(sys.modules,"uvicorn",SimpleNamespace(run=forbidden))
    assert example.main(["--model-config",str(config)]) == 2
    assert "Local model HTTP example:" in capsys.readouterr().err


def test_cli_cannot_expose_remote_bind_host(tmp_path):
    with pytest.raises(SystemExit) as caught:
        example.main(["--model-config",str(tmp_path/"model.json"),"--host","0.0.0.0"])
    assert caught.value.code == 2
