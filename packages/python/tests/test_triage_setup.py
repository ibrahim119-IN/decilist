"""Native HTTP stubs; no real endpoints, completion, or model load."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import httpx
import pytest

from decilist import triage_hypothesis_v6 as v6

from decilist import triage_setup as setup


class NativeServer:
    def __init__(self, profile="qwen3_coder_non_reasoning", mutate=None):
        self.profile, self.mutate = profile, mutate
        self.alias = "An explicitly configured local alias 模型"
        self.model_path = "models/arbitrary-name-模型.gguf"
        self.template = "Actual native served template, not an inferred publisher hash"
        self.requests = []
        self.props_count = 0
    def handle(self, request):
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method,path,body,str(request.url)))
        assert path != "/completion", "setup must never request generation"
        if path == "/props":
            self.props_count += 1
            result = {"model_alias":self.alias,"build_info":v6.PINNED_BUILD,"model_path":self.model_path,
                      "is_sleeping":False,"chat_template":self.template,"default_generation_settings":{"n_ctx":16384},
                      "unrelated_password":"MUST_NOT_BE_SAVED"}
        elif path == "/health":
            result = {"status":"ok"}
        elif path == "/v1/models":
            result = {"data":[{"id":self.alias,"meta":{"n_vocab":256,"n_ctx":16384,"n_ctx_train":32768}}]}
        elif path == "/apply-template":
            assert body == {"messages":v6.MODE_PROBE_MESSAGES}
            result = {"prompt":"fixed neutral native probe\n" + v6.GENERATION_PROFILES[self.profile].prompt_suffix}
        else:
            raise AssertionError("Unexpected setup endpoint")
        if self.mutate:
            replacement = self.mutate(self,path,result)
            if isinstance(replacement,httpx.Response):
                return replacement
        return httpx.Response(200,content=json.dumps(result,ensure_ascii=False).encode("utf-8"))
    def transport(self):
        return httpx.MockTransport(self.handle)


@pytest.mark.asyncio
@pytest.mark.parametrize("profile",tuple(v6.GENERATION_PROFILES))
async def test_discovery_uses_actual_v6_profile_validation_and_exclusive_utf8_lf_write(tmp_path,profile):
    server = NativeServer(profile)
    output = tmp_path / "new.json"
    result = await setup.setup("http://127.0.0.1:8792",profile,output,transport=server.transport())
    assert result == {
        "model":server.alias,"expected_build":v6.PINNED_BUILD,"generation_profile":profile,
        "expected_chat_template_sha256":hashlib.sha256(server.template.encode("utf-8")).hexdigest(),
        "expected_model_path":server.model_path,
    }
    data = output.read_bytes()
    assert json.loads(data) == result
    assert not data.startswith(b"\xef\xbb\xbf") and b"\r" not in data and data.endswith(b"\n")
    assert "模型".encode("utf-8") in data and b"MUST_NOT_BE_SAVED" not in data
    assert set(result) == {"model","expected_build","generation_profile","expected_chat_template_sha256","expected_model_path"}
    assert sum(path=="/apply-template" for _,path,_,_ in server.requests) == 1
    assert server.requests[0][:2] == ("GET","/props")
    assert all(path in {"/props","/health","/v1/models","/apply-template"} for _,path,_,_ in server.requests)
    assert setup.V6LlamaCppProvider is v6.V6LlamaCppProvider


@pytest.mark.asyncio
async def test_profile_is_never_guessed_from_alias_or_file_name(tmp_path):
    server = NativeServer("qwen3_6_closed_thinking")
    server.alias = "anything-that-does-not-name-a-family"
    server.model_path = "models/not-a-profile-name.gguf"
    result = await setup.setup("http://[::1]:8792","qwen3_6_closed_thinking",tmp_path/"new.json",transport=server.transport())
    assert result["generation_profile"] == "qwen3_6_closed_thinking"
    assert result["model"] == server.alias


@pytest.mark.asyncio
async def test_unknown_profile_and_existing_config_fail_without_requests(tmp_path):
    server = NativeServer()
    with pytest.raises(setup.SetupError,match="explicit supported"):
        await setup.setup("http://127.0.0.1:8792","guess-it",tmp_path/"new.json",transport=server.transport())
    assert not (tmp_path/"new.json").exists() and server.requests == []
    existing = tmp_path / "existing.json"
    existing.write_bytes(b"KEEP EXISTING CONFIG EXACTLY\r\n")
    with pytest.raises(FileExistsError):
        await setup.setup("http://127.0.0.1:8792","qwen3_coder_non_reasoning",existing,transport=server.transport())
    assert existing.read_bytes() == b"KEEP EXISTING CONFIG EXACTLY\r\n" and server.requests == []


@pytest.mark.asyncio
async def test_wrong_selected_mode_fails_closed_without_auto_switch_or_completion(tmp_path):
    server = NativeServer("qwen3_coder_non_reasoning")
    output = tmp_path / "new.json"
    with pytest.raises(v6.GenerationValidationError,match="exact configured"):
        await setup.setup("http://127.0.0.1:8792","qwen3_6_closed_thinking",output,transport=server.transport())
    assert not output.exists()
    assert sum(path=="/apply-template" for _,path,_,_ in server.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change",["build","sleeping","wrong_service","context","training_context"])
async def test_wrong_runtime_or_profile_context_never_produces_config(tmp_path,change):
    def mutate(server,path,result):
        if path=="/props":
            if change=="build": result["build_info"] = "unreviewed-build"
            elif change=="sleeping": result["is_sleeping"] = True
            elif change=="wrong_service": result.clear(); result.update(service="decilist",ready=True)
            elif change=="context": result["default_generation_settings"]["n_ctx"] = 8192
        if path=="/v1/models":
            if change=="context": result["data"][0]["meta"]["n_ctx"] = 8192
            elif change=="training_context": result["data"][0]["meta"]["n_ctx_train"] = 1024
    server = NativeServer(mutate=mutate)
    output = tmp_path / "new.json"
    with pytest.raises((setup.SetupError,ValueError,RuntimeError)):
        await setup.setup("http://127.0.0.1:8792","qwen3_coder_non_reasoning",output,transport=server.transport())
    assert not output.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("field",["chat_template","model_alias","model_path","build_info"])
async def test_identity_drift_after_discovery_is_rejected_before_saving(tmp_path,field):
    def mutate(server,path,result):
        if path=="/props" and server.props_count>=2:
            result[field] += " changed"
    server = NativeServer(mutate=mutate)
    output = tmp_path / "new.json"
    with pytest.raises((ValueError,RuntimeError)):
        await setup.setup("http://127.0.0.1:8792","qwen3_coder_non_reasoning",output,transport=server.transport())
    assert not output.exists()


@pytest.mark.asyncio
async def test_identity_drift_after_native_probe_is_rejected_by_final_readiness(tmp_path):
    def mutate(server,path,result):
        if path=="/apply-template":
            server.template = "changed after native probe"
    server = NativeServer(mutate=mutate)
    output = tmp_path / "new.json"
    with pytest.raises(RuntimeError,match="identity changed"):
        await setup.setup("http://127.0.0.1:8792","qwen3_coder_non_reasoning",output,transport=server.transport())
    assert not output.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("url",["http://localhost:8792","https://example.com","http://192.168.1.2:8792","http://user:secret@127.0.0.1:8792","http://127.0.0.1:8792/path","http://127.0.0.1:8792?key=secret","file:///local"])
async def test_dns_remote_auth_and_decorated_origins_are_rejected_before_http(tmp_path,url):
    server = NativeServer()
    with pytest.raises(ValueError,match="loopback"):
        await setup.setup(url,"qwen3_coder_non_reasoning",tmp_path/"new.json",transport=server.transport())
    assert server.requests == [] and not (tmp_path/"new.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("response",[
    httpx.Response(302,headers={"Location":"http://127.0.0.1:8792/elsewhere"}),
    httpx.Response(200,content=b'{"model_alias":"one","model_alias":"two"}'),
    httpx.Response(200,content=b'{"bad":NaN}'),
    httpx.Response(200,json=[]),
])
async def test_existing_guarded_decoder_rejects_redirect_duplicate_nonfinite_and_nonobject(tmp_path,response):
    calls = []
    def handle(request):
        calls.append(str(request.url))
        return response
    with pytest.raises(RuntimeError):
        await setup.setup("http://127.0.0.1:8792","qwen3_coder_non_reasoning",tmp_path/"new.json",transport=httpx.MockTransport(handle))
    assert calls == ["http://127.0.0.1:8792/props"]
    assert not (tmp_path/"new.json").exists()


@pytest.mark.asyncio
async def test_default_http_ownership_disables_env_proxies_and_redirects(tmp_path,monkeypatch):
    original = httpx.AsyncClient
    seen = []
    def record(*args,**kwargs):
        seen.append(kwargs)
        return original(*args,**kwargs)
    monkeypatch.setenv("HTTP_PROXY","http://do-not-use.example")
    monkeypatch.setenv("HTTPS_PROXY","http://do-not-use.example")
    monkeypatch.setattr(httpx,"AsyncClient",record)
    server = NativeServer()
    await setup.setup("http://127.0.0.1:8792","qwen3_coder_non_reasoning",tmp_path/"new.json",transport=server.transport())
    assert len(seen)==2 and all(row["trust_env"] is False and row["follow_redirects"] is False for row in seen)


@pytest.mark.asyncio
async def test_config_appearing_during_verification_is_preserved_exclusively(tmp_path):
    output = tmp_path / "racing.json"
    def mutate(server,path,result):
        if path=="/apply-template":
            output.write_bytes(b"OTHER OWNER CONFIG")
    server = NativeServer(mutate=mutate)
    with pytest.raises(FileExistsError):
        await setup.setup("http://127.0.0.1:8792","qwen3_coder_non_reasoning",output,transport=server.transport())
    assert output.read_bytes() == b"OTHER OWNER CONFIG"


@pytest.mark.asyncio
async def test_parent_identity_change_is_rejected_without_creating_output(tmp_path,monkeypatch):
    original = setup._parent_snapshot
    calls = 0
    def changed(path):
        nonlocal calls
        calls += 1
        snapshot = original(path)
        return snapshot if calls==1 else tuple((parent,device,inode+1) for parent,device,inode in snapshot)
    monkeypatch.setattr(setup,"_parent_snapshot",changed)
    server = NativeServer()
    with pytest.raises(setup.SetupError,match="parent changed"):
        await setup.setup("http://127.0.0.1:8792","qwen3_coder_non_reasoning",tmp_path/"new.json",transport=server.transport())
    assert not (tmp_path/"new.json").exists()


@pytest.mark.asyncio
async def test_reparse_parent_is_rejected_before_discovery(tmp_path,monkeypatch):
    original = os.lstat
    def reparse(path,*args,**kwargs):
        observed = original(path,*args,**kwargs)
        if Path(path)==tmp_path:
            return SimpleNamespace(st_mode=stat.S_IFDIR,st_file_attributes=0x400,st_dev=observed.st_dev,st_ino=observed.st_ino)
        return observed
    monkeypatch.setattr(os,"lstat",reparse)
    server = NativeServer()
    with pytest.raises(setup.SetupError,match="reparse"):
        await setup.setup("http://127.0.0.1:8792","qwen3_coder_non_reasoning",tmp_path/"new.json",transport=server.transport())
    assert server.requests == []


@pytest.mark.asyncio
async def test_actual_parent_symlink_is_rejected_when_platform_supports_it(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(real,target_is_directory=True)
    except OSError:
        pytest.skip("Platform does not allow creating a test directory symlink")
    server = NativeServer()
    with pytest.raises(setup.SetupError,match="links"):
        await setup.setup("http://127.0.0.1:8792","qwen3_coder_non_reasoning",link/"new.json",transport=server.transport())
    assert not (real/"new.json").exists() and server.requests == []


@pytest.mark.asyncio
async def test_existing_leaf_symlink_preserves_its_target_without_requests(tmp_path):
    target = tmp_path / "owner.json"
    target.write_bytes(b"OWNER CONFIG")
    link = tmp_path / "new.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Platform does not allow creating a test file symlink")
    server = NativeServer()
    with pytest.raises(FileExistsError):
        await setup.setup("http://127.0.0.1:8792","qwen3_coder_non_reasoning",link,transport=server.transport())
    assert target.read_bytes() == b"OWNER CONFIG" and link.is_symlink() and server.requests == []


@pytest.mark.parametrize("path",["../new.json","C:new.json","new.json:stream","NUL.json","\\\\server\\share\\new.json","\\\\?\\C:\\new.json"])
def test_ambiguous_network_device_and_traversal_outputs_do_not_touch_filesystem(path,monkeypatch):
    def forbidden(*args,**kwargs):
        pytest.fail("rejected output names must not touch a network or filesystem path")
    monkeypatch.setattr(os,"lstat",forbidden)
    with pytest.raises(setup.SetupError):
        setup._new_output(path)


@pytest.mark.asyncio
async def test_missing_parent_is_not_created_and_never_contacts_server(tmp_path):
    server = NativeServer()
    output = tmp_path / "missing" / "new.json"
    with pytest.raises(setup.SetupError,match="parent must already exist"):
        await setup.setup("http://127.0.0.1:8792","qwen3_coder_non_reasoning",output,transport=server.transport())
    assert not output.parent.exists() and server.requests == []


def test_cli_has_one_explicit_profile_and_no_endpoint_or_import_side_effects(tmp_path,monkeypatch,capsys):
    calls = []
    async def fake(url,profile,output):
        calls.append((url,profile,output))
        return {"model":"not printed"}
    monkeypatch.setattr(setup,"setup",fake)
    output = tmp_path / "new.json"
    assert setup.main(["--url","http://127.0.0.1:8792","--profile","qwen3_coder_non_reasoning","--output",str(output)]) == 0
    assert calls == [("http://127.0.0.1:8792","qwen3_coder_non_reasoning",output)]
    assert "No generation" in capsys.readouterr().out
    assert not output.exists()


def test_cli_wrong_mode_selection_and_dns_url_fail_before_setup(tmp_path,monkeypatch):
    async def forbidden(*args,**kwargs):
        pytest.fail("invalid arguments cannot begin setup")
    monkeypatch.setattr(setup,"setup",forbidden)
    for arguments in [
        ["--profile","auto","--output",str(tmp_path/"new.json")],
        ["--url","http://localhost:8792","--profile","qwen3_coder_non_reasoning","--output",str(tmp_path/"new.json")],
    ]:
        with pytest.raises(SystemExit) as caught:
            setup.main(arguments)
        assert caught.value.code == 2


def test_import_does_not_create_http_clients_or_begin_discovery(monkeypatch):
    import importlib
    def forbidden(*args,**kwargs):
        pytest.fail("importing setup must not construct HTTP clients or contact a runtime")
    monkeypatch.setattr(httpx,"AsyncClient",forbidden)
    reloaded = importlib.reload(setup)
    assert reloaded.V6LlamaCppProvider is v6.V6LlamaCppProvider
