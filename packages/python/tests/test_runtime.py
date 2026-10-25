import asyncio
import json
import socket
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from decilist import Client, Decilist, FunctionProvider, Policy
from decilist.runtime import RuntimeCompatibilityError, RuntimeConnectionError, main, validate_runtime_health
from decilist.server import create_app

HEALTH = {"service": "decilist", "schema_version": "1", "runtime_version": "0.1.0a1", "ready": True, "status": "ok", "provider": "demo"}


@contextmanager
def endpoint(payload):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("change", [{"service": "other"}, {"schema_version": "2"}, {"runtime_version": ""}])
def test_health_rejects_other_services_and_incompatible_versions(change):
    with pytest.raises(RuntimeCompatibilityError):
        validate_runtime_health({**HEALTH, **change})


def test_start_reuses_only_matching_ready_provider_without_stopping_it(capsys):
    with endpoint(HEALTH) as port:
        assert main(["runtime", "start", "--provider", "demo", "--port", str(port)]) == 0
        assert "Reusing ready" in capsys.readouterr().out
        assert httpx.get(f"http://127.0.0.1:{port}/health", trust_env=False).status_code == 200
        assert main(["runtime", "start", "--provider", "liquid", "--port", str(port), "--allow-model-code"]) == 1
        assert "different Decilist provider" in capsys.readouterr().err
        assert httpx.get(f"http://127.0.0.1:{port}/health", trust_env=False).status_code == 200


def test_start_does_not_take_over_an_unrelated_service(capsys):
    with endpoint({"service": "unrelated"}) as port:
        assert main(["runtime", "start", "--provider", "demo", "--port", str(port)]) == 1
        assert "No process was stopped" in capsys.readouterr().err
        assert httpx.get(f"http://127.0.0.1:{port}/health", trust_env=False).json() == {"service": "unrelated"}


def test_run_waits_for_health_propagates_application_exit_and_stops_owned_server():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    application = "import json,os,urllib.request; h=json.load(urllib.request.urlopen(os.environ['DECILIST_URL']+'/health')); assert h['ready'] and h['service']=='decilist'; raise SystemExit(7)"
    assert main(["runtime", "run", "--provider", "demo", "--port", str(port), "--", sys.executable, "-c", application]) == 7
    # Windows may time out rather than immediately refuse a closed loopback port.
    with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)):
        httpx.get(f"http://127.0.0.1:{port}/health", timeout=.2, trust_env=False)


def test_run_reuses_runtime_but_does_not_stop_it():
    with endpoint(HEALTH) as port:
        assert main(["runtime", "run", "--provider", "demo", "--port", str(port), "--", sys.executable, "-c", "raise SystemExit(3)"]) == 3
        assert httpx.get(f"http://127.0.0.1:{port}/health", trust_env=False).status_code == 200


def test_no_download_permission_and_no_non_loopback_start(capsys):
    assert main(["runtime", "setup", "liquid"]) == 1
    assert "--allow-download --allow-model-code" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        main(["runtime", "start", "--provider", "demo", "--host", "0.0.0.0"])
    assert exc.value.code == 2


def test_imports_do_not_load_vendor_dependencies_or_start_runtime():
    result = subprocess.run([sys.executable, "-c", "import sys; import decilist, decilist.runtime, decilist.__main__; assert 'torch' not in sys.modules; assert 'transformers' not in sys.modules"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


async def test_installed_server_health_and_direct_calls_have_same_contract():
    engine = Decilist([FunctionProvider(lambda q: {"a": 1, "b": 0})])
    app = create_app(engine, include_ui=False)
    async with Client() as client:
        await client._http.aclose()
        client._http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=client.base_url)
        health = await client.ready()
        assert health["service"] == "decilist" and health["schema_version"] == "1"
        result = await client.choose("Evidence", "Choose", {"a": "A", "b": "B"})
        assert result.status == "accepted" and result.value == "a"


async def test_client_waits_for_readiness_and_refuses_incompatible_runtime():
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={**HEALTH, "ready": len(calls) > 1})
    async with Client() as client:
        await client._http.aclose()
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=client.base_url)
        assert (await client.ready(wait_seconds=1))["ready"] is True
        assert len(calls) == 2
    def incompatible(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={**HEALTH, "schema_version": "2"})
    async with Client() as client:
        await client._http.aclose()
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(incompatible), base_url=client.base_url)
        with pytest.raises(RuntimeCompatibilityError):
            await client.choose("Evidence", "Choose", {"a": "A", "b": "B"})
        assert "/v1/decisions" not in calls


async def test_client_policy_is_configured_once_and_can_be_overridden_per_call():
    class Slow:
        name, model, locality = "slow", "test", "local"
        async def infer(self, question):
            await asyncio.sleep(.02)
            return {"false": 0, "true": 1}
    app = create_app(Decilist([Slow()]), include_ui=False)
    async with Client(policy=Policy(timeout_ms=1)) as client:
        await client._http.aclose()
        client._http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=client.base_url)
        result = await client.predict("Ready", "Ready?")
        assert result.status == "abstained" and result.attempts[0].outcome == "timeout"
        assert (await client.predict("Ready", "Ready?", policy=Policy(timeout_ms=100))).value is True


async def test_unavailable_runtime_has_actionable_error():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        async with Client(f"http://127.0.0.1:{port}") as client:
            with pytest.raises(RuntimeConnectionError, match="runtime start"):
                await client.ready()


@pytest.mark.parametrize("wait_seconds", [float("nan"), float("inf"), -1])
async def test_client_rejects_unbounded_readiness_wait(wait_seconds):
    async with Client() as client:
        with pytest.raises(ValueError, match="finite and nonnegative"):
            await client.ready(wait_seconds=wait_seconds)


@pytest.mark.parametrize("url", ["file:///tmp/a", "https://user:secret@host", "http://host/prefix", "http://host/?secret=value"])
def test_client_rejects_ambiguous_or_credential_urls(url):
    with pytest.raises(ValueError):
        Client(url)
